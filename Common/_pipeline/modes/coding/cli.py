"""argparse registration + top-level run() for the coding mode.

Kept as a thin coordinator: path resolution, planning_cfg construction,
mode-mismatch + resume logic, then dispatches to stages_llm / stages_exec.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from ... import config as cfg
from ...claude import ClaudeError
from ...ollama import LLMError
from ...progress import ProgressFile
from ...subprocess_runner import UserCancelled
from ...ui import Color, banner, cprint, setup_logging, stage_log
from .router import describe_models, get_mode
from .stages_exec import stage4_run_aider, stage5_fix_imports
from .stages_llm import stage0, stage1, stage2, stage2c_review, stage3, stage3c_review


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "coding",
        help="Planning pipeline (stages 0-3) producing Architecture Plan.md and aidercommands.md.",
    )
    parser.add_argument("--initial-prompt", default=None, metavar="PATH",
                        help="Path to InitialPrompt.md (default: ./InitialPrompt.md). "
                             "Its parent directory becomes target-dir; repo-root defaults "
                             "to one level above that.")
    parser.add_argument("--repo-root", default=None, metavar="DIR",
                        help="Codebase repo root (default: parent of target-dir).")
    parser.add_argument("--target-dir", default=None, metavar="DIR",
                        help="Output directory (default: parent of --initial-prompt).")
    parser.add_argument("--claude", default="Claude1",
                        help="Claude account identifier (Claude1/Claude2).")
    parser.add_argument("--model", default=None,
                        help="Override Claude model (sonnet/opus/explicit tag) for ALL Claude stages.")
    parser.add_argument("--local-model", default=None,
                        help="Override local model for ALL local stages.")
    parser.add_argument("--local-endpoint", default=None,
                        help="Override Ollama endpoint URL.")
    parser.add_argument("--local", action="store_true",
                        help="Use the local Ollama server for every stage (including Stage 1).")
    parser.add_argument("--all-claude", action="store_true",
                        help="Use Claude Code for every stage.")
    parser.add_argument("--ultrathink", action="store_true",
                        help="Force 'ultrathink. ' prefix for ALL Claude stages.")
    parser.add_argument("--no-ultrathink", action="store_true",
                        help="Disable 'ultrathink. ' prefix for ALL Claude stages.")
    parser.add_argument("--from-stage", type=int, default=1,
                        help="Skip stages 0..N-1 (default: 1).")
    parser.add_argument("--skip-stage", type=int, nargs="*", default=(),
                        help="Stage numbers to skip entirely.")
    parser.add_argument("--restart", action="store_true",
                        help="Ignore saved progress and start fresh.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print prompts/commands without running them.")
    parser.add_argument("--force", action="store_true",
                        help="Skip overwrite confirmation prompts.")
    parser.add_argument("--review", action="store_true",
                        help="Run Claude review + auto-fix at TWO checkpoints: "
                             "(Stage 2c) after Architecture Plan.md is "
                             "generated, audit vs. Implementation Planning "
                             "Prompt.md and patch drift (missing Design "
                             "Decisions, duplicate symbols, signature/import "
                             "drift, stub bodies, phantom modules, etc.); "
                             "(Stage 3c) after aidercommands.md is generated, "
                             "audit + patch the step-level equivalents. Both "
                             "stages BLOCK the next generation stage on any "
                             "remaining blocking findings. Pre-patch snapshots "
                             "saved to .bak files; audit logs to .review.md "
                             "files in target_dir. NOTE: `--skip-stage N` also "
                             "skips the corresponding review (2 skips 2c; 3 "
                             "skips 3c) — use `--from-stage N --review` to "
                             "audit existing artefacts without regenerating.")
    parser.add_argument("--package-dir", default=None, metavar="DIR",
                        help="[Stage 5] Package directory for fix_imports.py "
                             "(default: src/<--package-name> if given, else autodetect).")
    parser.add_argument("--package-name", default=None, metavar="NAME",
                        help="Python package name (e.g. 'nmon2'). When set, "
                             "every LLM stage is told to place source files under "
                             "src/<NAME>/ and Stage 5 defaults --package-dir to "
                             "src/<NAME>. Without this flag the LLM picks a name "
                             "from context, which can drift across runs.")
    parser.set_defaults(func=run)


def _resolve_paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    """Return (initial_prompt, target_dir, repo_root)."""
    if args.initial_prompt:
        initial_prompt = Path(args.initial_prompt).resolve()
    else:
        cwd_prompt = Path.cwd() / "InitialPrompt.md"
        nested_prompt = Path.cwd() / "LocalLLMCodePrompts" / "InitialPrompt.md"
        if cwd_prompt.exists():
            initial_prompt = cwd_prompt.resolve()
        elif nested_prompt.exists():
            initial_prompt = nested_prompt.resolve()
        else:
            initial_prompt = cwd_prompt.resolve()  # Stage 1 will surface the error
    target_dir = Path(args.target_dir).resolve() if args.target_dir else initial_prompt.parent
    repo_root = Path(args.repo_root).resolve() if args.repo_root else target_dir.parent
    return initial_prompt, target_dir, repo_root


def _build_planning_cfg(env: dict[str, str], args: argparse.Namespace) -> dict:
    return {
        "model":         cfg.resolve_model(env, "LLM_PLANNING_MODEL", "gemma4:26b"),
        "num_ctx":       int(env.get("LLM_PLANNING_NUM_CTX", "65536")),
        "max_tokens":    int(env.get("LLM_PLANNING_MAX_TOKENS", "49152")),
        "timeout":       int(env.get("LLM_PLANNING_TIMEOUT", env.get("LLM_TIMEOUT", "1200"))),
        "temperature":   float(env.get("LLM_TEMPERATURE", "0.1")),
        "think":         env.get("LLM_THINK", "false").lower() in ("1", "true", "yes", "on"),
        "save_thinking": env.get("LLM_SAVE_THINKING", "false").lower() in ("1", "true", "yes", "on"),
        "endpoint":      cfg.resolve_ollama_endpoint(env, explicit=args.local_endpoint),
    }


def _apply_progress(progress: ProgressFile, args: argparse.Namespace, mode: str) -> int | None:
    """Handle mode-mismatch / restart / resume. Returns the stage number
    to start from, or None if the pipeline should exit early."""
    state = progress.read()

    if not args.restart and state.mode and state.mode != mode:
        cprint(
            f"ERROR: saved progress used mode '{state.mode}' but current run uses '{mode}'.",
            Color.RED,
        )
        hint = {
            "allclaude": "Re-run with --all-claude to resume",
            "local":     "Re-run with --local to resume",
            "default":   "Re-run with no mode flags (the default) to resume",
        }.get(state.mode, "Use --restart to start over")
        cprint(f"  {hint}, or use --restart to start over.", Color.RED)
        return None

    if args.restart and state.last_completed >= 0:
        cprint(f"Restarting (ignoring saved progress through stage {state.last_completed})",
               Color.YELLOW)
        progress.clear()
        state = progress.read()

    from_stage = args.from_stage
    if state.last_completed >= 0 and args.from_stage == 1 and not args.restart:
        resume_from = state.last_completed + 1
        if resume_from <= 5:
            from_stage = resume_from
            cprint(f"Resuming from Stage {from_stage} "
                   f"(stages 0-{state.last_completed} completed previously)", Color.YELLOW)
            cprint("  Use --restart to start over", Color.BLUE)
        else:
            cprint("All stages were completed previously. Use --restart to run again.",
                   Color.YELLOW)
            return -1  # sentinel for "finished, exit 0"
    return from_stage


def run(args: argparse.Namespace) -> int:
    if args.local and args.all_claude:
        cprint("ERROR: --local and --all-claude are mutually exclusive.", Color.RED)
        return 1

    initial_prompt, target_dir, repo_root = _resolve_paths(args)
    if not target_dir.is_dir():
        cprint(f"ERROR: target directory not found: {target_dir}", Color.RED)
        return 1
    if not repo_root.is_dir():
        cprint(f"ERROR: repo root not found: {repo_root}", Color.RED)
        return 1

    toolkit_log = cfg.toolkit_root() / "LocalLLMCoding"
    toolkit_log.mkdir(exist_ok=True)
    setup_logging(toolkit_log / "coding_pipeline.log")

    env = cfg.load_env()
    planning_cfg = _build_planning_cfg(env, args)

    mode = get_mode(args)
    progress = ProgressFile(target_dir / ".progress")
    from_stage = _apply_progress(progress, args, mode)
    if from_stage is None:
        return 1
    if from_stage == -1:
        return 0

    cprint(f"Repo root:        {repo_root}", Color.CYAN)
    cprint(f"Target directory: {target_dir}", Color.CYAN)
    cprint(f"Initial prompt:   {initial_prompt}", Color.CYAN)
    cprint(f"Claude account:   {args.claude}", Color.CYAN)
    cprint(f"Mode:             {mode}", Color.CYAN)
    if mode != "allclaude":
        cprint(f"  Local endpoint: {planning_cfg['endpoint']}", Color.CYAN)
        cprint(f"  Local model:    {planning_cfg['model']} (num_ctx={planning_cfg['num_ctx']})", Color.CYAN)

    # Per-stage model table — previews which engine/model every stage that
    # will run in this invocation uses, before any LLM calls are made.
    model_rows = describe_models(args, env, planning_cfg)
    # Column widths derived from the longest cell in each column so the
    # table renders cleanly regardless of model tags / statuses.
    widths = {
        "stage":  max(len("Stage"),  max(len(r["stage"])  for r in model_rows)),
        "role":   max(len("Role"),   max(len(r["role"])   for r in model_rows)),
        "engine": max(len("Engine"), max(len(r["engine"]) for r in model_rows)),
        "model":  max(len("Model"),  max(len(r["model"])  for r in model_rows)),
        "think":  max(len("Think"),  max(len(r["think"])  for r in model_rows)),
    }
    header = (f"  {'Stage':<{widths['stage']}}  {'Role':<{widths['role']}}  "
              f"{'Engine':<{widths['engine']}}  {'Model':<{widths['model']}}  "
              f"{'Think':<{widths['think']}}  Status")
    divider = "  " + "-" * (len(header) - 2 + 8)  # leave room for Status column
    cprint("\n  Models for this run:", Color.CYAN)
    cprint(divider, Color.CYAN)
    cprint(header, Color.CYAN)
    cprint(divider, Color.CYAN)
    for r in model_rows:
        color = Color.GREEN if r["status"] == "will run" else Color.YELLOW
        line = (f"  {r['stage']:<{widths['stage']}}  {r['role']:<{widths['role']}}  "
                f"{r['engine']:<{widths['engine']}}  {r['model']:<{widths['model']}}  "
                f"{r['think']:<{widths['think']}}  {r['status']}")
        cprint(line, color)
    cprint(divider, Color.CYAN)

    arch_plan = target_dir / "Architecture Plan.md"
    aider_commands = target_dir / "aidercommands.md"
    impl_prompt = target_dir / "Implementation Planning Prompt.md"
    codebase_summary = repo_root / "Implemented Plans" / "Codebase Summary.md"
    skip = set(args.skip_stage)

    try:
        # Stage 0 runs whenever Implemented Plans/ has content, independent
        # of --from-stage (context-gathering prerequisite, not a numbered stage).
        if 0 not in skip:
            with stage_log(repo_root, "Stage 0"):
                stage0(repo_root, codebase_summary, args, env, planning_cfg, progress, mode)

        if from_stage <= 1 and 1 not in skip:
            with stage_log(repo_root, "Stage 1"):
                stage1(repo_root, target_dir, initial_prompt, args, env, planning_cfg, progress, mode)
        else:
            cprint("\n  Skipping Stage 1 (Improve Initial Prompt)", Color.BLUE)

        if from_stage <= 2 and 2 not in skip:
            with stage_log(repo_root, "Stage 2"):
                stage2(repo_root, target_dir, arch_plan, args, env, planning_cfg, progress, mode)
        else:
            cprint("\n  Skipping Stage 2 (Generate Architecture Plan)", Color.BLUE)

        # Stage 2c review runs only when Stage 2 is not in --skip-stage
        # (skipping Stage 2 also skips its review). It does run when Stage 2
        # is above --from-stage — that's the "audit existing Arch Plan" case.
        if args.review and 2 not in skip:
            with stage_log(repo_root, "Stage 2c"):
                if not stage2c_review(repo_root, target_dir, arch_plan,
                                       impl_prompt, args, env, planning_cfg):
                    cprint(
                        "  Stage 3 blocked by Architecture Plan review. "
                        "Patch the flagged sections (or restore from "
                        "'Architecture Plan.md.bak') and re-run.",
                        Color.RED + Color.BOLD,
                    )
                    return 1

        if from_stage <= 3 and 3 not in skip:
            with stage_log(repo_root, "Stage 3"):
                stage3(repo_root, target_dir, arch_plan, aider_commands,
                       args, env, planning_cfg, progress, mode)
        else:
            cprint("\n  Skipping Stage 3 (Generate Aider Commands)", Color.BLUE)

        # Same gating as Stage 2c: skipping Stage 3 also skips its review.
        if args.review and 3 not in skip:
            with stage_log(repo_root, "Stage 3c"):
                if not stage3c_review(repo_root, target_dir, arch_plan,
                                       aider_commands, args, env, planning_cfg):
                    cprint(
                        "  Stage 4 blocked by review. Patch the flagged "
                        "steps and re-run (with or without --review).",
                        Color.RED + Color.BOLD,
                    )
                    return 1

        if from_stage <= 4 and 4 not in skip:
            with stage_log(repo_root, "Stage 4"):
                stage4_run_aider(repo_root, aider_commands, args, progress, mode)
        else:
            cprint("\n  Skipping Stage 4 (Run Aider)", Color.BLUE)

        if from_stage <= 5 and 5 not in skip:
            with stage_log(repo_root, "Stage 5"):
                stage5_fix_imports(repo_root, args, progress, mode)
        else:
            cprint("\n  Skipping Stage 5 (Fix Imports)", Color.BLUE)

    except UserCancelled:
        cprint("[Ctrl+Q] Cancelled. Progress saved.", Color.YELLOW)
        return 130
    except (LLMError, ClaudeError) as exc:
        cprint(f"PIPELINE FAILED: {exc}", Color.RED + Color.BOLD)
        return 1
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        cprint(f"Unexpected error: {exc}", Color.RED + Color.BOLD)
        return 2

    if not args.dry_run:
        progress.clear()
    banner("Pipeline complete.", Color.GREEN)
    return 0
