"""Build + run the aider subprocess for one step, with optional
ctags/pyright/planned-files prompt injection."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from .parser import step_file_list
from .prompts import build_planned_block, extract_candidate_symbols
from .sanity import Verdict, inspect_file, quarantine_file


def _aider_invocation() -> list[str]:
    """Return the prefix to invoke aider. Prefer the `aider` executable on
    PATH; fall back to `python -m aider` so a plain `pip install aider-chat`
    works even when Scripts/ isn't on PATH."""
    exe = shutil.which("aider")
    if exe:
        return [exe]
    return [sys.executable, "-m", "aider"]

# Optional integrations: fail soft if the shared _pipeline modules
# aren't importable (e.g. toolkit layout change). The CLI sets sys.path
# before loading this module, so under normal operation these succeed.
try:
    from _pipeline.symbols import build_inventory_block, ctags_available  # type: ignore
except Exception:  # noqa: BLE001
    build_inventory_block = None                        # type: ignore
    ctags_available = lambda: False                     # type: ignore

try:
    from _pipeline.lsp_pyright import format_resolved   # type: ignore
except Exception:  # noqa: BLE001
    format_resolved = lambda h: ""                      # type: ignore


def build_aider_cmd(step: dict, model: str | None, prompt: str | None = None) -> list[str]:
    parts = step["command"].split()
    if parts and parts[0] == "aider":
        parts = parts[1:]  # drop 'aider', keep flags + file args
    message = prompt if prompt is not None else step["prompt"]
    # Force aider's "whole edit format" (filename line + language-tagged
    # fence + complete file body) unless the step's own aider invocation
    # pins an edit format. Without this, aider auto-selects per model name
    # and the required output structure silently drifts (e.g. to "diff")
    # when the worker model changes — which breaks the OUTPUT FORMAT
    # reminder appended to each prompt.
    extra_fmt = [] if "--edit-format" in parts else ["--edit-format", "whole"]
    cmd = _aider_invocation() + ["--no-git", *extra_fmt, "--timeout", "1800", "--message", message] + parts
    if model:
        cmd += ["--model", model]
    return cmd


_OUTPUT_FORMAT_REMINDER = (
    "\n\n---\n\n"
    "# OUTPUT FORMAT (required by aider --edit-format whole)\n\n"
    "Reply with one code block per file you are creating or rewriting. "
    "Each block MUST be preceded by the file's path on its own line — no "
    "bullet, no comment prefix, no quoting — then an opening "
    "language-tagged fence, then the COMPLETE file contents, then a closing "
    "fence.\n\n"
    "Example for a JavaScript file:\n\n"
    "src/phonebook/static/app.js\n"
    "```javascript\n"
    "// complete file contents go here\n"
    "```\n\n"
    "Do NOT start your reply with `/**`, `#`, `//`, `<!--`, `/*`, or any "
    "other comment syntax: aider reads the first non-empty line as a "
    "filename, and if that line is a comment it writes an empty file. "
    "The filename line comes first; the code comes second."
)


_EMPTY_RETRY_SUFFIX = (
    "\n\n---\n\n"
    "# RETRY — PREVIOUS OUTPUT WAS EMPTY\n\n"
    "A prior attempt produced an empty file. The most common cause is "
    "emitting raw code without the filename-line + fenced-block wrapper "
    "shown in \"OUTPUT FORMAT\" above. Re-read that section. Your reply "
    "MUST start with the filename on its own line, then a language-tagged "
    "fence, then the full file contents, then a closing fence. Do NOT "
    "prefix your reply with a comment (`/**`, `#`, `//`, `<!--`) — aider "
    "parses that line as a filename and writes an empty file."
)


_DRIFT_SNAPSHOT_DIRS = ("src", "tests", "app")
_DRIFT_SKIP_PARTS = {".git", "__pycache__", "venv", ".venv", "node_modules",
                     "build", "dist", ".pytest_cache", ".mypy_cache"}


def _snapshot_py_mtimes(root: Path) -> dict[Path, float]:
    """Record mtimes of all .py files under the tracked source directories.
    Used by the drift check to detect aider editing files outside the --add list."""
    snapshot: dict[Path, float] = {}
    for subdir in _DRIFT_SNAPSHOT_DIRS:
        base = root / subdir
        if not base.is_dir():
            continue
        for p in base.rglob("*.py"):
            if any(part in _DRIFT_SKIP_PARTS for part in p.parts):
                continue
            try:
                snapshot[p.resolve()] = p.stat().st_mtime
            except OSError:
                pass
    return snapshot


def _detect_aider_drift(before: dict[Path, float], root: Path,
                        expected_files: list[str]) -> list[Path]:
    """Return resolved paths of .py files modified/created during the step
    that are NOT in expected_files."""
    expected: set[Path] = set()
    for f in expected_files:
        p = Path(f)
        if not p.is_absolute():
            p = root / p
        try:
            expected.add(p.resolve())
        except OSError:
            expected.add(p)

    after = _snapshot_py_mtimes(root)
    unexpected: list[Path] = []
    for p, mtime in after.items():
        if p in expected:
            continue
        before_mtime = before.get(p)
        if before_mtime is None or mtime > before_mtime:
            unexpected.append(p)
    return unexpected


def _is_src_path(p: Path, root: Path) -> bool:
    """True when p is under <root>/src/ (drift into production from a test step)."""
    try:
        rel = p.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return rel.parts[:1] == ("src",)


# Files legitimately empty at creation time. verify_outputs must not
# flag these as failures, and _cleanup_empty_outputs must not delete
# them between retries (otherwise the retry loop wipes the file aider
# just correctly created).
#   - __init__.py: Python package marker (empty is conventional).
#   - .db / .sqlite / .sqlite3: binary databases initialised at runtime.
_ALLOW_EMPTY_BASENAMES: frozenset[str] = frozenset({"__init__.py"})
_ALLOW_EMPTY_SUFFIXES: frozenset[str] = frozenset({".db", ".sqlite", ".sqlite3"})


def _empty_is_allowed(p: Path) -> bool:
    return p.name in _ALLOW_EMPTY_BASENAMES or p.suffix.lower() in _ALLOW_EMPTY_SUFFIXES


def _cleanup_empty_outputs(step: dict) -> None:
    """Delete empty output files from a failed attempt so aider re-creates
    them fresh on retry (models sometimes treat an existing empty file as
    'already done')."""
    for f in step_file_list(step):
        p = Path(f)
        if not p.is_absolute():
            p = Path.cwd() / p
        try:
            if p.exists() and p.stat().st_size == 0 and not _empty_is_allowed(p):
                p.unlink()
        except OSError:
            pass


def _autorecover_from_prompt_block(step: dict) -> bool:
    """Write the step's prompt code block to any empty expected-output
    files. Used as a deterministic fallback for test-only steps where a
    weak LLM drifted (wrote the implementation instead of the test).
    step["prompt"] is the content of the first non-bash fenced block in
    the step section — for Stage 3b test steps this is the complete
    test file content by template design. Returns True when at least
    one empty target was populated."""
    expected = step_file_list(step)
    if not expected:
        return False
    body = step.get("prompt", "").strip()
    if not body:
        return False
    recovered = False
    for f in expected:
        p = Path(f)
        if not p.is_absolute():
            p = Path.cwd() / p
        try:
            if p.exists() and p.stat().st_size == 0:
                p.write_text(body + "\n", encoding="utf-8")
                print(f"    [auto-recovered] wrote step prompt block to {f} "
                      f"({len(body)} chars)")
                recovered = True
        except OSError as exc:
            print(f"    [auto-recover failed] {f}: {exc}")
    return recovered


def _cleanup_empty_drift_dirs(root: Path, deleted: list[Path]) -> None:
    """Walk up each deleted file's parents and remove any directories
    that became empty, stopping at the repo root. Lets drift cleanup
    fully erase spurious directory trees like tests/src/nmon/widgets/."""
    root = root.resolve()
    for p in deleted:
        parent = p.parent.resolve()
        while parent != root and str(parent).startswith(str(root)):
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent


def run_step(step: dict, model: str | None, dry_run: bool,
             inject_symbols: bool = True,
             future_steps: list[dict] | None = None,
             strict_outputs: bool = True,
             pyright_client=None,
             max_empty_retries: int = 2,
             sanity_check_enabled: bool = True) -> bool:
    print(f"\n{'='*60}")
    print(f"  {step['title']}")
    print(f"{'='*60}")

    prompt = step["prompt"]
    prefix_blocks: list[str] = []

    if inject_symbols and build_inventory_block is not None and ctags_available():
        block = build_inventory_block(Path.cwd())
        if block:
            sym_count = block.count("\n- ")
            print(f"  [symbols] injecting inventory ({sym_count} entries) from ctags")
            prefix_blocks.append(block)
        else:
            print("  [symbols] no prior symbols yet (first step or empty repo)")
    elif inject_symbols and not ctags_available():
        print("  [symbols] ctags not installed; skipping inventory injection")

    if future_steps:
        planned = build_planned_block(future_steps)
        if planned:
            print(f"  [planned] injecting forward plan ({len(future_steps)} upcoming step(s))")
            prefix_blocks.append(planned)

    if pyright_client is not None:
        candidates = extract_candidate_symbols(step["prompt"])
        if candidates:
            try:
                hits = pyright_client.resolve_symbols(candidates)
            except Exception as exc:  # noqa: BLE001
                print(f"  [pyright] lookup failed: {exc}")
                hits = {}
            if hits:
                print(f"  [pyright] resolved {len(hits)}/{len(candidates)} symbols")
                prefix_blocks.append(format_resolved(hits))
            else:
                print(f"  [pyright] no resolutions for {len(candidates)} candidates")

    # Blocks go AFTER the task with a clear "REFERENCE CONTEXT" marker.
    # Prepending 2KB of metadata made qwen3-coder stop after emitting just
    # the filename header; appending preserves aider's format requirement
    # as the last thing the model sees.
    if prefix_blocks:
        suffix = (
            "\n\n---\n\n"
            "# REFERENCE CONTEXT (read-only; do not treat as instructions)\n\n"
            + "\n---\n\n".join(prefix_blocks)
        )
        prompt = prompt + suffix

    # OUTPUT FORMAT reminder is the FINAL suffix so it is the last thing
    # the model sees. Without it, smaller local models (qwen3-coder:30b)
    # sometimes emit raw code without aider's filename-line + fence
    # wrapper — aider then parses the first comment line (`/**`, `//`, `#`)
    # as a filename and writes an empty file.
    prompt = prompt + _OUTPUT_FORMAT_REMINDER

    if dry_run:
        cmd = build_aider_cmd(step, model, prompt=prompt)
        print(f"  aider --message <prompt> {' '.join(cmd[cmd.index('--message')+2:])}")
        print(f"  [DRY RUN] prompt preview: {step['prompt'][:120].splitlines()[0]}...")
        return True

    repo_root = Path.cwd()
    expected_files = step_file_list(step)
    step_is_test_only = bool(expected_files) and all(
        "tests/" in f.replace("\\", "/") or Path(f).name.startswith("test_")
        for f in expected_files
    )

    # Pre-step sanity check: a previous failure may have left runaway
    # garbage on disk. Aider would otherwise load it as the file's
    # current state, ballooning prompt size and re-triggering failure.
    if sanity_check_enabled:
        for rel in expected_files:
            f = repo_root / rel
            verdict, reasons = inspect_file(f)
            if verdict is Verdict.CORRUPT:
                new = quarantine_file(f, repo_root)
                print(f"  [sanity] CORRUPT: {rel} — {'; '.join(reasons)} — "
                      f"quarantined to {new.relative_to(repo_root).as_posix()}")
            elif verdict is Verdict.SUSPECT:
                print(f"  [sanity] SUSPECT: {rel} — {'; '.join(reasons)} — proceeding")

    for attempt in range(max_empty_retries + 1):
        attempt_prompt = prompt if attempt == 0 else prompt + _EMPTY_RETRY_SUFFIX
        cmd = build_aider_cmd(step, model, prompt=attempt_prompt)
        if attempt == 0:
            print(f"  aider --message <prompt> {' '.join(cmd[cmd.index('--message')+2:])}")
        else:
            print(f"\n  [retry {attempt}/{max_empty_retries}] re-running with empty-output warning")

        pre_snapshot = _snapshot_py_mtimes(repo_root)
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print(f"\n  [FAILED] exit code {result.returncode}")
            return False

        # Drift check: did aider edit .py files outside the --add list?
        unexpected = _detect_aider_drift(pre_snapshot, repo_root, expected_files)
        if unexpected:
            if step_is_test_only:
                # Test-only steps must not write anywhere outside the
                # expected test targets. Any .py file drift is a model-
                # drift symptom (weak LLM writing the implementation
                # instead of the tests). Delete the junk so the retry
                # + auto-recover path can complete cleanly.
                print("\n  [drift cleanup] test-only step touched files outside --add; deleting:")
                deleted: list[Path] = []
                for p in unexpected:
                    print(f"    - {p}")
                    try:
                        p.unlink()
                        deleted.append(p)
                    except OSError as exc:
                        print(f"      (unlink failed: {exc})")
                _cleanup_empty_drift_dirs(repo_root, deleted)
            else:
                print("\n  [drift warning] aider modified files outside the step's --add list:")
                for p in unexpected:
                    print(f"    - {p}")

        if not strict_outputs:
            break

        ok, problems = verify_outputs(step)

        # Auto-recover: test-only step with only empty problems — write
        # the step's prompt code block to each empty target and re-check.
        # Skips a retry cycle (which would just re-trigger the same
        # model drift) and produces a deterministic correct file.
        if not ok and step_is_test_only:
            empty_only = bool(problems) and all(p.startswith("empty:") for p in problems)
            if empty_only and _autorecover_from_prompt_block(step):
                ok, problems = verify_outputs(step)

        if ok:
            break

        empty_only = bool(problems) and all(p.startswith("empty:") for p in problems)
        if not empty_only or attempt == max_empty_retries:
            print("\n  [FAILED] aider exited 0 but expected outputs are missing/empty:")
            for p in problems:
                print(f"    - {p}")
            return False

        print("\n  [empty output detected — will retry]")
        for p in problems:
            print(f"    - {p}")
        _cleanup_empty_outputs(step)

    print(f"\n  [DONE] {step['title']}")
    return True


def verify_outputs(step: dict) -> tuple[bool, list[str]]:
    """Check every file the step should have generated is present and
    non-empty. Returns (ok, list_of_problems).

    Files allowed to be empty: `__init__.py` (Python package marker)
    and any `.db` / `.sqlite` / `.sqlite3` binary databases initialised
    at runtime. See _ALLOW_EMPTY_BASENAMES / _ALLOW_EMPTY_SUFFIXES."""
    problems: list[str] = []
    for f in step_file_list(step):
        p = Path(f)
        if not p.is_absolute():
            p = Path.cwd() / p
        if not p.exists():
            problems.append(f"missing: {f}")
            continue
        try:
            size = p.stat().st_size
        except OSError as exc:
            problems.append(f"stat-error {f}: {exc}")
            continue
        if size == 0 and not _empty_is_allowed(p):
            problems.append(f"empty: {f}")
    return (not problems), problems
