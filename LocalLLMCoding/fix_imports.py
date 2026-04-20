#!/usr/bin/env python3
"""
Advisory import checker. Walks every .py file under a package root,
tries to import it in a fresh Python process, and for each failure asks
the local LLM to diagnose the root cause and propose a specific fix.

Does NOT apply any fixes. The LLM's proposals are written to a log file
for the user to implement manually (installing packages, editing files,
invoking aider interactively, etc.).

Usage (from project root, with the project venv activated):
    python fix_imports.py --package nmon2
    python fix_imports.py --package src/nmon --log fix_imports.log

Design rationale: the earlier auto-fix version invoked aider, which
sometimes asked for missing packages to be installed and then started
the next iteration without waiting. This version stops at "here's what
to do" so humans decide how to act on each proposal.
"""
import argparse
import datetime
import os
import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent / "Common"))
from _pipeline import config as cfg           # noqa: E402
from _pipeline.ollama import LLMError, invoke_local_llm  # noqa: E402

DEFAULT_DIAGNOSE_MODEL = "qwen3-coder:30b"
# Cap the per-file source we feed the LLM so huge files don't blow the
# context window. 400 lines is enough to see imports + class signatures.
_MAX_FILE_LINES = 400


def resolve_diagnose_config() -> tuple[str, str]:
    """Return (model, endpoint) using the shared endpoint precedence.
    Model resolution chains LLM_AIDER_MODEL -> LLM_DEFAULT_MODEL -> fallback
    (LLM_FIX_IMPORTS_MODEL was deprecated; it always mirrored LLM_AIDER_MODEL)."""
    env = cfg.load_env()
    model = cfg.resolve_model(env, "LLM_AIDER_MODEL", DEFAULT_DIAGNOSE_MODEL)
    endpoint = cfg.resolve_ollama_endpoint(env)
    return model, endpoint


def find_modules(package_dir: Path) -> list[tuple[str, Path]]:
    """Return (dotted_module_name, file_path) for every .py under
    package_dir. Dotted name starts at package_dir.name so the caller's
    Python interpreter can import it directly (assumes editable install)."""
    root = package_dir.parent
    result: list[tuple[str, Path]] = []
    for py in sorted(package_dir.rglob("*.py")):
        rel = py.relative_to(root).with_suffix("")
        parts = list(rel.parts)
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        if not parts:
            continue
        result.append((".".join(parts), py))
    return result


def try_import(mod_name: str, python_exe: str) -> tuple[bool, str]:
    result = subprocess.run(
        [python_exe, "-c", f"import {mod_name}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return True, ""
    return False, result.stderr.strip()


_RELATED_PATTERNS = [
    re.compile(r"cannot import name '[^']+' from '([\w\.]+)'"),
    re.compile(r"No module named '([\w\.]+)'"),
    re.compile(r"from ([\w\.]+) import "),
]


def locate_related_file(stderr: str, src_root: Path) -> Path | None:
    """Best-effort: find a sibling .py file named in the import error."""
    for pat in _RELATED_PATTERNS:
        m = pat.search(stderr)
        if not m:
            continue
        dotted = m.group(1)
        candidate = src_root / Path(*dotted.split(".")).with_suffix(".py")
        if candidate.exists():
            return candidate
        init_candidate = src_root / Path(*dotted.split(".")) / "__init__.py"
        if init_candidate.exists():
            return init_candidate
    return None


def _read_snippet(path: Path, max_lines: int = _MAX_FILE_LINES) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return f"<could not read {path}: {exc}>"
    if len(lines) <= max_lines:
        return "\n".join(lines)
    head = lines[: max_lines // 2]
    tail = lines[-max_lines // 2 :]
    return "\n".join(head) + f"\n\n... [truncated {len(lines) - max_lines} lines] ...\n\n" + "\n".join(tail)


_DIAGNOSE_PROMPT = """You are an expert Python developer diagnosing a failing import.

DO NOT write any code. Your job is to diagnose and propose a single
concrete fix that a human will implement manually.

Output EXACTLY this structure:

## Root cause
<one or two sentences identifying what is wrong>

## Fix type
<choose ONE: MISSING_PACKAGE | WRONG_IMPORT | MISSING_SYMBOL | CIRCULAR_IMPORT | OTHER>

## Action
<the exact action the user should take. Include:
 - pip install commands with the exact package name, OR
 - file path + line number + before/after snippet for an edit, OR
 - a refactoring description for structural issues.
 Be specific. No "maybe" or "try". Pick the most likely fix.>

## Confidence
<LOW | MEDIUM | HIGH>

## Notes
<any caveats, alternative interpretations, or risks worth flagging>
"""


def diagnose_failure(
    mod_names: list[str],
    target: Path,
    related: Path | None,
    combined_err: str,
    src_root: Path,
    model: str,
    env: dict[str, str],
    timeout: int,
    num_ctx: int,
    max_tokens: int,
) -> str:
    """Ask the local LLM for a proposed fix. Returns the LLM response
    text (or an error marker on failure)."""
    target_rel = target.relative_to(src_root.parent)
    parts = [
        _DIAGNOSE_PROMPT,
        "\n---\n",
        f"Failing module(s): {', '.join(mod_names)}",
        f"Primary file: {target_rel}",
    ]
    if related and related != target:
        parts.append(f"Related file: {related.relative_to(src_root.parent)}")
    parts.append("\n## Error output\n")
    parts.append("```")
    parts.append(combined_err)
    parts.append("```")
    parts.append(f"\n## Contents of {target_rel}\n")
    parts.append("```python")
    parts.append(_read_snippet(target))
    parts.append("```")
    if related and related != target:
        rel = related.relative_to(src_root.parent)
        parts.append(f"\n## Contents of {rel}\n")
        parts.append("```python")
        parts.append(_read_snippet(related))
        parts.append("```")

    prompt = "\n".join(parts)
    try:
        return invoke_local_llm(
            prompt,
            env=env,
            model=model,
            num_ctx=num_ctx,
            max_tokens=max_tokens,
            timeout=timeout,
            temperature=0.1,
        )
    except LLMError as exc:
        return f"(LLM diagnosis failed: {exc})"


def run_once(package_dir: Path, python_exe: str) -> list[tuple[str, Path, str]]:
    modules = find_modules(package_dir)
    failures: list[tuple[str, Path, str]] = []
    for mod_name, path in modules:
        ok, err = try_import(mod_name, python_exe)
        status = "OK  " if ok else "FAIL"
        print(f"  [{status}] {mod_name}")
        if not ok:
            failures.append((mod_name, path, err))
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", default="src/nmon",
                        help="Package directory to walk (default: src/nmon)")
    parser.add_argument("--python", default=sys.executable,
                        help="Python interpreter for import checks "
                             "(default: current interpreter — run inside the project venv)")
    parser.add_argument("--log", default="fix_imports.log",
                        help="Log file path (default: ./fix_imports.log)")
    parser.add_argument("--no-diagnose", action="store_true",
                        help="Skip LLM diagnosis; only list failures.")
    args = parser.parse_args()

    pkg_dir = Path(args.package).resolve()
    if not pkg_dir.is_dir():
        sys.exit(f"Package directory not found: {pkg_dir}")
    src_root = pkg_dir.parent

    env = cfg.load_env()
    model, endpoint = resolve_diagnose_config()
    os.environ.setdefault("OLLAMA_API_BASE", endpoint)
    print(f"[fix_imports] OLLAMA_API_BASE={endpoint}")
    print(f"[fix_imports] model={model}")

    num_ctx = int(env.get("LLM_NUM_CTX", "32768"))
    timeout = int(env.get("LLM_TIMEOUT", "600"))
    max_tokens = int(env.get("LLM_FIX_MAX_TOKENS", "4096"))

    log_path = Path(args.log).resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open("w", encoding="utf-8") as log:
        log.write("# fix_imports.py — advisory report\n\n")
        log.write(f"Started:  {datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n")
        log.write(f"Package:  {pkg_dir}\n")
        log.write(f"Model:    {model}\n")
        log.write(f"Endpoint: {endpoint}\n\n")
        print(f"[fix_imports] logging to {log_path}")

        print("\n=== Import check ===")
        failures = run_once(pkg_dir, args.python)

        if not failures:
            msg = "All modules import cleanly."
            print(f"\n{msg}")
            log.write(f"{msg}\n")
            return

        header = f"{len(failures)} module(s) failed."
        print(f"\n{header}")
        log.write(f"## {header}\n\n")

        # Summary and error details first — always captured even if the
        # user aborts during LLM diagnosis.
        log.write("### Summary\n")
        for mod_name, path, err in failures:
            rel_path = path.relative_to(src_root.parent)
            last_line = err.splitlines()[-1] if err else "<no stderr>"
            log.write(f"- {mod_name} ({rel_path}): {last_line}\n")

        log.write("\n### Full errors\n\n")
        related_for: dict[str, Path | None] = {}
        for mod_name, path, err in failures:
            rel_path = path.relative_to(src_root.parent)
            related = locate_related_file(err, src_root)
            related_for[mod_name] = related
            log.write(f"#### {mod_name}  ({rel_path})\n")
            if related:
                log.write(f"Related file: {related.relative_to(src_root.parent)}\n")
            log.write("```\n")
            log.write(err if err else "<no stderr>")
            log.write("\n```\n\n")
        log.flush()

        if args.no_diagnose:
            print("\n[--no-diagnose] Skipping LLM diagnosis.")
            return

        # Dedupe by edit scope: when multiple failing modules trace back
        # to the same source file, ask the LLM once per unique scope.
        groups: dict[tuple, dict] = {}
        for mod_name, path, err in failures:
            related = related_for[mod_name]
            scope_paths = sorted({str(path), str(related)} if related else {str(path)})
            key = tuple(scope_paths)
            if key not in groups:
                groups[key] = {"target": path, "related": related,
                               "modules": [], "errors": []}
            groups[key]["modules"].append(mod_name)
            groups[key]["errors"].append(err)

        print(f"\n=== Diagnosing {len(groups)} unique edit scope(s) "
              f"for {len(failures)} failure(s) ===")
        log.write("\n## Proposed fixes\n\n")

        ts_start = datetime.datetime.now()
        for i, (key, g) in enumerate(groups.items(), start=1):
            mods = ", ".join(g["modules"])
            rel_target = g["target"].relative_to(src_root.parent)
            print(f"\n--- [{i}/{len(groups)}] {rel_target}  (modules: {mods}) ---")
            for mod_name, err in zip(g["modules"], g["errors"]):
                last_line = err.splitlines()[-1] if err else "<no stderr>"
                print(f"    [{mod_name}] {last_line}")

            combined_err = "\n\n---\n\n".join(
                f"# Module {m}\n{e}" for m, e in zip(g["modules"], g["errors"])
            )
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            print(f"    [diagnose: {model}] - ({ts})")
            proposal = diagnose_failure(
                g["modules"], g["target"], g["related"], combined_err,
                src_root, model, env, timeout, num_ctx, max_tokens,
            )

            log.write(f"### [{i}/{len(groups)}] {rel_target}  (modules: {mods})\n\n")
            if g["related"] and g["related"] != g["target"]:
                log.write(f"Related file: {g['related'].relative_to(src_root.parent)}\n\n")
            log.write(proposal.strip() + "\n\n---\n\n")
            log.flush()

        elapsed = (datetime.datetime.now() - ts_start).total_seconds()
        summary = (f"Diagnosed {len(groups)} scope(s) in {elapsed:.1f}s. "
                   "Review proposed fixes in the log and implement manually.")
        print(f"\n{summary}")
        log.write(f"## Done\n{summary}\n")


if __name__ == "__main__":
    main()
