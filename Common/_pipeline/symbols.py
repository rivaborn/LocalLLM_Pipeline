"""Language-agnostic symbol extraction via universal-ctags.

Produces a compact 'symbol inventory' string suitable for prepending to
an aider / LLM prompt so the model knows what classes, functions, and
methods already exist in the repo. Reduces cross-file import drift when
generating files one at a time.

Works on any language ctags supports (Python, C, C++, C#, Go, Rust,
Java, TypeScript, Ruby, etc.).

Usage:
    from _pipeline.symbols import build_inventory_block
    block = build_inventory_block(repo_root)
    if block:
        full_prompt = block + "\n\n" + step_prompt
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


# ctags 'kind' letters that we consider "importable" across languages.
# Intentionally permissive; tuned to favour false-positives (include a
# symbol that shouldn't be imported) over false-negatives (miss a real
# symbol the model might need to reference).
_IMPORTABLE_KINDS = {
    "class", "struct", "interface", "enum", "function", "method",
    "namespace", "module", "typedef", "type", "trait", "constant",
    "variable",  # module-level
    # short forms that older ctags emit
    "c", "s", "i", "g", "f", "m", "n", "t", "v",
}

# Files / dirs to ignore during scan.
_EXCLUDE_GLOBS = [
    ".git", ".venv", "venv", "__pycache__", "node_modules", "build",
    "dist", ".cache", ".pytest_cache", ".mypy_cache", "architecture",
    "LocalLLMCodePrompts", "Implemented Plans", "tests", "test",
]


def ctags_available() -> bool:
    return shutil.which("ctags") is not None


def _run_ctags(repo_root: Path) -> list[dict]:
    """Return raw ctags JSON entries (one per symbol) for the repo."""
    cmd = [
        "ctags",
        "--output-format=json",
        "--fields=+nKs",       # +name, +kind (full), +signature
        "--languages=all",
        "-R",
    ]
    for g in _EXCLUDE_GLOBS:
        cmd += [f"--exclude={g}"]
    cmd += ["-f", "-", "."]    # write JSON to stdout
    try:
        out = subprocess.run(
            cmd, cwd=repo_root, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if out.returncode != 0:
        return []
    entries: list[dict] = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _group_by_file(entries: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for e in entries:
        kind = (e.get("kind") or "").lower()
        if kind not in _IMPORTABLE_KINDS:
            continue
        scope = (e.get("scopeKind") or "").lower()
        # Skip members/locals inside classes for Python/C++ (we surface the
        # class; the class's methods already get extracted as separate
        # entries with kind=method).
        if scope in ("function",):
            continue
        path = e.get("path") or ""
        if not path:
            continue
        out.setdefault(path, []).append(e)
    return out


def _format_entry(e: dict) -> str:
    name = e.get("name", "?")
    kind = (e.get("kind") or "?").lower()
    sig = e.get("signature") or ""
    if sig:
        return f"{kind} {name}{sig}"
    return f"{kind} {name}"


def build_inventory(repo_root: Path, max_per_file: int = 40) -> dict[str, list[str]]:
    """Return {relative_path: [formatted_symbol, ...]} for the repo."""
    if not ctags_available():
        return {}
    entries = _run_ctags(repo_root)
    grouped = _group_by_file(entries)
    out: dict[str, list[str]] = {}
    for path, items in sorted(grouped.items()):
        lines = [_format_entry(e) for e in items[:max_per_file]]
        if len(items) > max_per_file:
            lines.append(f"... ({len(items) - max_per_file} more)")
        out[path] = lines
    return out


def build_inventory_block(repo_root: Path, max_per_file: int = 40) -> str:
    """Return a markdown block ready to prepend to a prompt, or '' if no
    ctags or no symbols found."""
    inv = build_inventory(repo_root, max_per_file=max_per_file)
    if not inv:
        return ""
    lines = [
        "## Existing Symbol Inventory",
        "",
        "The following symbols already exist in the repo. When writing new",
        "code, import from these paths using these exact names; do not",
        "invent new names for symbols that already exist here.",
        "",
    ]
    for path, syms in inv.items():
        lines.append(f"### {path}")
        for s in syms:
            lines.append(f"- {s}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
