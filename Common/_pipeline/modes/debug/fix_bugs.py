"""Step 5: per-file bug fix loop driven by a local LLM.

Reads bug_reports/<target>/*.md, skips files the report calls clean,
asks qwen3-coder:30b (by default) for the full corrected source plus a
summary, writes the file back and appends the summary to
.debug_changes.md. Resumable mid-loop via ProgressFile sub-steps.
"""
from __future__ import annotations

import datetime
import logging
import re
from pathlib import Path

from ...ollama import LLMError, invoke_local_llm
from ...progress import ProgressFile
from ...subprocess_runner import StepFailed
from ...ui import Color, check_cancel, cprint


_PROMPT_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

_NO_BUG_PATTERNS = [
    "no significant bugs", "no bugs found", "no issues found",
    "no significant issues", "no critical", "no bugs were",
    "no problems", "no defects",
]


def has_real_bugs(report: str) -> bool:
    low = report.lower()
    for pat in _NO_BUG_PATTERNS:
        if pat in low:
            return False
    return len(report.strip()) >= 200


def _fix_one_file(
    src_rel: str,
    src_full: Path,
    bug_report: str,
    iface_snippet: str,
    gap_snippet: str,
    data_flow: str,
    env: dict[str, str],
    model: str,
    max_tokens: int,
    num_ctx: int,
    timeout: int,
    change_log: Path,
) -> None:
    template = (_PROMPT_DIR / "debug_fix.md").read_text(encoding="utf-8")
    prompt = (
        template.replace("SRCPATH", src_rel)
        + "\n\n## Bug Report\n\n"
        + bug_report
        + iface_snippet
        + gap_snippet
        + "\n\n## Data Flow Context (system-wide)\n\n"
        + data_flow
        + f"\n\n## Source File: {src_rel}\n\n```python\n"
        + src_full.read_text(encoding="utf-8", errors="replace")
        + "\n```\n"
    )

    result = invoke_local_llm(
        prompt,
        env=env,
        model=model,
        max_tokens=max_tokens,
        num_ctx=num_ctx,
        timeout=timeout,
        temperature=0.1,
    )

    match = re.search(r"```(?:python|py)?\s*\r?\n(.*?)\r?\n```", result, re.DOTALL)
    if not match:
        raise RuntimeError(
            f"Could not find fenced code block in LLM response for {src_rel}. "
            "Raw response too long to print; inspect .debug_response.txt"
        )
    src_full.write_text(match.group(1), encoding="utf-8")

    summary = result[match.end():].strip() or f"### {src_rel}\n(fix applied; model produced no summary)"
    with change_log.open("a", encoding="utf-8") as fh:
        fh.write(f"\n---\n\n{summary}\n")


def _load_context(repo_root: Path, target_dir: str, env: dict[str, str]):
    arch_dir = Path(env.get("ARCHITECTURE_DIR", "architecture"))
    if not arch_dir.is_absolute():
        arch_dir = repo_root / arch_dir

    required = {
        "INTERFACES.md":  arch_dir / "INTERFACES.md",
        "DATA_FLOW.md":   arch_dir / "DATA_FLOW.md",
        "bug SUMMARY.md": repo_root / "bug_reports" / "SUMMARY.md",
        "GAP_REPORT.md":  repo_root / "test_gaps" / "GAP_REPORT.md",
    }
    for label, path in required.items():
        if not path.exists():
            cprint(f"  ERROR: Required file not found: {path} ({label})", Color.RED)
            raise StepFailed(f"Missing {label}")

    data_flow = required["DATA_FLOW.md"].read_text(encoding="utf-8")

    bug_dir = repo_root / "bug_reports" / target_dir
    bug_files = sorted(bug_dir.rglob("*.md")) if bug_dir.is_dir() else []

    gap_dir = repo_root / "test_gaps" / target_dir
    gap_lookup: dict[str, str] = {}
    if gap_dir.is_dir():
        for gf in gap_dir.rglob("*.gap.md"):
            gap_lookup[gf.relative_to(gap_dir).as_posix()] = gf.read_text(encoding="utf-8")

    iface_dir = arch_dir / "interfaces"
    iface_lookup: dict[str, str] = {}
    if iface_dir.is_dir():
        for ifl in iface_dir.rglob("*.iface.md"):
            iface_lookup[ifl.relative_to(iface_dir).as_posix()] = ifl.read_text(encoding="utf-8")

    return data_flow, bug_files, gap_lookup, iface_lookup


def step5_fix_bugs(
    repo_root: Path,
    target_dir: str,
    progress: ProgressFile,
    env: dict[str, str],
    logger: logging.Logger,
    dry_run: bool,
) -> None:
    cprint("\n  Step 5/6 - Fix Bugs (per file, local LLM)", Color.CYAN + Color.BOLD)
    logger.info("Step 5/6: Fix Bugs (per file, local LLM)")

    if dry_run:
        cprint("  [DRY RUN] Would fix bugs in bug_reports/<target>/*.md (requires "
               "analysis outputs from steps 1-4)", Color.BLUE)
        return

    data_flow, bug_files, gap_lookup, iface_lookup = _load_context(repo_root, target_dir, env)

    change_log = repo_root / ".debug_changes.md"
    resume_sub = progress.read().sub_step if progress.read().last_completed == 4 else None

    if not bug_files:
        cprint("  No per-file bug reports found - nothing to fix", Color.YELLOW)
        progress.save(5, mode="debug", target_dir=target_dir)
        return

    total = len(bug_files)
    if resume_sub is None:
        header = (
            "# Bug Fix Changes - Detailed Log\n\n"
            f"Generated: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n"
            f"Target: {target_dir}\n\n"
        )
        change_log.write_text(header, encoding="utf-8")
        cprint(f"  Fixing bugs in {total} file(s)...", Color.BLUE)
    else:
        cprint(f"  Resuming from file {resume_sub + 1} ({resume_sub} of {total} done)",
               Color.YELLOW)

    model = env.get("LLM_MODEL", "qwen3-coder:30b")
    num_ctx = int(env.get("LLM_NUM_CTX", "32768"))
    timeout = int(env.get("LLM_TIMEOUT", "600"))
    max_tokens = int(env.get("LLM_FIX_MAX_TOKENS", "16384"))

    for i, bf in enumerate(bug_files, start=1):
        check_cancel()
        if resume_sub is not None and i <= resume_sub:
            rel = bf.relative_to(repo_root / "bug_reports").as_posix()
            cprint(f"    {i}/{total} - {rel} [already done]", Color.BLUE)
            continue

        rel_md = bf.relative_to(repo_root / "bug_reports").as_posix()
        src_rel = rel_md[:-3] if rel_md.endswith(".md") else rel_md  # drop trailing .md
        src_full = repo_root / src_rel.replace("/", "\\")
        if not src_full.exists():
            cprint(f"      Warning: Source file not found: {src_full}", Color.YELLOW)
            progress.save(4, sub_step=i, mode="debug", target_dir=target_dir)
            continue

        bug_report = bf.read_text(encoding="utf-8")
        # Strip target_dir prefix to derive the gap_lookup key (gap_dir
        # rglobs produce keys relative to itself).
        file_key = re.sub(rf"^{re.escape(target_dir)}/", "", src_rel)

        if not has_real_bugs(bug_report):
            cprint(f"    {i}/{total} - {src_rel} [clean - skipped]", Color.BLUE)
            with change_log.open("a", encoding="utf-8") as fh:
                fh.write(
                    f"\n---\n\nNo changes needed for {src_rel} "
                    "(bug report indicates clean file).\n"
                )
            progress.save(4, sub_step=i, mode="debug", target_dir=target_dir)
            continue

        cprint(f"    {i}/{total} - {src_rel} [{model}]", Color.CYAN)

        iface_snippet = ""
        iface_key = f"{src_rel}.iface.md"
        if iface_key in iface_lookup:
            iface_snippet = f"\n## Interface Contract for {src_rel}\n\n" + iface_lookup[iface_key]
        gap_snippet = ""
        gap_key = f"{file_key}.gap.md"
        if gap_key in gap_lookup:
            gap_snippet = f"\n## Test Gap Report for {src_rel}\n\n" + gap_lookup[gap_key]

        try:
            _fix_one_file(
                src_rel, src_full, bug_report, iface_snippet, gap_snippet,
                data_flow, env, model, max_tokens, num_ctx, timeout, change_log,
            )
        except (LLMError, RuntimeError) as exc:
            cprint(f"      ERROR: {exc}", Color.RED)
            raise StepFailed(str(exc)) from exc

        cprint(f"      {i}/{total} - done", Color.GREEN)
        progress.save(4, sub_step=i, mode="debug", target_dir=target_dir)

    progress.save(5, mode="debug", target_dir=target_dir)
    cprint(f"  Step 5 complete - all {total} file(s) processed", Color.GREEN)
