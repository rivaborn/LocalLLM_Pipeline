"""Terminal UI helpers: ANSI colors, Ctrl+Q cancellation, logging setup.

Ported from Arch_Analysis_Pipeline.py so the orchestrator's output is
visually consistent with the legacy pipeline during the deprecation window.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


class Color:
    CYAN    = "\033[96m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    MAGENTA = "\033[95m"
    BLUE    = "\033[94m"
    RED     = "\033[91m"
    BOLD    = "\033[1m"
    RESET   = "\033[0m"


def cprint(msg: str, color: str = Color.RESET) -> None:
    print(f"{color}{msg}{Color.RESET}", file=sys.stderr)


# Windows Ctrl+Q handling -- mirrors the behaviour of the PowerShell
# helpers so a single keypress between steps cancels the whole pipeline.
try:
    import msvcrt  # type: ignore[import-not-found]
    _HAS_MSVCRT = True
except ImportError:
    _HAS_MSVCRT = False


def check_cancel() -> None:
    if not _HAS_MSVCRT:
        return
    if not sys.stdin.isatty():
        return
    while msvcrt.kbhit():  # type: ignore[name-defined]
        ch = msvcrt.getwch()  # type: ignore[name-defined]
        if ch == "\x11":  # Ctrl+Q
            cprint("", Color.RESET)
            cprint("[Ctrl+Q] User cancelled. Exiting cleanly...", Color.YELLOW)
            sys.exit(130)


def enable_windows_ansi() -> None:
    """Enable VT100 escape-sequence processing on Windows terminals."""
    if sys.platform == "win32":
        os.system("")  # triggers ENABLE_VIRTUAL_TERMINAL_PROCESSING


def setup_logging(log_path: Path, logger_name: str = "archpipeline") -> logging.Logger:
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)
    # Avoid duplicate handlers when the module is re-imported in tests.
    if logger.handlers:
        return logger

    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )
    file_handler.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    console_handler.setLevel(logging.INFO)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger


def banner(title: str, color: str = Color.CYAN, width: int = 60) -> None:
    cprint("=" * width, color)
    cprint(f"  {title}", color + Color.BOLD)
    cprint("=" * width, color)
