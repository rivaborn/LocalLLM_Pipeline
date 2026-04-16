"""argparse register + run() entry point for the debug pipeline."""
from __future__ import annotations

import argparse
import datetime
from pathlib import Path

from ... import config as cfg
from ...progress import ProgressFile
from ...subprocess_runner import StepFailed, UserCancelled
from ...ui import Color, banner, check_cancel, cprint, setup_logging
from .archive import step6_archive
from .fix_bugs import step5_fix_bugs
from .workers import WORKER_DIR, run_worker_step


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
            run_worker_step(step_num, label, script, repo_root,
                            args.target_dir, args.test_dir, logger, args.dry_run)
            if not args.dry_run:
                progress.save(step_num, mode="debug", target_dir=args.target_dir)

        if last < 5:
            step5_fix_bugs(repo_root, args.target_dir, progress, env, logger, args.dry_run)
        else:
            cprint("\n  Step 5/6 - Fix Bugs [already done]", Color.BLUE)

        if last < 6:
            step6_archive(repo_root, progress, args.target_dir, logger, args.dry_run)
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
