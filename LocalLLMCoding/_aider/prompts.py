"""Prompt-block construction: symbol candidates + forward-looking plan.

ctags inventory (past symbols) and pyright resolution (external symbols)
are imported lazily from Common/_pipeline/; the runner injects all three
alongside the forward-looking 'planned files' block built here.
"""
from __future__ import annotations

import re

from .parser import step_file_list


# CamelCase identifiers the model is likely to import (classes, enum
# members, constants). Used to harvest candidate names from each step
# prompt so pyright can resolve their real module locations.
_CAMEL_RE = re.compile(r"\b[A-Z][A-Za-z0-9_]{2,}\b")

_STOPWORDS = {
    "None", "True", "False", "Self", "TODO", "FIXME", "NOTE", "API", "URL",
    "UI", "HTTP", "HTTPS", "JSON", "YAML", "TOML", "HTML", "CSS", "SQL",
    "GPU", "CPU", "RAM", "OS", "CLI", "TUI", "PR", "CI", "CD",
}


def extract_candidate_symbols(text: str, limit: int = 40) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for m in _CAMEL_RE.finditer(text):
        n = m.group(0)
        if n in _STOPWORDS or n in seen:
            continue
        seen.add(n)
        names.append(n)
        if len(names) >= limit:
            break
    return names


def build_planned_block(future_steps: list[dict]) -> str:
    """Forward-looking inventory: files + titles of steps that come later.

    ctags can only see files that already exist, so imports that reference
    files generated in a later step must be guessed. Surfacing the upcoming
    step plan here lets the model align names with what WILL be written."""
    if not future_steps:
        return ""
    lines = [
        "## Planned Upcoming Files",
        "",
        "These files have not been generated yet but will be produced by",
        "later steps. If you need to import from one, use the file name as",
        "your guide and align your expected class/function names with the",
        "step title (e.g. 'Step 18 - PowerTab implementation' -> class",
        "`PowerTab` in power_tab.py).",
        "",
    ]
    for s in future_steps:
        files = step_file_list(s)
        if not files:
            continue
        lines.append(f"- Step {s['number']} - {s['title']}")
        for f in files:
            lines.append(f"    - {f}")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"
