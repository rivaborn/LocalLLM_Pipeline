"""Stages 0-3 — LLM-driven planning.

    0  Summarize existing Implemented Plans/ into Codebase Summary.md
    1  Improve InitialPrompt.md -> Implementation Planning Prompt.md
    2  Generate Architecture Plan.md (2a section list, 2b per-section)
    3  Generate aidercommands.md   (3a step list, 3b per-step)
"""
from __future__ import annotations

import argparse
import datetime
import re
from pathlib import Path

from ...progress import ProgressFile
from ...ui import Color, banner, check_cancel, cprint
from .fileops import (
    architecture_slice,
    codebase_summary_context,
    confirm_overwrite,
    get_implemented_plans,
    load_prompt,
)
from .router import invoke_stage


def stage0(repo_root: Path, codebase_summary: Path, args: argparse.Namespace,
           env: dict, planning_cfg: dict, progress: ProgressFile, mode: str) -> None:
    plans = get_implemented_plans(repo_root)
    if not plans:
        cprint("\n  No implemented plans found - skipping Stage 0 (Codebase Summary)", Color.BLUE)
        return

    banner("Stage 0 - Summarize Existing Codebase", Color.CYAN)
    cprint(f"  Found {len(plans)} implemented document(s):", Color.CYAN)
    for p in plans:
        size_kb = round(p.stat().st_size / 1024, 1)
        cprint(f"    {p.name}  ({size_kb} KB)", Color.CYAN)

    if not confirm_overwrite([codebase_summary], args):
        cprint("  Skipping Stage 0 (user declined overwrite)", Color.YELLOW)
        return

    concatenated = "\n\n---\n\n".join(
        f"### {p.name}\n\n{p.read_text(encoding='utf-8')}" for p in plans
    )
    prompt = load_prompt("stage0_summarize.md") + "\n\n" + concatenated

    result = invoke_stage(prompt, "0", args=args, env=env, planning_cfg=planning_cfg,
                          thinking_file=codebase_summary.with_suffix(".md.thinking.md"))
    codebase_summary.parent.mkdir(parents=True, exist_ok=True)
    codebase_summary.write_text(result, encoding="utf-8")
    cprint(f"  Saved to {codebase_summary.name}", Color.GREEN)
    if not args.dry_run:
        progress.save(0, mode=mode)


def stage1(repo_root: Path, target_dir: Path, initial: Path, args: argparse.Namespace,
           env: dict, planning_cfg: dict, progress: ProgressFile, mode: str) -> None:
    banner("Stage 1 - Improve Initial Prompt", Color.CYAN)
    impl_prompt = target_dir / "Implementation Planning Prompt.md"
    updates = target_dir / "PromptUpdates.md"
    if not initial.exists():
        cprint(f"  ERROR: {initial} not found (required by Stage 1)", Color.RED)
        raise SystemExit(1)
    if not confirm_overwrite([impl_prompt, updates], args):
        cprint("  Skipping Stage 1 (user declined overwrite)", Color.YELLOW)
        return

    prompt = load_prompt("stage1_improve_prompt.md") + "\n\n" + initial.read_text(encoding="utf-8")
    cprint("  Processing InitialPrompt.md...", Color.CYAN)

    result = invoke_stage(prompt, "1", args=args, env=env, planning_cfg=planning_cfg,
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


def stage2(repo_root: Path, target_dir: Path, arch_plan: Path,
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
    existing_ctx = codebase_summary_context(repo_root)

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
        if not confirm_overwrite([arch_plan], args):
            cprint("  Skipping Stage 2 (user declined overwrite)", Color.YELLOW)
            return
        cprint("  Stage 2a: Generating section plan...", Color.CYAN)
        prompt_2a = load_prompt("stage2a_section_plan.md") + "\n\n" + planning_content + "\n" + existing_ctx
        if args.dry_run:
            cprint("  [DRY RUN] Would generate section plan", Color.BLUE)
            return
        result = invoke_stage(
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

        tmpl = load_prompt("stage2b_section.md")
        prompt_2b = (
            tmpl.replace("SECTITLE", sec_title).replace("SECDESC", sec_desc)
            + "\n\n" + planning_content
            + (("\n" + existing_ctx) if existing_ctx else "")
        )
        sidecar = Path(str(arch_plan) + f".section_{sec_num}.thinking.md")
        result = invoke_stage(
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


def stage3(repo_root: Path, target_dir: Path, arch_plan: Path, aider_commands: Path,
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
    existing_ctx = codebase_summary_context(repo_root)
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
        if not confirm_overwrite([aider_commands], args):
            cprint("  Skipping Stage 3 (user declined overwrite)", Color.YELLOW)
            return
        cprint("  Stage 3a: Generating step plan...", Color.CYAN)
        head = load_prompt("stage3a_step_plan_head.md")
        tail = load_prompt("stage3a_step_plan_tail.md")
        prompt_3a = head + arch_content + "\n" + existing_ctx + tail
        if args.dry_run:
            cprint("  [DRY RUN] Would generate step plan", Color.BLUE)
            return
        result = invoke_stage(
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

        head = load_prompt("stage3b_step_head.md")
        tail = load_prompt("stage3b_step_tail.md")
        body_ctx = architecture_slice(arch_content, file_list) if use_slice else arch_content

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
        result = invoke_stage(
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
