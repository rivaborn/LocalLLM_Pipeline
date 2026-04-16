"""Architecture coding mode.

Ports Arch_Coding_Pipeline.ps1: four stages (0, 1, 2, 3) with sub-passes,
per-stage model routing between Claude and the local Ollama server,
incremental .progress tracking with resume-mid-stage, and direct-append
writing for Architecture Plan.md / aidercommands.md.

Stage defaults:
    0  sonnet  think=false  local_model=<default>
    1  sonnet  think=false  local_model=<default>        engine=claude in 'default' mode
    2a opus    think=true   local_model=<default>
    2b opus    think=true   local_model=<default>
    3a opus    think=true   local_model=qwen3-coder:30b  (coder override)
    3b sonnet  think=false  local_model=qwen3-coder:30b  (coder override)

Modes (mutually exclusive):
    default    -- stage 1 on Claude, all others local
    --local    -- every stage on local
    --all-claude -- every stage on Claude
"""
from __future__ import annotations

import argparse
import datetime
import os
import re
import subprocess
import sys
from pathlib import Path

from .. import config as cfg
from ..claude import ClaudeError, invoke_claude
from ..ollama import LLMError, invoke_local_llm
from ..progress import ProgressFile
from ..subprocess_runner import UserCancelled
from ..ui import Color, banner, check_cancel, cprint, setup_logging


_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"

STAGE_DEFAULTS: dict[str, dict] = {
    "0":  {"model": "sonnet", "think": False, "local_model": ""},
    "1":  {"model": "sonnet", "think": False, "local_model": ""},
    "2a": {"model": "opus",   "think": True,  "local_model": ""},
    "2b": {"model": "opus",   "think": True,  "local_model": ""},
    "3a": {"model": "opus",   "think": True,  "local_model": "qwen3-coder:30b"},
    "3b": {"model": "sonnet", "think": False, "local_model": "qwen3-coder:30b"},
}


# ───────────────────────── CLI ─────────────────────────

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
    parser.add_argument("--package-dir", default=None, metavar="DIR",
                        help="[Stage 5] Package directory for fix_imports.py "
                             "(default: fix_imports.py's own default, src/nmon).")
    parser.set_defaults(func=run)


# ───────────────────────── routing helpers ─────────────────────────

def _get_mode(args: argparse.Namespace) -> str:
    if args.all_claude:
        return "allclaude"
    if args.local:
        return "local"
    return "default"


def _get_engine(sub_stage: str, mode: str) -> str:
    if mode == "allclaude":
        return "claude"
    if mode == "local":
        return "local"
    return "claude" if sub_stage == "1" else "local"


def _think_prefix(sub_stage: str, args: argparse.Namespace, engine: str) -> str:
    """'ultrathink. ' prefix for Claude stages only; no-op for local engines
    (kept for parity with the PowerShell script's Get-StageThinkPrefix)."""
    if engine == "local":
        return ""
    use = STAGE_DEFAULTS[sub_stage]["think"]
    if args.ultrathink:
        use = True
    if args.no_ultrathink:
        use = False
    return "ultrathink. " if use else ""


def _resolve_local_model(sub_stage: str, user_supplied_local_model: str | None, fallback: str) -> str:
    if user_supplied_local_model:
        return user_supplied_local_model
    override = STAGE_DEFAULTS[sub_stage]["local_model"]
    return override or fallback


def _resolve_claude_model(sub_stage: str, user_override: str | None) -> str:
    if user_override:
        return user_override
    return STAGE_DEFAULTS[sub_stage]["model"]


# ───────────────────────── prompt execution ─────────────────────────

def _invoke_stage(
    prompt: str,
    sub_stage: str,
    *,
    args: argparse.Namespace,
    env: dict[str, str],
    planning_cfg: dict,
    thinking_file: Path | None = None,
) -> str:
    mode = _get_mode(args)
    engine = _get_engine(sub_stage, mode)
    think_pfx = _think_prefix(sub_stage, args, engine)
    full_prompt = think_pfx + prompt

    if engine == "claude":
        model = _resolve_claude_model(sub_stage, args.model)
        cprint(f"    [claude model={model}]", Color.BLUE)
        if args.dry_run:
            return f"[DRY RUN Claude output for stage {sub_stage}]"
        return invoke_claude(full_prompt, model=model, account=args.claude)

    # local engine
    local_model = _resolve_local_model(sub_stage, args.local_model, planning_cfg["model"])
    # Per-stage coder-model override disables thinking automatically (qwen3-coder
    # is not a reasoning model; sending think:true to it wastes budget).
    stage_think = planning_cfg["think"] and local_model == planning_cfg["model"]
    cprint(
        f"    [local: {local_model} @ {planning_cfg['endpoint']} "
        f"ctx={planning_cfg['num_ctx']} think={stage_think}]",
        Color.BLUE,
    )
    if args.dry_run:
        return f"[DRY RUN local output for stage {sub_stage}]"

    return invoke_local_llm(
        full_prompt,
        env=env,
        endpoint=planning_cfg["endpoint"],
        model=local_model,
        num_ctx=planning_cfg["num_ctx"],
        max_tokens=planning_cfg["max_tokens"],
        timeout=planning_cfg["timeout"],
        temperature=planning_cfg["temperature"],
        think=stage_think,
        thinking_file=thinking_file if planning_cfg["save_thinking"] else None,
    )


# ───────────────────────── file helpers ─────────────────────────

def _confirm_overwrite(paths: list[Path], args: argparse.Namespace) -> bool:
    if args.force or args.dry_run:
        return True
    existing = [p for p in paths if p.exists()]
    if not existing:
        return True
    cprint("\n  The following output files already exist:", Color.YELLOW)
    for p in existing:
        info = p.stat()
        size_kb = round(info.st_size / 1024, 1)
        mtime = datetime.datetime.fromtimestamp(info.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        cprint(f"    {p.name}  ({size_kb} KB, modified {mtime})", Color.YELLOW)
    try:
        answer = input("  Overwrite? [y/N]: ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


def _get_implemented_plans(repo_root: Path) -> list[Path]:
    impl_dir = repo_root / "Implemented Plans"
    if not impl_dir.is_dir():
        return []
    rx = re.compile(r"^(Architecture Plan|Bug Fix Changes) \d+\.md$")
    return sorted(
        [p for p in impl_dir.glob("*.md") if rx.match(p.name)],
        key=lambda p: p.stat().st_mtime,
    )


def _codebase_summary_context(repo_root: Path) -> str:
    summary = repo_root / "Implemented Plans" / "Codebase Summary.md"
    if not summary.exists():
        return ""
    content = summary.read_text(encoding="utf-8")
    return (
        "\n\n## Existing Codebase Context\n\n"
        "The following is a consolidated summary of all previously implemented architecture plans.\n"
        "The codebase already contains the files, modules, data models, and infrastructure described\n"
        "below. Your plan must build on this existing code — do not recreate or conflict with what\n"
        "already exists. Reuse existing modules, types, and patterns where appropriate.\n\n"
        + content
    )


_ALWAYS_INCLUDE_SECTIONS = (
    "Project Structure", "Data Model", "Data Pipeline",
    "Configuration", "Dependencies", "Build/Run", "Build ", "Testing",
)


def _architecture_slice(arch_content: str, files: list[str]) -> str:
    basenames = [Path(f.replace("/", "\\")).name for f in files if f]
    parts = re.split(r"(?m)(?=^##\s)", arch_content)
    keep_parts: list[str] = []
    for part in parts:
        m = re.match(r"(?m)^##\s+(.+?)\s*$", part)
        if not m:
            keep_parts.append(part)  # preamble
            continue
        heading = m.group(1).strip()
        keep = any(re.search(re.escape(a), heading) for a in _ALWAYS_INCLUDE_SECTIONS)
        if not keep:
            keep = any(re.search(re.escape(b), heading) for b in basenames)
        if keep:
            keep_parts.append(part)
    return "".join(keep_parts)


def _load_prompt(name: str) -> str:
    return (_PROMPT_DIR / name).read_text(encoding="utf-8")


# ───────────────────────── stages ─────────────────────────

def _stage0(repo_root: Path, codebase_summary: Path, args: argparse.Namespace,
            env: dict, planning_cfg: dict, progress: ProgressFile, mode: str) -> None:
    plans = _get_implemented_plans(repo_root)
    if not plans:
        cprint("\n  No implemented plans found - skipping Stage 0 (Codebase Summary)", Color.BLUE)
        return

    banner("Stage 0 - Summarize Existing Codebase", Color.CYAN)
    cprint(f"  Found {len(plans)} implemented document(s):", Color.CYAN)
    for p in plans:
        size_kb = round(p.stat().st_size / 1024, 1)
        cprint(f"    {p.name}  ({size_kb} KB)", Color.CYAN)

    if not _confirm_overwrite([codebase_summary], args):
        cprint("  Skipping Stage 0 (user declined overwrite)", Color.YELLOW)
        return

    concatenated = "\n\n---\n\n".join(
        f"### {p.name}\n\n{p.read_text(encoding='utf-8')}" for p in plans
    )
    prompt = _load_prompt("stage0_summarize.md") + "\n\n" + concatenated

    result = _invoke_stage(prompt, "0", args=args, env=env, planning_cfg=planning_cfg,
                           thinking_file=codebase_summary.with_suffix(".md.thinking.md"))
    codebase_summary.parent.mkdir(parents=True, exist_ok=True)
    codebase_summary.write_text(result, encoding="utf-8")
    cprint(f"  Saved to {codebase_summary.name}", Color.GREEN)
    if not args.dry_run:
        progress.save(0, mode=mode)


def _stage1(repo_root: Path, target_dir: Path, initial: Path, args: argparse.Namespace,
            env: dict, planning_cfg: dict, progress: ProgressFile, mode: str) -> None:
    banner("Stage 1 - Improve Initial Prompt", Color.CYAN)
    impl_prompt = target_dir / "Implementation Planning Prompt.md"
    updates = target_dir / "PromptUpdates.md"
    if not initial.exists():
        cprint(f"  ERROR: {initial} not found (required by Stage 1)", Color.RED)
        raise SystemExit(1)
    if not _confirm_overwrite([impl_prompt, updates], args):
        cprint("  Skipping Stage 1 (user declined overwrite)", Color.YELLOW)
        return

    prompt = _load_prompt("stage1_improve_prompt.md") + "\n\n" + initial.read_text(encoding="utf-8")
    cprint("  Processing InitialPrompt.md...", Color.CYAN)

    result = _invoke_stage(prompt, "1", args=args, env=env, planning_cfg=planning_cfg,
                           thinking_file=impl_prompt.with_suffix(".md.thinking.md"))
    if args.dry_run:
        cprint("  [DRY RUN] Would save improved prompt and PromptUpdates.md", Color.BLUE)
        return

    sep = "---PROMPT_UPDATES---"
    parts = result.split(sep, 1)
    impl_prompt.write_text(parts[0].strip(), encoding="utf-8")
    cprint(f"  Saved improved prompt to: {impl_prompt.name}", Color.GREEN)
    if len(parts) > 1:
        header = f"# Prompt Updates\n\nGenerated: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n\n"
        updates.write_text(header + parts[1].strip(), encoding="utf-8")
        cprint(f"  Saved critique to: {updates.name}", Color.GREEN)
    else:
        cprint("  Warning: No PROMPT_UPDATES separator found in output", Color.YELLOW)
    progress.save(1, mode=mode)


def _stage2(repo_root: Path, target_dir: Path, arch_plan: Path,
            args: argparse.Namespace, env: dict, planning_cfg: dict,
            progress: ProgressFile, mode: str) -> None:
    banner("Stage 2 - Generate Architecture Plan", Color.CYAN)
    impl_prompt = target_dir / "Implementation Planning Prompt.md"
    section_plan_file = target_dir / ".section_plan.md"
    if not impl_prompt.exists():
        if args.dry_run:
            cprint(f"  [DRY RUN] Would generate Architecture Plan.md from "
                   f"{impl_prompt.name} (not yet present)", Color.BLUE)
            return
        cprint(f"  ERROR: {impl_prompt} not found", Color.RED)
        raise SystemExit(1)

    planning_content = impl_prompt.read_text(encoding="utf-8")
    existing_ctx = _codebase_summary_context(repo_root)

    state = progress.read()
    resume_section = state.sub_step if (state.last_completed == 1 and section_plan_file.exists()) else -1
    if resume_section is not None and resume_section >= 0:
        cprint(
            f"  Resuming Stage 2 from section {resume_section + 1} "
            f"(sections 1-{resume_section} completed)",
            Color.YELLOW,
        )
    else:
        resume_section = -1

    # ── Stage 2a ──
    if resume_section < 0:
        if not _confirm_overwrite([arch_plan], args):
            cprint("  Skipping Stage 2 (user declined overwrite)", Color.YELLOW)
            return
        cprint("  Stage 2a: Generating section plan...", Color.CYAN)
        prompt_2a = _load_prompt("stage2a_section_plan.md") + "\n\n" + planning_content + "\n" + existing_ctx
        if args.dry_run:
            cprint("  [DRY RUN] Would generate section plan", Color.BLUE)
            return
        result = _invoke_stage(
            prompt_2a, "2a", args=args, env=env, planning_cfg=planning_cfg,
            thinking_file=Path(str(section_plan_file) + ".thinking.md"),
        )
        section_plan_file.write_text(result, encoding="utf-8")
        cprint(f"  Section plan saved to {section_plan_file.name}", Color.GREEN)
        arch_plan.write_text("# Architecture Plan\n\n", encoding="utf-8")

    # ── Stage 2b ──
    if args.dry_run:
        cprint("  [DRY RUN] Would generate individual sections", Color.BLUE)
        return

    lines = [ln for ln in section_plan_file.read_text(encoding="utf-8").splitlines()
             if re.match(r"^\s*SECTION\s+\d+", ln)]
    if not lines:
        cprint(
            f"ERROR: Stage 2a produced no parseable SECTION lines.\n"
            f"  Inspect {section_plan_file} -- the model likely ignored the "
            f"'SECTION N | Title | Description' format.",
            Color.RED,
        )
        raise SystemExit(1)
    total = len(lines)
    cprint(f"  Stage 2b: Generating {total} section(s)...", Color.CYAN)

    for line in lines:
        check_cancel()
        m = re.match(r"^\s*SECTION\s+(\d+)\s*\|\s*(.+?)\s*\|\s*(.+)\s*$", line)
        if not m:
            cprint(f"  Warning: could not parse section line: {line}", Color.YELLOW)
            continue
        sec_num = int(m.group(1))
        sec_title = m.group(2).strip()
        sec_desc = m.group(3).strip()
        if sec_num <= resume_section:
            cprint(f"    Section {sec_num}/{total} - {sec_title} [already done]", Color.BLUE)
            continue
        cprint(f"    Section {sec_num}/{total} - {sec_title}", Color.CYAN)

        tmpl = _load_prompt("stage2b_section.md")
        prompt_2b = (
            tmpl.replace("SECTITLE", sec_title).replace("SECDESC", sec_desc)
            + "\n\n" + planning_content
            + (("\n" + existing_ctx) if existing_ctx else "")
        )
        sidecar = Path(str(arch_plan) + f".section_{sec_num}.thinking.md")
        result = _invoke_stage(
            prompt_2b, "2b", args=args, env=env, planning_cfg=planning_cfg,
            thinking_file=sidecar,
        )
        with arch_plan.open("a", encoding="utf-8") as fh:
            fh.write(f"\n{result.strip()}\n")
        cprint(f"    Section {sec_num}/{total} - done -> appended to {arch_plan.name}",
               Color.GREEN)
        progress.save(1, sub_step=sec_num, mode=mode)

    if section_plan_file.exists():
        section_plan_file.unlink()
    progress.save(2, mode=mode)
    cprint(f"  All {total} sections generated in {arch_plan.name}", Color.GREEN)


def _stage3(repo_root: Path, target_dir: Path, arch_plan: Path, aider_commands: Path,
            args: argparse.Namespace, env: dict, planning_cfg: dict,
            progress: ProgressFile, mode: str) -> None:
    banner("Stage 3 - Generate Aider Commands", Color.CYAN)
    if not arch_plan.exists():
        if args.dry_run:
            cprint(f"  [DRY RUN] Would generate aidercommands.md from "
                   f"{arch_plan.name} (not yet present)", Color.BLUE)
            return
        cprint(f"  ERROR: {arch_plan} not found", Color.RED)
        raise SystemExit(1)
    arch_content = arch_plan.read_text(encoding="utf-8")
    existing_ctx = _codebase_summary_context(repo_root)
    step_plan_file = target_dir / ".step_plan.md"

    state = progress.read()
    resume_step = state.sub_step if (state.last_completed == 2 and step_plan_file.exists()) else -1
    if resume_step is not None and resume_step >= 0:
        cprint(
            f"  Resuming Stage 3 from step {resume_step + 1} "
            f"(steps 1-{resume_step} completed)",
            Color.YELLOW,
        )
    else:
        resume_step = -1

    # ── Stage 3a ──
    if resume_step < 0:
        if not _confirm_overwrite([aider_commands], args):
            cprint("  Skipping Stage 3 (user declined overwrite)", Color.YELLOW)
            return
        cprint("  Stage 3a: Generating step plan...", Color.CYAN)
        head = _load_prompt("stage3a_step_plan_head.md")
        tail = _load_prompt("stage3a_step_plan_tail.md")
        prompt_3a = head + arch_content + "\n" + existing_ctx + tail
        if args.dry_run:
            cprint("  [DRY RUN] Would generate step plan", Color.BLUE)
            return
        result = _invoke_stage(
            prompt_3a, "3a", args=args, env=env, planning_cfg=planning_cfg,
            thinking_file=Path(str(step_plan_file) + ".thinking.md"),
        )
        step_plan_file.write_text(result, encoding="utf-8")
        cprint(f"  Step plan saved to {step_plan_file.name}", Color.GREEN)
        aider_commands.write_text(
            "# Implementation - One File Per Session\n\n"
            "Each step is a separate aider invocation. The prompt is self-contained -\n"
            "do NOT --read Architecture Plan.md, it is too large. Run each command,\n"
            "wait for it to finish, then move to the next step.\n\n---\n\n",
            encoding="utf-8",
        )

    # ── Stage 3b ──
    if args.dry_run:
        cprint("  [DRY RUN] Would generate individual steps", Color.BLUE)
        return

    lines = [ln for ln in step_plan_file.read_text(encoding="utf-8").splitlines()
             if re.match(r"^\s*STEP\s+\d+", ln)]
    if not lines:
        cprint(
            f"ERROR: Stage 3a produced no parseable STEP lines.\n"
            f"  Inspect {step_plan_file} -- the model likely ignored the "
            f"'STEP N | Title | files' format.\n"
            "  Re-run Stage 3 after deleting that file, or edit it to match.",
            Color.RED,
        )
        raise SystemExit(1)
    total = len(lines)
    cprint(f"  Stage 3b: Generating {total} step(s)...", Color.CYAN)

    # Legacy Arch_Coding_Pipeline.ps1 sliced only when --local was passed.
    # Keep that behaviour so default-mode Stage 3b sees the full architecture
    # plan (may overflow context on very large plans, but matches the old
    # pipeline's output exactly).
    use_slice = (mode == "local")

    for line in lines:
        check_cancel()
        m = re.match(r"^\s*STEP\s+(\d+)\s*\|\s*(.+?)\s*\|\s*(.+)\s*$", line)
        if not m:
            cprint(f"  Warning: could not parse step line: {line}", Color.YELLOW)
            continue
        step_num = int(m.group(1))
        step_title = m.group(2).strip()
        step_files = m.group(3).strip()
        if step_num <= resume_step:
            cprint(f"    Step {step_num}/{total} - {step_title} [already done]", Color.BLUE)
            continue
        cprint(f"    Step {step_num}/{total} - {step_title}", Color.CYAN)

        file_list = [f.strip() for f in step_files.split(",")]
        aider_files = " ".join(file_list)

        head = _load_prompt("stage3b_step_head.md")
        tail = _load_prompt("stage3b_step_tail.md")
        body_ctx = _architecture_slice(arch_content, file_list) if use_slice else arch_content

        prompt_3b = (
            head
            .replace("STEPNUM", str(step_num))
            .replace("STEPTITLE", step_title)
            .replace("AIDERFILES", aider_files)
            + "\n\n" + body_ctx
            + (("\n" + existing_ctx) if existing_ctx else "")
            + tail
            .replace("STEPNUM", str(step_num))
            .replace("STEPTITLE", step_title)
            .replace("AIDERFILES", aider_files)
        )

        sidecar = target_dir / f".step_{step_num}.thinking.md"
        result = _invoke_stage(
            prompt_3b, "3b", args=args, env=env, planning_cfg=planning_cfg,
            thinking_file=sidecar,
        )
        with aider_commands.open("a", encoding="utf-8") as fh:
            fh.write(f"\n{result.strip()}\n\n---\n")
        cprint(f"    Step {step_num}/{total} - done", Color.GREEN)
        progress.save(2, sub_step=step_num, mode=mode)

    if step_plan_file.exists():
        step_plan_file.unlink()
    progress.save(3, mode=mode)
    cprint(f"  All {total} steps generated in {aider_commands.name}", Color.GREEN)


# ───────────────────────── execution stages ─────────────────────────

def _stage4_run_aider(repo_root: Path, aider_commands: Path,
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


def _detect_package_dir(repo_root: Path) -> str | None:
    _SKIP = {"tests", "test", "venv", ".venv", "architecture", "LocalLLMCodePrompts",
             "build", "dist", "__pycache__", ".git", "Implemented Plans"}
    # src-layout first.
    src = repo_root / "src"
    if src.is_dir():
        for child in sorted(src.iterdir()):
            if child.is_dir() and (child / "__init__.py").exists():
                return f"src/{child.name}"
    # Flat layout.
    for child in sorted(repo_root.iterdir()):
        if not child.is_dir() or child.name in _SKIP or child.name.startswith("."):
            continue
        if (child / "__init__.py").exists():
            return child.name
    return None


def _stage5_fix_imports(repo_root: Path, args: argparse.Namespace,
                        progress: ProgressFile, mode: str) -> None:
    banner("Stage 5 - Fix Imports (post-gen repair)", Color.CYAN)
    script = cfg.toolkit_root() / "LocalLLMCoding" / "fix_imports.py"
    pkg = args.package_dir or _detect_package_dir(repo_root)
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


# ───────────────────────── entry point ─────────────────────────

def run(args: argparse.Namespace) -> int:
    if args.local and args.all_claude:
        cprint("ERROR: --local and --all-claude are mutually exclusive.", Color.RED)
        return 1

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
            initial_prompt = cwd_prompt.resolve()  # falls through to Stage 1's error
    if args.target_dir:
        target_dir = Path(args.target_dir).resolve()
    else:
        target_dir = initial_prompt.parent
    if args.repo_root:
        repo_root = Path(args.repo_root).resolve()
    else:
        repo_root = target_dir.parent
    if not target_dir.is_dir():
        cprint(f"ERROR: target directory not found: {target_dir}", Color.RED)
        return 1
    if not repo_root.is_dir():
        cprint(f"ERROR: repo root not found: {repo_root}", Color.RED)
        return 1

    toolkit_log = cfg.toolkit_root() / "LocalLLMCoding"
    toolkit_log.mkdir(exist_ok=True)
    logger = setup_logging(toolkit_log / "coding_pipeline.log")
    _ = logger  # currently unused outside setup; kept for parity

    env = cfg.load_env()

    planning_cfg = {
        "model":        env.get("LLM_PLANNING_MODEL", "gemma4:26b"),
        "num_ctx":      int(env.get("LLM_PLANNING_NUM_CTX", "65536")),
        "max_tokens":   int(env.get("LLM_PLANNING_MAX_TOKENS", "49152")),
        "timeout":      int(env.get("LLM_PLANNING_TIMEOUT", env.get("LLM_TIMEOUT", "1200"))),
        "temperature":  float(env.get("LLM_TEMPERATURE", "0.1")),
        "think":        env.get("LLM_THINK", "false").lower() in ("1", "true", "yes", "on"),
        "save_thinking": env.get("LLM_SAVE_THINKING", "false").lower() in ("1", "true", "yes", "on"),
        "endpoint":     args.local_endpoint or env.get("LLM_ENDPOINT") or f"http://{env.get('LLM_HOST', '192.168.1.126')}:{env.get('LLM_PORT', '11434')}",
    }

    mode = _get_mode(args)
    progress = ProgressFile(target_dir / ".progress")
    state = progress.read()

    # Mode-mismatch guard.
    if not args.restart and state.mode and state.mode != mode:
        cprint(
            f"ERROR: saved progress used mode '{state.mode}' but current run uses '{mode}'.",
            Color.RED,
        )
        hint = {
            "allclaude": "Re-run with --all-claude to resume",
            "local": "Re-run with --local to resume",
            "default": "Re-run with no mode flags (the default) to resume",
        }.get(state.mode, "Use --restart to start over")
        cprint(f"  {hint}, or use --restart to start over.", Color.RED)
        return 1

    if args.restart and state.last_completed >= 0:
        cprint(
            f"Restarting (ignoring saved progress through stage {state.last_completed})",
            Color.YELLOW,
        )
        progress.clear()
        state = progress.read()

    from_stage = args.from_stage
    if state.last_completed >= 0 and args.from_stage == 1 and not args.restart:
        resume_from = state.last_completed + 1
        if resume_from <= 5:
            from_stage = resume_from
            cprint(
                f"Resuming from Stage {from_stage} "
                f"(stages 0-{state.last_completed} completed previously)",
                Color.YELLOW,
            )
            cprint("  Use --restart to start over", Color.BLUE)
        else:
            cprint(
                "All stages were completed previously. Use --restart to run again.",
                Color.YELLOW,
            )
            return 0

    cprint(f"Repo root:        {repo_root}", Color.CYAN)
    cprint(f"Target directory: {target_dir}", Color.CYAN)
    cprint(f"Initial prompt:   {initial_prompt}", Color.CYAN)
    cprint(f"Claude account:   {args.claude}", Color.CYAN)
    cprint(f"Mode:             {mode}", Color.CYAN)
    if mode != "allclaude":
        cprint(f"  Local endpoint: {planning_cfg['endpoint']}", Color.CYAN)
        cprint(f"  Local model:    {planning_cfg['model']} (num_ctx={planning_cfg['num_ctx']})", Color.CYAN)

    arch_plan = target_dir / "Architecture Plan.md"
    aider_commands = target_dir / "aidercommands.md"
    codebase_summary = repo_root / "Implemented Plans" / "Codebase Summary.md"

    try:
        # Stage 0 runs whenever Implemented Plans/ has content, independent of
        # --from-stage (legacy Arch_Coding_Pipeline.ps1 behaved the same way --
        # Stage 0 is a prerequisite context-gathering step, not a numbered stage).
        if 0 not in args.skip_stage:
            _stage0(repo_root, codebase_summary, args, env, planning_cfg, progress, mode)

        if from_stage <= 1 and 1 not in args.skip_stage:
            _stage1(repo_root, target_dir, initial_prompt, args, env, planning_cfg, progress, mode)
        else:
            cprint("\n  Skipping Stage 1 (Improve Initial Prompt)", Color.BLUE)

        if from_stage <= 2 and 2 not in args.skip_stage:
            _stage2(repo_root, target_dir, arch_plan, args, env, planning_cfg, progress, mode)
        else:
            cprint("\n  Skipping Stage 2 (Generate Architecture Plan)", Color.BLUE)

        if from_stage <= 3 and 3 not in args.skip_stage:
            _stage3(repo_root, target_dir, arch_plan, aider_commands,
                    args, env, planning_cfg, progress, mode)
        else:
            cprint("\n  Skipping Stage 3 (Generate Aider Commands)", Color.BLUE)

        if from_stage <= 4 and 4 not in args.skip_stage:
            _stage4_run_aider(repo_root, aider_commands, args, progress, mode)
        else:
            cprint("\n  Skipping Stage 4 (Run Aider)", Color.BLUE)

        if from_stage <= 5 and 5 not in args.skip_stage:
            _stage5_fix_imports(repo_root, args, progress, mode)
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
