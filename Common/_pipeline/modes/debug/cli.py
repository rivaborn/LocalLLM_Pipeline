"""argparse register + run() entry point for the debug pipeline.

Also contains the step 1-4 worker dispatcher and step 6 archiver
(too small for their own files).
"""
from __future__ import annotations

import argparse
import datetime
import logging
import re
from pathlib import Path

from ... import config as cfg
from ...progress import ProgressFile
from ...subprocess_runner import StepFailed, UserCancelled, powershell_cmd, run_command
from ...ui import Color, banner, check_cancel, cprint, setup_logging
from .fix_bugs import step5_fix_bugs


WORKER_DIR = cfg.toolkit_root() / "LocalLLMDebug"


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


_ANALYSIS_STEPS = [
    (1, "Data flow analysis",     "dataflow_local.ps1"),
    (2, "Interfaces extraction",  "interfaces_local.ps1"),
    (3, "Test gap analysis",      "testgap_local.ps1"),
    (4, "Bug hunt",               "bughunt_local.ps1"),
]


def _run_worker_step(
    step_num: int,
    label: str,
    script: str,
    repo_root: Path,
    target_dir: str,
    test_dir: str,
    logger: logging.Logger,
    dry_run: bool,
) -> None:
    cprint(f"\n  Step {step_num}/6 - {label}", Color.CYAN + Color.BOLD)
    logger.info("Step %d/6: %s", step_num, label)
    if script == "testgap_local.ps1":
        args = ["-SrcDir", target_dir, "-TestDir", test_dir]
    else:
        args = ["-TargetDir", target_dir]
    run_command(
        powershell_cmd(WORKER_DIR / script, *args),
        repo_root, logger, dry_run,
    )


def _next_bugfix_number(impl_dir: Path) -> int:
    if not impl_dir.is_dir():
        return 1
    highest = 0
    for p in impl_dir.glob("Bug Fix Changes *.md"):
        m = re.match(r"Bug Fix Changes (\d+)\.md$", p.name)
        if m:
            highest = max(highest, int(m.group(1)))
    return highest + 1


def _step6_archive(
    repo_root: Path,
    progress: ProgressFile,
    target_dir: str,
    logger: logging.Logger,
    dry_run: bool,
) -> None:
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
    logger = setup_logging(WORKER_DIR / "debug_pipeline.log")

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
        for step_num, label, script in _ANALYSIS_STEPS:
            check_cancel()
            if last >= step_num:
                cprint(f"\n  Step {step_num}/6 - {label} [already done]", Color.BLUE)
                continue
            _run_worker_step(step_num, label, script, repo_root,
                             args.target_dir, args.test_dir, logger, args.dry_run)
            if not args.dry_run:
                progress.save(step_num, mode="debug", target_dir=args.target_dir)

        if last < 5:
            step5_fix_bugs(repo_root, args.target_dir, progress, env, logger, args.dry_run)
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
