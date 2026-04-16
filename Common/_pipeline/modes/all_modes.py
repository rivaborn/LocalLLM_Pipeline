"""End-to-end 'all' mode.

Runs coding -> analysis -> debug in sequence, sharing repo-root. Stops on
first failure unless --continue-on-error is passed. --from-section lets
you skip earlier sections.
"""
from __future__ import annotations

import argparse
import copy
from pathlib import Path

from . import analysis, coding, debug
from ..ui import Color, banner, cprint


SECTIONS = ("coding", "analysis", "debug")


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "all",
        help="Run coding -> analysis -> debug end-to-end (default).",
    )
    # Cross-section control.
    parser.add_argument("--from-section", choices=SECTIONS, default="coding",
                        help="Start from this section (default: coding).")
    parser.add_argument("--continue-on-error", action="store_true",
                        help="Continue to the next section if one fails.")
    # Shared.
    parser.add_argument("--repo-root", default=None, metavar="DIR",
                        help="Codebase repo root (default: parent of target-dir).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be run without invoking workers/LLMs.")
    # Coding section.
    parser.add_argument("--initial-prompt", default=None, metavar="PATH",
                        help="[coding] Path to InitialPrompt.md (default: ./InitialPrompt.md).")
    parser.add_argument("--coding-target-dir", default=None, metavar="DIR",
                        help="[coding] Output directory (default: parent of --initial-prompt).")
    parser.add_argument("--claude", default="Claude1",
                        help="[coding] Claude account identifier (Claude1/Claude2).")
    parser.add_argument("--model", default=None,
                        help="[coding] Override Claude model for ALL Claude stages.")
    parser.add_argument("--local-model", default=None,
                        help="[coding] Override local model for ALL local stages.")
    parser.add_argument("--local-endpoint", default=None,
                        help="[coding] Override Ollama endpoint URL.")
    parser.add_argument("--local", action="store_true",
                        help="[coding] Use local Ollama for every stage.")
    parser.add_argument("--all-claude", action="store_true",
                        help="[coding] Use Claude Code for every stage.")
    parser.add_argument("--ultrathink", action="store_true",
                        help="[coding] Force 'ultrathink. ' prefix for Claude stages.")
    parser.add_argument("--no-ultrathink", action="store_true",
                        help="[coding] Disable 'ultrathink. ' prefix for Claude stages.")
    parser.add_argument("--from-stage", type=int, default=1,
                        help="[coding] Skip stages 0..N-1 (default: 1).")
    parser.add_argument("--skip-stage", type=int, nargs="*", default=(),
                        help="[coding] Stage numbers to skip entirely.")
    parser.add_argument("--restart", action="store_true",
                        help="[coding/debug] Ignore saved progress and start fresh.")
    parser.add_argument("--force", action="store_true",
                        help="[coding] Skip overwrite confirmation prompts.")
    parser.add_argument("--package-dir", default=None, metavar="DIR",
                        help="[coding Stage 5] Package directory for fix_imports.py "
                             "(default: src/nmon).")
    # Analysis section.
    parser.add_argument("--start-from", type=int, default=1, metavar="N",
                        help="[analysis] Skip subsections 1..N-1.")
    parser.add_argument("--skip-lsp", action="store_true",
                        help="[analysis] Skip one-time compile_commands + serena setup.")
    # Debug section.
    parser.add_argument("--debug-target-dir", default=None, metavar="DIR",
                        help="[debug] Source directory to analyse (e.g. src/nmon). "
                             "Required when section includes debug.")
    parser.add_argument("--test-dir", default="tests",
                        help="[debug] Test directory (default: tests).")
    parser.set_defaults(func=run)


def _coding_args(args: argparse.Namespace) -> argparse.Namespace:
    ns = copy.copy(args)
    ns.target_dir = args.coding_target_dir
    return ns


def _analysis_args(args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(
        repo_root=args.repo_root, dry_run=args.dry_run,
        start_from=args.start_from, skip_lsp=args.skip_lsp,
        func=analysis.run,
    )


def _debug_args(args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(
        repo_root=args.repo_root, dry_run=args.dry_run,
        target_dir=args.debug_target_dir, test_dir=args.test_dir,
        restart=args.restart, func=debug.run,
    )


def run(args: argparse.Namespace) -> int:
    ordered = SECTIONS[SECTIONS.index(args.from_section):]
    if "debug" in ordered and not args.debug_target_dir:
        cprint("ERROR: --debug-target-dir is required when debug section is included.",
               Color.RED)
        return 1

    banner(f"FULL PIPELINE - running: {' -> '.join(ordered)}", Color.GREEN)

    last_rc = 0
    for section in ordered:
        banner(f"SECTION: {section}", Color.CYAN)
        if section == "coding":
            rc = coding.run(_coding_args(args))
        elif section == "analysis":
            rc = analysis.run(_analysis_args(args))
        else:  # debug
            rc = debug.run(_debug_args(args))

        if rc != 0:
            last_rc = rc
            cprint(f"\nSection '{section}' failed (exit {rc}).", Color.RED)
            if not args.continue_on_error:
                cprint("  Stopping. Use --continue-on-error to proceed regardless.",
                       Color.RED)
                return rc
            cprint("  Continuing (--continue-on-error).", Color.YELLOW)

    banner("FULL PIPELINE COMPLETE", Color.GREEN)
    return last_rc
