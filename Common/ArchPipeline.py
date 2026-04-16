#!/usr/bin/env python3
"""Unified ArchPipeline orchestrator.

Replaces the legacy triple:
    LocalLLMCoding/Arch_Coding_Pipeline.ps1
    LocalLLMAnalysis/Arch_Analysis_Pipeline.py
    LocalLLMDebug/Arch_Debug_Pipeline.ps1

Usage:
    python ArchPipeline.py analysis [flags]
    python ArchPipeline.py debug    [flags]    (phase 2 -- not yet wired)
    python ArchPipeline.py coding   [flags]    (phase 3 -- not yet wired)

Run from the target project's directory; the working directory is
treated as the repo root for path resolution, matching the convention
of the legacy Arch_Analysis_Pipeline.py.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the _pipeline package importable regardless of how the script is
# invoked (from repo root, from Common/, or via an absolute path).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _pipeline.modes import all_modes, analysis, coding, debug
from _pipeline.ui import enable_windows_ansi


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ArchPipeline",
        description="Unified orchestrator for the LocalLLM_Pipeline toolkit "
                    "(all, coding, analysis, debug).",
    )
    subparsers = parser.add_subparsers(dest="mode")
    all_modes.register(subparsers)
    analysis.register(subparsers)
    debug.register(subparsers)
    coding.register(subparsers)
    return parser


def main() -> int:
    enable_windows_ansi()
    parser = build_parser()
    argv = sys.argv[1:]
    # Default to 'all' when no subcommand is given.
    if not argv or argv[0] not in ("all", "analysis", "debug", "coding", "-h", "--help"):
        argv = ["all"] + argv
    args = parser.parse_args(argv)
    if args.mode is None:
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
