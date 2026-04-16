"""Dispatcher for the four PowerShell analysis workers (steps 1-4)."""
from __future__ import annotations

import logging
from pathlib import Path

from ... import config as cfg
from ...subprocess_runner import powershell_cmd, run_command
from ...ui import Color, cprint


WORKER_DIR = cfg.toolkit_root() / "LocalLLMDebug"


def run_worker_step(
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
    # testgap_local.ps1 predates the "TargetDir" convention the other
    # workers adopted; it takes -SrcDir / -TestDir instead.
    if script == "testgap_local.ps1":
        args = ["-SrcDir", target_dir, "-TestDir", test_dir]
    else:
        args = ["-TargetDir", target_dir]
    run_command(
        powershell_cmd(WORKER_DIR / script, *args),
        repo_root, logger, dry_run,
    )
