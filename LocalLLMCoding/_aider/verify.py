"""Post-aider sanity check: were the declared output files written?"""
from __future__ import annotations

from pathlib import Path

from .parser import step_file_list


def verify_outputs(step: dict) -> tuple[bool, list[str]]:
    """Check every file the step should have generated is present and
    non-empty. Returns (ok, list_of_problems).

    Exception: `__init__.py` is allowed to be empty (conventional Python
    package marker)."""
    problems: list[str] = []
    for f in step_file_list(step):
        p = Path(f)
        if not p.is_absolute():
            p = Path.cwd() / p
        if not p.exists():
            problems.append(f"missing: {f}")
            continue
        try:
            size = p.stat().st_size
        except OSError as exc:
            problems.append(f"stat-error {f}: {exc}")
            continue
        if size == 0 and p.name != "__init__.py":
            problems.append(f"empty: {f}")
    return (not problems), problems
