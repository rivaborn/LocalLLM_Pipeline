"""Subprocess invocation helper that streams output and logs it.

Centralises the pattern used across modes: spawn a PowerShell or Python
child process, stream its stdout to the terminal line-by-line (so
progress bars from pip/tqdm render correctly), log every line to the
pipeline log, and raise on non-zero exit.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path


class StepFailed(Exception):
    """Raised when a pipeline step exits non-zero (other than 130)."""


class UserCancelled(Exception):
    """Raised when a child process exits 130 (Ctrl+Q cancellation)."""


def run_command(
    cmd: list[str],
    cwd: Path,
    logger: logging.Logger,
    dry_run: bool = False,
) -> None:
    logger.info("Running: %s", " ".join(cmd))
    if dry_run:
        logger.info("[DRY RUN] Skipped")
        return

    # stderr=None lets tqdm/progress bars pass through to the terminal.
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=None,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output_lines: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip("\n")
        print(line, flush=True)
        output_lines.append(line)
        logger.debug(line)
    proc.wait()

    if proc.returncode == 130:
        raise UserCancelled()
    if proc.returncode != 0:
        tail = "\n".join(output_lines[-50:])
        msg = (
            f"Command failed (exit {proc.returncode}): {' '.join(cmd)}\n"
            f"output (last 50 lines):\n{tail}"
        )
        logger.error(msg)
        raise StepFailed(msg)


def powershell_cmd(script: Path, *args: str) -> list[str]:
    """Build a 'powershell -NoProfile -ExecutionPolicy Bypass -File <script>' command."""
    return [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        *args,
    ]
