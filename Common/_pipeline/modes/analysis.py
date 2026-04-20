"""Architecture analysis mode.

Absorbs the old Arch_Analysis_Pipeline.py. Walks every subsection declared
in Common/.env, invokes six PowerShell worker scripts in sequence, and
skips subsections whose output folder already exists (detected by the
`^\\d+\\. <name>` naming convention used when the legacy pipeline renamed
architecture/ on completion; we keep the detection so a partial legacy
run can be resumed under the new orchestrator).
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import logging
import re
import sys
from pathlib import Path

from .. import config as cfg
from ..subprocess_runner import (
    StepFailed,
    UserCancelled,
    powershell_cmd,
    run_command,
)
from ..ui import Color, banner, check_cancel, cprint, setup_logging


@dataclasses.dataclass(frozen=True)
class PipelineStep:
    name: str
    script: str
    args: list[str]
    use_target_dir: bool


PIPELINE_STEPS: list[PipelineStep] = [
    # archgen_local.ps1 defaults -Preset to the .env PRESET value; no CLI
    # override here so the preset follows the target project's language.
    PipelineStep("Per-file docs",         "archgen_local.ps1",       [],                      True),
    PipelineStep("Cross-reference index", "archxref.ps1",            [],                      False),
    PipelineStep("Mermaid diagrams",      "archgraph.ps1",           [],                      False),
    PipelineStep("Architecture overview", "arch_overview_local.ps1", [],                      False),
    PipelineStep("Pass 2 context",        "archpass2_context.ps1",   [],                      False),
    PipelineStep("Pass 2 analysis",       "archpass2_local.ps1",     [],                      False),
]

# Location of the PowerShell workers inside the toolkit.
_WORKER_DIR = cfg.toolkit_root() / "LocalLLMAnalysis"


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "analysis",
        help="Run the architecture analysis pipeline for every subsection in Common/.env.",
    )
    parser.add_argument("--repo-root", default=None, metavar="DIR",
                        help="Codebase repo root (default: current directory).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be run without invoking workers.")
    parser.add_argument("--start-from", type=int, default=1, metavar="N",
                        help="Skip subsections 1..N-1 (1-indexed).")
    parser.add_argument("--skip-lsp", action="store_true",
                        help="Skip one-time compile_commands.json + serena_extract setup.")
    parser.set_defaults(func=run)


def _is_subsection_completed(repo_root: Path, subsection: str) -> bool:
    """Legacy-compat check: detect a `N. <name>` folder (old rename convention).

    The current pipeline no longer renames, so this only returns True for
    folders left behind by an older run. New runs re-process whatever's in
    architecture/ every time.
    """
    sanitized = cfg.sanitize_subsection_name(subsection)
    for item in repo_root.iterdir():
        if item.is_dir() and re.match(r"^\d+\.\s+", item.name) and item.name.endswith(sanitized):
            return True
    return False


def _build_cmd(step: PipelineStep, subsection: str) -> list[str]:
    script = _WORKER_DIR / step.script
    extra: list[str] = []
    if step.use_target_dir:
        extra.extend(["-TargetDir", subsection])
    extra.extend(step.args)
    return powershell_cmd(script, *extra)


def _one_time_setup(repo_root: Path, logger: logging.Logger, dry_run: bool, skip_lsp: bool) -> None:
    if skip_lsp:
        logger.info("Skipping LSP setup (--skip-lsp)")
        return
    banner("ONE-TIME SETUP STEPS")
    logger.info("=== One-time setup steps ===")

    cprint("  >> generate_compile_commands.py", Color.BLUE)
    run_command(
        [sys.executable, str(_WORKER_DIR / "generate_compile_commands.py")],
        repo_root, logger, dry_run,
    )
    cprint("  >> serena_extract.ps1", Color.BLUE)
    run_command(
        powershell_cmd(_WORKER_DIR / "serena_extract.ps1"),
        repo_root, logger, dry_run,
    )


def _run_subsections(
    repo_root: Path,
    subsections: list[str],
    logger: logging.Logger,
    dry_run: bool,
    start_from: int,
) -> None:
    total = len(subsections)
    cprint("Press Ctrl+Q to cancel (checked between steps).", Color.BLUE)

    for i, subsection in enumerate(subsections, start=1):
        check_cancel()
        if i < start_from:
            cprint(f"  SKIP {i}/{total}: {subsection} (--start-from)", Color.YELLOW)
            logger.info("Skipping %d/%d: %s (--start-from)", i, total, subsection)
            continue
        if _is_subsection_completed(repo_root, subsection):
            cprint(f"  SKIP {i}/{total}: {subsection} (already completed)", Color.YELLOW)
            logger.info("Skipping %d/%d: %s (already completed)", i, total, subsection)
            continue

        banner(f"SUBSECTION {i}/{total}: {subsection}", Color.GREEN)
        logger.info("Subsection %d/%d: %s", i, total, subsection)

        for step_idx, step in enumerate(PIPELINE_STEPS, start=1):
            check_cancel()
            cprint(
                f"  --- Step {step_idx}/{len(PIPELINE_STEPS)}: {step.name} ---",
                Color.CYAN + Color.BOLD,
            )
            logger.info("  Step %d/%d: %s", step_idx, len(PIPELINE_STEPS), step.name)
            run_command(_build_cmd(step, subsection), repo_root, logger, dry_run)


def run(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve() if args.repo_root else Path.cwd()
    if not repo_root.is_dir():
        cprint(f"ERROR: repo root not found: {repo_root}", Color.RED)
        return 1
    log_path = _WORKER_DIR / "pipeline.log"
    logger = setup_logging(log_path)

    banner("ARCHITECTURE PIPELINE")
    cprint(f"  Started at {datetime.datetime.now():%Y-%m-%d %H:%M:%S}", Color.CYAN)
    logger.info("Pipeline started at %s", datetime.datetime.now())

    subsections = cfg.parse_subsections()
    if not subsections:
        cprint("ERROR: No subsections found in .env", Color.RED)
        logger.error("No subsections found in .env")
        return 1

    # Per-mode models summary — analysis routes every per-subsection
    # worker through the same local LLM via $env:LLM_MODEL / $env:LLM_ENDPOINT
    # inside the .ps1 scripts.
    env = cfg.load_env()
    model = cfg.resolve_model(env, "LLM_MODEL", "qwen3-coder:30b")
    endpoint = cfg.resolve_ollama_endpoint(env)
    cprint("\n  Models for this run:", Color.CYAN)
    cprint(
        f"    All six workers (archgen, archxref, archgraph, arch_overview, "
        f"archpass2_context, archpass2_local) -> local '{model}' @ {endpoint}",
        Color.CYAN,
    )
    if args.start_from < 1:
        cprint("ERROR: --start-from must be >= 1", Color.RED)
        return 1
    if args.start_from > len(subsections):
        cprint("ERROR: --start-from exceeds subsection count", Color.RED)
        return 1

    try:
        _one_time_setup(repo_root, logger, args.dry_run, args.skip_lsp)
        _run_subsections(repo_root, subsections, logger, args.dry_run, args.start_from)
    except UserCancelled:
        cprint("[Ctrl+Q] Child process cancelled by user. Exiting pipeline.", Color.YELLOW)
        return 130
    except StepFailed as exc:
        cprint(f"PIPELINE FAILED: {exc}", Color.RED + Color.BOLD)
        logger.error("PIPELINE FAILED: %s", exc)
        return 1
    except Exception as exc:  # noqa: BLE001
        cprint(f"Unexpected error: {exc}", Color.RED + Color.BOLD)
        logger.error("Unexpected error: %s", exc)
        return 2

    banner("PIPELINE COMPLETE", Color.GREEN)
    logger.info("Pipeline complete.")
    return 0
