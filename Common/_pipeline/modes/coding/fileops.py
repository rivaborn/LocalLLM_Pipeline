"""Filesystem / content helpers for the coding pipeline.

Kept together because they're all small utilities operating on paths,
prompt templates, or previously-generated artefacts.
"""
from __future__ import annotations

import argparse
import datetime
import re
from pathlib import Path

from ...ui import Color, cprint


# Prompt templates live at Common/_pipeline/prompts/, three levels up
# from this file (coding/fileops.py -> coding/ -> modes/ -> _pipeline/).
PROMPT_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8")


def confirm_overwrite(paths: list[Path], args: argparse.Namespace) -> bool:
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


def get_implemented_plans(repo_root: Path) -> list[Path]:
    impl_dir = repo_root / "Implemented Plans"
    if not impl_dir.is_dir():
        return []
    rx = re.compile(r"^(Architecture Plan|Bug Fix Changes) \d+\.md$")
    return sorted(
        [p for p in impl_dir.glob("*.md") if rx.match(p.name)],
        key=lambda p: p.stat().st_mtime,
    )


def codebase_summary_context(repo_root: Path) -> str:
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


# Files produced by the pipeline itself. If a Stage 3 step targets only
# these, it's asking aider to re-write something the pipeline already
# generated — skip it.
PIPELINE_OUTPUT_FILES = frozenset({
    "Architecture Plan.md",
    "aidercommands.md",
    "Implementation Planning Prompt.md",
    "PromptUpdates.md",
    "Codebase Summary.md",
})


def is_pipeline_output_only_step(step_files: str) -> bool:
    files = [f.strip() for f in step_files.split(",") if f.strip()]
    if not files:
        return False
    return all(Path(f).name in PIPELINE_OUTPUT_FILES for f in files)


# Patterns that, when present in the architecture context fed to Stage 3b,
# bias qwen3-coder and similar local models to copy stubs into their own
# output. Neutralised before the LLM sees them.
_ARCH_STUB_SUBS = [
    # `def foo(...):` on a line, next line is only `pass` (with optional trailing comment).
    (re.compile(r"(:[ \t]*\n[ \t]+)pass\b[ \t]*(?:#.*)?$", re.MULTILINE),
     r"\1# [pseudocode stub - generated code MUST include a full implementation]"),
    # Same for bare ellipsis bodies.
    (re.compile(r"(:[ \t]*\n[ \t]+)\.\.\.[ \t]*(?:#.*)?$", re.MULTILINE),
     r"\1# [pseudocode stub - generated code MUST include a full implementation]"),
    # `# Placeholder implementation` anywhere.
    (re.compile(r"#\s*Placeholder implementation\b", re.IGNORECASE),
     "# [implement per the surrounding specification]"),
    # Bare `# Placeholder` line (but not `# Placeholder: some note`).
    (re.compile(r"^(\s*)#\s*Placeholder\s*$", re.MULTILINE),
     r"\1# [implement per the surrounding specification]"),
]


def sanitize_arch_context(content: str) -> str:
    """Neutralise stub patterns in the architecture context before it is
    injected into a Stage 3b prompt.

    Architecture plans describe methods in pseudocode, sometimes with
    `pass` / `...` / `# Placeholder` as the body. Local models read those
    as "this is the template to emit", copy them into their output, and
    trigger the drift detector. Replacing them with explicit
    "MUST include a full implementation" comments removes the pattern
    without losing the method-signature context the LLM needs."""
    for pattern, replacement in _ARCH_STUB_SUBS:
        content = pattern.sub(replacement, content)
    return content


def architecture_slice(arch_content: str, files: list[str]) -> str:
    """Return the subset of Architecture Plan.md sections relevant to
    the given files (by basename match) or always-included sections.

    Test files pull in their production module's section: a file named
    `test_<stem>.py` also matches progressively-stripped variants of
    `<stem>.py`. For `test_gpu_monitor.py` the candidates are
    `gpu_monitor.py` and `monitor.py`, so a heading like
    `## Module: src/nmon/gpu/monitor.py` is hit via the `monitor.py`
    variant. Accepts some over-match (e.g. also pulls `llm/monitor.py`)
    — the LLM gets distinguishing signal from the step title + file name."""
    basenames: list[str] = []
    for f in files:
        if not f:
            continue
        b = Path(f.replace("/", "\\")).name
        basenames.append(b)
        if b.startswith("test_") and b.endswith(".py"):
            stem = b[len("test_"):-len(".py")]  # "gpu_monitor"
            tokens = stem.split("_")
            for i in range(len(tokens)):
                basenames.append("_".join(tokens[i:]) + ".py")
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


def detect_package_dir(repo_root: Path) -> str | None:
    """Best-effort autodetect for Stage 5's fix_imports --package target.
    Prefers src-layout (src/<pkg>/), falls back to the first top-level
    dir containing __init__.py."""
    _skip = {"tests", "test", "venv", ".venv", "architecture", "LocalLLMCodePrompts",
             "build", "dist", "__pycache__", ".git", "Implemented Plans"}
    src = repo_root / "src"
    if src.is_dir():
        for child in sorted(src.iterdir()):
            if child.is_dir() and (child / "__init__.py").exists():
                return f"src/{child.name}"
    for child in sorted(repo_root.iterdir()):
        if not child.is_dir() or child.name in _skip or child.name.startswith("."):
            continue
        if (child / "__init__.py").exists():
            return child.name
    return None
