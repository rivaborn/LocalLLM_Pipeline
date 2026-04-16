"""Architecture debug mode.

Six-step pipeline absorbed from Arch_Debug_Pipeline.ps1:
    1. dataflow_local.ps1
    2. interfaces_local.ps1
    3. testgap_local.ps1
    4. bughunt_local.ps1
    5. Per-file bug fixing via local LLM (qwen3-coder:30b by default).
       Reads bug_reports/src/*.md, skips clean files, asks the LLM for the
       full corrected source plus a summary, writes the fixed file to disk
       and appends the summary to .debug_changes.md.
    6. Archive .debug_changes.md to "Implemented Plans/Bug Fix Changes N.md".

Resumable via .debug_progress (matches the legacy file format).
"""
from __future__ import annotations

import argparse
import datetime
import re
from pathlib import Path

from .. import config as cfg
from ..ollama import LLMError, invoke_local_llm
from ..progress import ProgressFile
from ..subprocess_runner import (
    StepFailed,
    UserCancelled,
    powershell_cmd,
    run_command,
)
from ..ui import Color, banner, check_cancel, cprint, setup_logging


_WORKER_DIR = cfg.toolkit_root() / "LocalLLMDebug"
_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"

# Indicators that a bug report means "no real issues".
_NO_BUG_PATTERNS = [
    "no significant bugs", "no bugs found", "no issues found",
    "no significant issues", "no critical", "no bugs were",
    "no problems", "no defects",
]


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "debug",
        help="Run the six-step architecture debug pipeline against TargetDir.",
    )
    parser.add_argument("--repo-root", default=None, metavar="DIR",
                        help="Codebase repo root (default: current directory).")
    parser.add_argument("--target-dir", required=True, metavar="DIR",
                        help="Source directory to analyse (e.g. src/nmon).")
    parser.add_argument("--test-dir", default="tests",
                        help="Test directory (default: tests).")
    parser.add_argument("--restart", action="store_true",
                        help="Ignore .debug_progress and start from step 1.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print commands without running them.")
    parser.set_defaults(func=run)


def _has_real_bugs(report: str) -> bool:
    low = report.lower()
    for pat in _NO_BUG_PATTERNS:
        if pat in low:
            return False
    return len(report.strip()) >= 200


def _step_analysis(
    step_num: int,
    label: str,
    script: str,
    repo_root: Path,
    target_dir: str,
    test_dir: str,
    logger,
    dry_run: bool,
) -> None:
    cprint(f"\n  Step {step_num}/6 - {label}", Color.CYAN + Color.BOLD)
    logger.info("Step %d/6: %s", step_num, label)
    # testgap_local.ps1 predates the "TargetDir" convention the other
    # workers adopted; it takes -SrcDir / -TestDir instead.
    if script == "testgap_local.ps1":
        args = ["-SrcDir", target_dir, "-TestDir", test_dir]
    else:
        args = ["-TargetDir", target_dir]
    run_command(
        powershell_cmd(_WORKER_DIR / script, *args),
        repo_root, logger, dry_run,
    )


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


def _step5_fix_bugs(
    repo_root: Path,
    target_dir: str,
    progress: ProgressFile,
    env: dict[str, str],
    logger,
    dry_run: bool,
) -> None:
    cprint("\n  Step 5/6 - Fix Bugs (per file, local LLM)", Color.CYAN + Color.BOLD)
    logger.info("Step 5/6: Fix Bugs (per file, local LLM)")

    if dry_run:
        cprint("  [DRY RUN] Would fix bugs in bug_reports/src/*.md (requires "
               "analysis outputs from steps 1-4)", Color.BLUE)
        return

    arch_dir = Path(env.get("ARCHITECTURE_DIR", "architecture"))
    if not arch_dir.is_absolute():
        arch_dir = repo_root / arch_dir

    required = {
        "INTERFACES.md":   arch_dir / "INTERFACES.md",
        "DATA_FLOW.md":    arch_dir / "DATA_FLOW.md",
        "bug SUMMARY.md":  repo_root / "bug_reports" / "SUMMARY.md",
        "GAP_REPORT.md":   repo_root / "test_gaps" / "GAP_REPORT.md",
    }
    for label, path in required.items():
        if not path.exists():
            cprint(f"  ERROR: Required file not found: {path} ({label})", Color.RED)
            raise StepFailed(f"Missing {label}")

    interfaces = required["INTERFACES.md"].read_text(encoding="utf-8")
    data_flow = required["DATA_FLOW.md"].read_text(encoding="utf-8")

    # target_dir is the subdir the user ran against (e.g. "nmon2" or "src/nmon").
    # The workers mirror that under bug_reports/ and test_gaps/.
    bug_dir = repo_root / "bug_reports" / target_dir
    bug_files = sorted(bug_dir.rglob("*.md")) if bug_dir.is_dir() else []

    gap_dir = repo_root / "test_gaps" / target_dir
    gap_lookup: dict[str, str] = {}
    if gap_dir.is_dir():
        for gf in gap_dir.rglob("*.gap.md"):
            key = gf.relative_to(gap_dir).as_posix()
            gap_lookup[key] = gf.read_text(encoding="utf-8")

    iface_dir = arch_dir / "interfaces"
    iface_lookup: dict[str, str] = {}
    if iface_dir.is_dir():
        for ifl in iface_dir.rglob("*.iface.md"):
            key = ifl.relative_to(iface_dir).as_posix()
            iface_lookup[key] = ifl.read_text(encoding="utf-8")

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
        # Strip the target_dir prefix to build the key used to look up a
        # matching gap report (gap_dir rglobs produce keys relative to itself).
        file_key = re.sub(rf"^{re.escape(target_dir)}/", "", src_rel)
        has_bugs = _has_real_bugs(bug_report)

        if not has_bugs:
            cprint(f"    {i}/{total} - {src_rel} [clean - skipped]", Color.BLUE)
            with change_log.open("a", encoding="utf-8") as fh:
                fh.write(
                    f"\n---\n\nNo changes needed for {src_rel} "
                    "(bug report indicates clean file).\n"
                )
            progress.save(4, sub_step=i, mode="debug", target_dir=target_dir)
            continue

        cprint(f"    {i}/{total} - {src_rel} [{model}]", Color.CYAN)
        if dry_run:
            cprint(f"      [DRY RUN] Would fix: {src_rel}", Color.BLUE)
            continue

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

    if not dry_run:
        progress.save(5, mode="debug", target_dir=target_dir)
        cprint(f"  Step 5 complete - all {total} file(s) processed", Color.GREEN)


def _next_bugfix_number(impl_dir: Path) -> int:
    if not impl_dir.is_dir():
        return 1
    highest = 0
    for p in impl_dir.glob("Bug Fix Changes *.md"):
        m = re.match(r"Bug Fix Changes (\d+)\.md$", p.name)
        if m:
            highest = max(highest, int(m.group(1)))
    return highest + 1


def _step6_archive(repo_root: Path, progress: ProgressFile, target_dir: str, logger, dry_run: bool) -> None:
    cprint("\n  Step 6/6 - Archive Bug Fix Changes", Color.CYAN + Color.BOLD)
    logger.info("Step 6/6: Archive Bug Fix Changes")
    impl_dir = repo_root / "Implemented Plans"
    change_log = repo_root / ".debug_changes.md"

    if dry_run:
        num = _next_bugfix_number(impl_dir)
        cprint(f"  [DRY RUN] Would write: Implemented Plans/Bug Fix Changes {num}.md",
               Color.BLUE)
        return

    if not change_log.exists():
        cprint("  No .debug_changes.md to archive - nothing to do", Color.YELLOW)
        progress.save(6, mode="debug", target_dir=target_dir)
        return

    impl_dir.mkdir(parents=True, exist_ok=True)
    num = _next_bugfix_number(impl_dir)
    dst = impl_dir / f"Bug Fix Changes {num}.md"
    dst.write_text(change_log.read_text(encoding="utf-8"), encoding="utf-8")
    cprint(f"  Archived -> {dst.relative_to(repo_root)}", Color.GREEN)
    change_log.unlink()
    progress.save(6, mode="debug", target_dir=target_dir)


def run(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve() if args.repo_root else Path.cwd()
    if not repo_root.is_dir():
        cprint(f"ERROR: repo root not found: {repo_root}", Color.RED)
        return 1
    logger = setup_logging(_WORKER_DIR / "debug_pipeline.log")

    banner("DEBUG PIPELINE")
    cprint(f"  Target: {args.target_dir}", Color.CYAN)
    cprint(f"  Started at {datetime.datetime.now():%Y-%m-%d %H:%M:%S}", Color.CYAN)
    logger.info("Debug pipeline started; target=%s", args.target_dir)

    progress = ProgressFile(repo_root / ".debug_progress")
    if args.restart:
        progress.clear()
        cprint("  -Restart: cleared saved progress", Color.YELLOW)

    state = progress.read()
    last = state.last_completed if state.last_completed > 0 else 0

    env = cfg.load_env()

    try:
        analysis_steps = [
            (1, "Data flow analysis",     "dataflow_local.ps1"),
            (2, "Interfaces extraction",  "interfaces_local.ps1"),
            (3, "Test gap analysis",      "testgap_local.ps1"),
            (4, "Bug hunt",               "bughunt_local.ps1"),
        ]
        for step_num, label, script in analysis_steps:
            check_cancel()
            if last >= step_num:
                cprint(f"\n  Step {step_num}/6 - {label} [already done]", Color.BLUE)
                continue
            _step_analysis(step_num, label, script, repo_root, args.target_dir,
                           args.test_dir, logger, args.dry_run)
            if not args.dry_run:
                progress.save(step_num, mode="debug", target_dir=args.target_dir)

        if last < 5:
            _step5_fix_bugs(repo_root, args.target_dir, progress, env, logger, args.dry_run)
        else:
            cprint("\n  Step 5/6 - Fix Bugs [already done]", Color.BLUE)

        if last < 6:
            _step6_archive(repo_root, progress, args.target_dir, logger, args.dry_run)
        else:
            cprint("\n  Step 6/6 - Archive [already done]", Color.BLUE)

    except UserCancelled:
        cprint("[Ctrl+Q] Cancelled. Progress saved.", Color.YELLOW)
        return 130
    except StepFailed as exc:
        cprint(f"PIPELINE FAILED: {exc}", Color.RED + Color.BOLD)
        logger.error("PIPELINE FAILED: %s", exc)
        return 1
    except Exception as exc:  # noqa: BLE001
        cprint(f"Unexpected error: {exc}", Color.RED + Color.BOLD)
        logger.error("Unexpected error: %s", exc)
        return 2

    if not args.dry_run:
        progress.clear()
    banner("DEBUG PIPELINE COMPLETE", Color.GREEN)
    return 0
