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

from ...claude import ClaudeError
from ...progress import ProgressFile
from ...ui import Color, banner, check_cancel, cprint
from .fileops import (
    architecture_slice,
    codebase_summary_context,
    confirm_overwrite,
    get_implemented_plans,
    is_pipeline_output_only_step,
    load_prompt,
    sanitize_arch_context,
)
from .router import invoke_stage


def _canned_init_py_section(title: str) -> str | None:
    """Return a canned Stage 2b output for `__init__.py` module sections, or
    None if the section title isn't an `__init__.py`.

    `__init__.py` files are package markers / re-export lists — they own no
    classes, functions, or module-level constants. Asking a thinking local
    model (Stage 2b defaults `think=true`) to produce pseudocode for them
    burns ~1 hour of inference time per section with the full `num_predict`
    budget and returns trivial output. The canned stub below honours every
    Stage 2b rule (canonical heading, Imports bullet, Testing strategy with
    Test file line) while taking milliseconds."""
    m = re.search(r"(src/[\w./]*__init__\.py)", title)
    if not m:
        return None
    path = m.group(1)
    return (
        f"## Module: {path}\n\n"
        f"Standard Python package-marker file. Owns no classes, functions, or\n"
        f"module-level constants — its role is to make the containing directory\n"
        f"importable as a package and (optionally) shorten import paths by\n"
        f"re-exporting selected symbols from sibling modules.\n\n"
        f"**Imports:** no intra-project imports beyond any re-exports. If re-exports\n"
        f"are kept, their canonical source is the sibling module section that\n"
        f"owns each symbol (see other `## Module: src/...` sections).\n\n"
        f"**Re-exports:** optional, determined at implementation time. May be empty.\n\n"
        f"### Testing strategy\n\n"
        f"Test file: none — a `__init__.py` with no logic has no behaviour to\n"
        f"assert beyond importability, which is covered implicitly by every\n"
        f"sibling module's test that imports from this package.\n"
    )


def _is_test_step(file_list: list[str]) -> bool:
    """True when every file in the step is under tests/ or named test_*.py."""
    if not file_list:
        return False
    for f in file_list:
        norm = f.replace("\\", "/")
        base = norm.rsplit("/", 1)[-1]
        if not (norm.startswith("tests/") or "/tests/" in norm or base.startswith("test_")):
            return False
    return True


_STUB_PATTERNS = (
    re.compile(r"#\s*Placeholder\b", re.IGNORECASE),
    re.compile(r":\s*\n\s+pass\b"),          # def foo(...): \n    pass
    re.compile(r":\s*\n\s+\.\.\."),          # def foo(...): \n    ...
)


_VERDICT_RE = re.compile(r"^VERDICT:\s*(PASS|BLOCK)\b", re.MULTILINE)

# Claude CLI error banners (rate limits, usage caps) get captured into the
# streaming review file when the CLI exits mid-audit. They are NOT real
# audit content and must be stripped before the resume logic treats the
# file as "partial prior output" — otherwise the next prompt feeds the
# banner back to Claude as if it were its own unfinished work.
_CLAUDE_ERROR_BANNER_RE = re.compile(
    r"^(?:"
    r"You[\u2019']ve hit your (?:usage )?limit.*"
    r"|Claude (?:AI )?usage limit reached.*"
    r"|API Error.*"
    r").*$",
    re.MULTILINE | re.IGNORECASE,
)


def _strip_claude_error_banners(text: str) -> str:
    return _CLAUDE_ERROR_BANNER_RE.sub("", text).strip()


def _review_is_complete(review_file: Path) -> bool:
    """True iff the review file exists and contains a VERDICT: PASS/BLOCK
    line — i.e. the prior audit ran to completion."""
    if not review_file.exists():
        return False
    try:
        text = review_file.read_text(encoding="utf-8")
    except OSError:
        return False
    return bool(_VERDICT_RE.search(text))


def _build_resume_prompt_suffix(review_file: Path) -> str:
    """Return a RESUMING FROM PRIOR PARTIAL AUDIT block to append to the
    Stage 2c / 3c prompt when a partial review file exists on disk.
    Empty string when the review file is absent, empty, already contains
    a VERDICT line, or contains only Claude CLI error banners (rate-limit
    / usage-cap messages with no real audit content)."""
    if not review_file.exists():
        return ""
    partial = _strip_claude_error_banners(review_file.read_text(encoding="utf-8"))
    if not partial or _VERDICT_RE.search(partial):
        return ""
    return (
        "\n\n---\n\n"
        "## RESUMING FROM PRIOR PARTIAL AUDIT\n\n"
        "Your previous audit of this file was interrupted (likely a Claude "
        "API rate limit or CLI error mid-output). The partial output you "
        "produced is reproduced verbatim below. The target file may "
        "ALREADY contain patches you applied before the interruption — "
        "BEFORE running any new Edit calls, read the target file's "
        "current state and verify. Edit calls whose `old_string` no "
        "longer matches have likely already landed; record them in "
        "PATCHES_APPLIED and move on.\n\n"
        "Do NOT repeat sections you have already produced. Continue "
        "where you left off and finish the audit with the remaining "
        "sections in order (SUMMARY / FINDINGS / PATCHES_APPLIED / "
        "MANUAL_REMAINING / VERDICT). The final line of your complete "
        "output must still be `VERDICT: PASS` or "
        "`VERDICT: BLOCK ...`.\n\n"
        "PRIOR PARTIAL OUTPUT (verbatim):\n\n"
        "```\n"
        + partial
        + "\n```\n"
    )


def _detect_stage3b_drift(result: str, step_num: int, file_list: list[str],
                          is_test_step: bool) -> str | None:
    """Return None if the generated Stage-3b body looks consistent with the
    step's target files, else a short reason string. Applies to any step, with
    stricter checks for test steps (which is where drift has historically hit)."""
    # Circuit-breaker: the Stage 3b template instructs the LLM to emit
    # `ERROR: target file absent from architecture context` when its input
    # is missing the target file. Surface that as a drift reason so the
    # user sees a clean diagnostic instead of a fabricated prompt body.
    stripped = result.strip()
    if stripped.startswith("ERROR:"):
        return stripped.splitlines()[0]
    if f"## Step {step_num}" not in result:
        return f"missing '## Step {step_num}' heading"
    for f in file_list:
        if f not in result and f.replace("\\", "/") not in result:
            return f"body does not mention target file '{f}'"
    if is_test_step:
        low = result.lower()
        test_kws = ("pytest", "assert ", "def test_", "monkeypatch", "mock")
        if not any(k in low for k in test_kws):
            return ("test-file step but body contains no test keywords "
                    "(pytest/assert/def test_/monkeypatch/mock) — likely "
                    "asking for production code instead of tests")
    stub_count = sum(len(p.findall(result)) for p in _STUB_PATTERNS)
    if stub_count >= 3:
        return (f"body contains {stub_count} stub markers "
                "(`pass` / `...` / `# Placeholder`) — prompt reads as a "
                "skeleton, not an implementation directive; qwen3-coder "
                "and similar local models will produce an empty file")
    # Check that the prompt body is inside a non-bash code fence.
    # The run_aider parser requires (1) a ```bash fence for the aider command
    # and (2) a separate non-bash ``` fence for the prompt body.  LLMs
    # sometimes emit the prompt as plain text, which Stage 4 rejects.
    blocks = re.findall(r"```(\w*)\n.*?```", stripped, re.DOTALL)
    has_bash = any(lang == "bash" for lang in blocks)
    has_prompt_fence = any(lang != "bash" for lang in blocks)
    if has_bash and not has_prompt_fence:
        return ("prompt body is plain text instead of a fenced code block "
                "— run_aider requires a non-bash ``` fence around the "
                "prompt body")
    return None


def _fix_stage3b_fencing(result: str) -> str:
    """Wrap unfenced prompt bodies in a plain ``` fence.

    The run_aider parser requires every step to have a non-bash code fence
    for the prompt body.  If the LLM produced a bash fence for the aider
    command but left the prompt body as plain text, wrap everything after the
    closing bash fence (up to the end of the result) in a plain ``` fence.
    Returns the result unchanged if a non-bash fence already exists."""
    blocks = re.findall(r"```(\w*)\n.*?```", result, re.DOTALL)
    has_bash = any(lang == "bash" for lang in blocks)
    has_prompt_fence = any(lang != "bash" for lang in blocks)
    if not has_bash or has_prompt_fence:
        return result  # nothing to fix, or no bash fence to anchor on

    # Find the end of the bash fence and wrap the remaining text.
    bash_close = re.search(r"```bash\n.*?```", result, re.DOTALL)
    if not bash_close:
        return result
    after = result[bash_close.end():]
    # Strip leading/trailing whitespace from the prompt body but keep the
    # content intact.
    body = after.strip()
    if not body:
        return result
    return result[:bash_close.end()] + "\n\n```\n" + body + "\n```"


def _pkg_constraint(args: argparse.Namespace) -> str:
    """Return a constraint block naming the package if --package-name was
    passed, otherwise empty. Prepended to every LLM stage prompt so the
    model uses a fixed name instead of inventing one."""
    name = getattr(args, "package_name", None)
    if not name:
        return ""
    return (
        "## PACKAGE NAME CONSTRAINT\n\n"
        f"The Python package name is `{name}`. All source files MUST live\n"
        f"under `src/{name}/...`. Imports MUST use `from {name}.<module>` "
        "or `import {name}.<module>` style. Do not invent a different name.\n\n"
        "---\n\n"
    )


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
    prompt = _pkg_constraint(args) + load_prompt("stage0_summarize.md") + "\n\n" + concatenated

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

    prompt = (
        _pkg_constraint(args)
        + load_prompt("stage1_improve_prompt.md") + "\n\n"
        + initial.read_text(encoding="utf-8")
    )
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
        prompt_2a = (
            _pkg_constraint(args)
            + load_prompt("stage2a_section_plan.md") + "\n\n"
            + planning_content + "\n" + existing_ctx
        )
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

        # Short-circuit __init__.py sections — they're trivial re-export markers,
        # not worth the thinking-model's full budget.
        canned = _canned_init_py_section(sec_title)
        if canned is not None:
            with arch_plan.open("a", encoding="utf-8") as fh:
                fh.write(f"\n{canned}\n")
            cprint(
                f"    Section {sec_num}/{total} - done (canned __init__.py stub, no LLM call)",
                Color.GREEN,
            )
            progress.save(1, sub_step=sec_num, mode=mode)
            continue

        tmpl = load_prompt("stage2b_section.md")
        prompt_2b = (
            _pkg_constraint(args)
            + tmpl.replace("SECTITLE", sec_title).replace("SECDESC", sec_desc)
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


def stage2c_review(repo_root: Path, target_dir: Path, arch_plan: Path,
                   planning_prompt: Path, args: argparse.Namespace,
                   env: dict, planning_cfg: dict) -> bool:
    """Claude-driven pre-Stage-3 audit of Architecture Plan.md.

    Reads the template at prompts/stage2c_review.md, substitutes the
    TARGET_DIR and REPO_ROOT placeholders, calls Claude (which reads both
    files directly via its Read tool), writes the report to
    <target_dir>/Architecture Plan.review.md, and parses the final VERDICT
    line. Mirrors stage3c_review in structure but audits the arch plan
    instead of aidercommands.

    Returns True when the pipeline should proceed to Stage 3, False when
    the review reports VERDICT: BLOCK and the caller should stop."""
    banner("Stage 2c - Review Architecture Plan.md", Color.CYAN)
    if not arch_plan.exists():
        cprint(f"  Architecture Plan.md not found at {arch_plan} — skipping review", Color.YELLOW)
        return True
    if not planning_prompt.exists():
        cprint(f"  Implementation Planning Prompt.md not found at {planning_prompt} — skipping review", Color.YELLOW)
        return True

    review_file = target_dir / "Architecture Plan.review.md"
    backup_file = arch_plan.with_suffix(arch_plan.suffix + ".bak")

    # Short-circuit: if a prior audit completed, honour its verdict instead
    # of re-running. Delete the review file to force a fresh audit.
    if _review_is_complete(review_file):
        prior = review_file.read_text(encoding="utf-8")
        m = _VERDICT_RE.search(prior)
        prior_verdict = m.group(1) if m else "BLOCK"
        cprint(
            f"  Prior review complete ({prior_verdict}) — re-using. "
            f"Delete {review_file.name} to force a fresh audit.",
            Color.YELLOW,
        )
        return prior_verdict == "PASS"

    # Detect partial prior audit; if present, build a resume suffix so
    # Claude can continue rather than restart.
    resume_suffix = _build_resume_prompt_suffix(review_file)
    prompt = (
        load_prompt("stage2c_review.md")
        .replace("TARGET_DIR", str(target_dir).replace("\\", "/"))
        .replace("REPO_ROOT", str(repo_root).replace("\\", "/"))
        + resume_suffix
    )

    cprint(f"  Running Claude review + fix (streaming to {review_file.name})", Color.CYAN)
    if args.dry_run:
        cprint("  [DRY RUN] Would run review and patch in-place", Color.BLUE)
        return True

    # Only back up the arch plan on the FIRST audit attempt. Resume runs
    # preserve the original pre-patch backup (the one made before any
    # patches landed during the interrupted first attempt).
    if resume_suffix:
        cprint(
            f"  Partial prior review detected at {review_file.name} — "
            f"resuming from where it stopped. "
            f"(Pre-patch backup at {backup_file.name} preserved.)",
            Color.CYAN,
        )
    else:
        backup_file.write_bytes(arch_plan.read_bytes())
        cprint(f"  Pre-patch backup: {backup_file.name}", Color.CYAN)

    try:
        result = invoke_stage(
            prompt, "2c", args=args, env=env, planning_cfg=planning_cfg,
            thinking_file=None,
            stream_to=review_file,
        )
    except ClaudeError as exc:
        cprint(f"  Claude invocation failed: {exc}", Color.RED + Color.BOLD)
        cprint(
            f"  Partial output preserved at {review_file}. "
            f"Re-run the same command to continue the audit.",
            Color.YELLOW,
        )
        return False

    cprint(f"  Review saved to {review_file}", Color.GREEN)

    # Parse the ACCUMULATED file content (resume runs append to it; the
    # streaming return value only contains the most recent round's output).
    accumulated = review_file.read_text(encoding="utf-8")

    patches_match = re.search(
        r"^###\s*PATCHES_APPLIED\s*\n(.*?)(?=^###\s|\Z)",
        accumulated, re.MULTILINE | re.DOTALL,
    )
    if patches_match:
        patches_body = patches_match.group(1).strip()
        if patches_body and patches_body.lower() != "(none)":
            cprint("  Patches applied to Architecture Plan.md:", Color.CYAN)
            for line in patches_body.splitlines():
                line = line.strip()
                if line:
                    cprint(f"    {line}", Color.BLUE)

    verdict_match = re.search(r"^VERDICT:\s*(PASS|BLOCK)\b(.*)$", accumulated, re.MULTILINE)
    if not verdict_match:
        cprint(
            "  WARNING: review output has no parseable VERDICT line — "
            "treating as PASS; inspect the review file before Stage 3.",
            Color.YELLOW,
        )
        return True
    verdict = verdict_match.group(1)
    verdict_tail = verdict_match.group(2).strip()

    if verdict == "PASS":
        cprint("  VERDICT: PASS — Stage 3 cleared to run", Color.GREEN)
        return True

    cprint("  VERDICT: BLOCK" + (f" {verdict_tail}" if verdict_tail else ""),
           Color.RED + Color.BOLD)
    after = accumulated[verdict_match.end():].lstrip("\n").split("\n", 1)[0].strip()
    if after:
        cprint(f"  {after}", Color.RED)
    cprint(f"  Full findings: {review_file}", Color.YELLOW)
    cprint("  Fix the flagged sections in Architecture Plan.md, then resume.", Color.YELLOW)
    return False


def stage3c_review(repo_root: Path, target_dir: Path, arch_plan: Path,
                   aider_commands: Path, args: argparse.Namespace,
                   env: dict, planning_cfg: dict) -> bool:
    """Claude-driven pre-execution audit of aidercommands.md.

    Reads the template at prompts/stage3c_review.md, substitutes the
    TARGET_DIR and REPO_ROOT placeholders, calls Claude (which reads both
    files directly), writes the report to <target_dir>/aidercommands.review.md,
    and parses the final VERDICT line.

    Returns True when the pipeline should proceed to Stage 4, False when the
    review reports VERDICT: BLOCK and the caller should stop."""
    banner("Stage 3c - Review aidercommands.md", Color.CYAN)
    if not aider_commands.exists():
        cprint(f"  aidercommands.md not found at {aider_commands} — skipping review", Color.YELLOW)
        return True
    if not arch_plan.exists():
        cprint(f"  Architecture Plan.md not found at {arch_plan} — skipping review", Color.YELLOW)
        return True

    review_file = target_dir / "aidercommands.review.md"
    backup_file = aider_commands.with_suffix(aider_commands.suffix + ".bak")

    # Short-circuit: prior audit already complete → honour its verdict.
    if _review_is_complete(review_file):
        prior = review_file.read_text(encoding="utf-8")
        m = _VERDICT_RE.search(prior)
        prior_verdict = m.group(1) if m else "BLOCK"
        cprint(
            f"  Prior review complete ({prior_verdict}) — re-using. "
            f"Delete {review_file.name} to force a fresh audit.",
            Color.YELLOW,
        )
        return prior_verdict == "PASS"

    resume_suffix = _build_resume_prompt_suffix(review_file)
    prompt = (
        load_prompt("stage3c_review.md")
        .replace("TARGET_DIR", str(target_dir).replace("\\", "/"))
        .replace("REPO_ROOT", str(repo_root).replace("\\", "/"))
        + resume_suffix
    )

    cprint(f"  Running Claude review + fix (streaming to {review_file.name})", Color.CYAN)
    if args.dry_run:
        cprint("  [DRY RUN] Would run review and patch in-place", Color.BLUE)
        return True

    if resume_suffix:
        cprint(
            f"  Partial prior review detected at {review_file.name} — "
            f"resuming from where it stopped. "
            f"(Pre-patch backup at {backup_file.name} preserved.)",
            Color.CYAN,
        )
    else:
        backup_file.write_bytes(aider_commands.read_bytes())
        cprint(f"  Pre-patch backup: {backup_file.name}", Color.CYAN)

    try:
        result = invoke_stage(
            prompt, "3c", args=args, env=env, planning_cfg=planning_cfg,
            thinking_file=None,
            stream_to=review_file,
        )
    except ClaudeError as exc:
        cprint(f"  Claude invocation failed: {exc}", Color.RED + Color.BOLD)
        cprint(
            f"  Partial output preserved at {review_file}. "
            f"Re-run the same command to continue the audit.",
            Color.YELLOW,
        )
        return False

    cprint(f"  Review saved to {review_file}", Color.GREEN)

    # Parse from accumulated file (resume appends across runs).
    accumulated = review_file.read_text(encoding="utf-8")

    # Surface the PATCHES_APPLIED section inline so the user sees what
    # Claude changed without having to open the review file.
    patches_match = re.search(
        r"^###\s*PATCHES_APPLIED\s*\n(.*?)(?=^###\s|\Z)",
        accumulated, re.MULTILINE | re.DOTALL,
    )
    if patches_match:
        patches_body = patches_match.group(1).strip()
        if patches_body and patches_body.lower() != "(none)":
            cprint("  Patches applied to aidercommands.md:", Color.CYAN)
            for line in patches_body.splitlines():
                line = line.strip()
                if line:
                    cprint(f"    {line}", Color.BLUE)

    verdict_match = re.search(r"^VERDICT:\s*(PASS|BLOCK)\b(.*)$", accumulated, re.MULTILINE)
    if not verdict_match:
        cprint(
            "  WARNING: review output has no parseable VERDICT line — "
            "treating as PASS; inspect the review file before Stage 4.",
            Color.YELLOW,
        )
        return True
    verdict = verdict_match.group(1)
    verdict_tail = verdict_match.group(2).strip()

    if verdict == "PASS":
        cprint("  VERDICT: PASS — Stage 4 cleared to run", Color.GREEN)
        return True

    cprint("  VERDICT: BLOCK" + (f" {verdict_tail}" if verdict_tail else ""),
           Color.RED + Color.BOLD)
    # Print the line that follows the VERDICT (typically lists affected steps).
    after = accumulated[verdict_match.end():].lstrip("\n").split("\n", 1)[0].strip()
    if after:
        cprint(f"  {after}", Color.RED)
    cprint(f"  Full findings: {review_file}", Color.YELLOW)
    cprint("  Fix the flagged steps in aidercommands.md, then resume.", Color.YELLOW)
    return False


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
        prompt_3a = _pkg_constraint(args) + head + arch_content + "\n" + existing_ctx + tail
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

    filtered: list[str] = []
    for ln in lines:
        m = re.match(r"^\s*STEP\s+(\d+)\s*\|\s*(.+?)\s*\|\s*(.+)\s*$", ln)
        if m and is_pipeline_output_only_step(m.group(3).strip()):
            cprint(
                f"    Skipping Step {m.group(1)} - {m.group(2).strip()} "
                f"[target is pipeline output, already generated]",
                Color.BLUE,
            )
            continue
        filtered.append(ln)
    lines = filtered

    if not lines:
        cprint("  All steps were pipeline-output docs; nothing to generate.", Color.YELLOW)
        if step_plan_file.exists():
            step_plan_file.unlink()
        progress.save(3, mode=mode)
        return

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

        is_test_step = _is_test_step(file_list)
        head_template = "stage3b_step_test_head.md" if is_test_step else "stage3b_step_head.md"
        head = load_prompt(head_template)
        tail = load_prompt("stage3b_step_tail.md")
        body_ctx = architecture_slice(arch_content, file_list) if use_slice else arch_content
        body_ctx = sanitize_arch_context(body_ctx)

        prompt_3b = (
            _pkg_constraint(args)
            + head
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

        drift = _detect_stage3b_drift(result, step_num, file_list, is_test_step)
        if drift:
            cprint(f"    [drift detected] {drift} - regenerating once", Color.YELLOW)
            retry_prompt = (
                prompt_3b
                + "\n\n---\n\nIMPORTANT: Your previous attempt drifted: "
                + drift
                + ". Produce the step output correctly this time. The target "
                + "file(s) are " + aider_files + ". "
                + ("This is a TEST step — write a prompt that instructs the "
                   "LLM to IMPORT the module under test and write pytest-style "
                   "test functions. Do NOT ask the LLM to re-implement the "
                   "production class." if is_test_step else
                   "Ensure the body explicitly names each target file and "
                   "describes its implementation. The implementation prompt "
                   "you write MUST be a concrete directive, not a skeleton. "
                   "Do NOT emit method bodies that are only `pass`, `...`, or "
                   "`# Placeholder implementation` — a downstream local LLM "
                   "treats such stubs as 'already done' and produces an empty "
                   "file. Describe behavior in prose comments or pseudocode "
                   "the LLM must expand into real code.")
            )
            result = invoke_stage(
                retry_prompt, "3b", args=args, env=env, planning_cfg=planning_cfg,
                thinking_file=sidecar,
            )
            drift2 = _detect_stage3b_drift(result, step_num, file_list, is_test_step)
            if drift2:
                cprint(
                    f"    [drift persists after retry] {drift2} - writing "
                    f"generated output anyway; inspect Step {step_num} in "
                    f"{aider_commands.name} before running Stage 4.",
                    Color.RED,
                )

        result = _fix_stage3b_fencing(result)
        with aider_commands.open("a", encoding="utf-8") as fh:
            fh.write(f"\n{result.strip()}\n\n---\n")
        cprint(f"    Step {step_num}/{total} - done", Color.GREEN)
        progress.save(2, sub_step=step_num, mode=mode)

    if step_plan_file.exists():
        step_plan_file.unlink()
    progress.save(3, mode=mode)
    cprint(f"  All {total} steps generated in {aider_commands.name}", Color.GREEN)
