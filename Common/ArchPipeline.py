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

from _pipeline.modes import analysis
from _pipeline.ui import enable_windows_ansi


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ArchPipeline",
        description="Unified orchestrator for the LocalLLM_Pipeline toolkit "
                    "(analysis, debug, coding modes).",
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)
    analysis.register(subparsers)
    # debug.register(subparsers)  # phase 2
    # coding.register(subparsers) # phase 3
    return parser


def main() -> int:
    enable_windows_ansi()
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
