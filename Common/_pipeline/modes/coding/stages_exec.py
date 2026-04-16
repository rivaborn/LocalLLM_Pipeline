"""Stages 4-5 — execute generated commands.

    4  Walk aidercommands.md and invoke aider per step (run_aider.py).
    5  Post-gen import repair (fix_imports.py).

Both stages shell out to sibling scripts in LocalLLMCoding/; they're
not invoked in-process because run_aider maintains long-lived state
(pyright LSP, ctags cache) best left outside the orchestrator's process.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from ... import config as cfg
from ...progress import ProgressFile
from ...ui import Color, banner, cprint
from .fileops import detect_package_dir


def stage4_run_aider(repo_root: Path, aider_commands: Path,
                     args: argparse.Namespace, progress: ProgressFile, mode: str) -> None:
    banner("Stage 4 - Run Aider (execute aidercommands.md)", Color.CYAN)
    if not aider_commands.exists():
        if args.dry_run:
            cprint(f"  [DRY RUN] Would run aider against {aider_commands.name}", Color.BLUE)
            return
        cprint(f"  ERROR: {aider_commands} not found", Color.RED)
        raise SystemExit(1)

    script = cfg.toolkit_root() / "LocalLLMCoding" / "run_aider.py"
    cmd = [sys.executable, str(script), str(aider_commands)]
    cprint(f"  Invoking: {' '.join(cmd)} (cwd={repo_root})", Color.BLUE)
    if args.dry_run:
        cprint("  [DRY RUN] Skipped", Color.BLUE)
        return
    result = subprocess.run(cmd, cwd=repo_root)
    if result.returncode != 0:
        cprint(f"  Stage 4 failed (exit {result.returncode})", Color.RED)
        raise SystemExit(result.returncode)
    progress.save(4, mode=mode)
    cprint("  Stage 4 complete", Color.GREEN)


def stage5_fix_imports(repo_root: Path, args: argparse.Namespace,
                       progress: ProgressFile, mode: str) -> None:
    banner("Stage 5 - Fix Imports (post-gen repair)", Color.CYAN)
    script = cfg.toolkit_root() / "LocalLLMCoding" / "fix_imports.py"
    pkg = args.package_dir
    if not pkg and getattr(args, "package_name", None):
        pkg = f"src/{args.package_name}"
    if not pkg:
        pkg = detect_package_dir(repo_root)
    if pkg:
        cprint(f"  Package directory: {pkg}", Color.CYAN)
    cmd = [sys.executable, str(script)]
    if pkg:
        cmd += ["--package", pkg]
    cprint(f"  Invoking: {' '.join(cmd)} (cwd={repo_root})", Color.BLUE)
    if args.dry_run:
        cprint("  [DRY RUN] Skipped", Color.BLUE)
        return
    result = subprocess.run(cmd, cwd=repo_root)
    if result.returncode != 0:
        cprint(f"  Stage 5 failed (exit {result.returncode})", Color.RED)
        raise SystemExit(result.returncode)
    progress.save(5, mode=mode)
    cprint("  Stage 5 complete", Color.GREEN)
