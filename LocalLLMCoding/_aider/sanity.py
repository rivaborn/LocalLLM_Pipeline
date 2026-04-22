"""Pre-step inspection of target files for corruption (size, repetition,
language drift, truncation). When a file is flagged CORRUPT, it's moved to
<repo>/.aider-quarantine/<timestamp>/ so aider regenerates from a clean
slate while the original remains recoverable.

Triggered by a real failure: a Stage 4 runaway left an 8289-line / 365KB
Hebrew-repetition file on disk; on retry, aider loaded it as the file's
"current state" and the prompt overflowed the model's context window.
Heuristics here are tuned to catch that shape (and similar) without
flagging legitimate large files like a 700-line test module."""
from __future__ import annotations

import shutil
from collections import Counter
from datetime import datetime
from enum import Enum
from pathlib import Path


class Verdict(Enum):
    CLEAN = "clean"
    SUSPECT = "suspect"
    CORRUPT = "corrupt"


# Thresholds tuned against known data points:
#   test_crud.py (700 lines, ~25KB)                          must be CLEAN
#   test_static_css.py runaway (8289 lines, 365KB, Hebrew)   must be CORRUPT
_MAX_BYTES = 100_000
_MAX_LINES = 2_000
_MAX_LINE_REPETITIONS = 50
_MAX_NON_ASCII_RATIO_PY = 0.05
_MIN_BYTES_TO_INSPECT = 1024

_QUARANTINE_DIR_NAME = ".aider-quarantine"
_GITIGNORE_LINE = ".aider-quarantine/"


def inspect_file(path: Path) -> tuple[Verdict, list[str]]:
    """Return (verdict, reasons). Missing, empty, or tiny files are CLEAN."""
    if not path.exists() or not path.is_file():
        return Verdict.CLEAN, []

    size = path.stat().st_size
    if size < _MIN_BYTES_TO_INSPECT:
        return Verdict.CLEAN, []

    reasons: list[str] = []

    if size > _MAX_BYTES:
        reasons.append(f"size={size} bytes > {_MAX_BYTES}")

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return Verdict.SUSPECT, [f"read error: {exc}"]

    lines = text.splitlines()
    if len(lines) > _MAX_LINES:
        reasons.append(f"lines={len(lines)} > {_MAX_LINES}")

    counts = Counter(line.strip() for line in lines if line.strip())
    if counts:
        most_line, most_count = counts.most_common(1)[0]
        if most_count >= _MAX_LINE_REPETITIONS:
            preview = (most_line[:40] + "...") if len(most_line) > 40 else most_line
            reasons.append(f"line repeated {most_count}x: {preview!r}")

    if path.suffix == ".py" and text:
        non_ascii = sum(1 for c in text if ord(c) > 127)
        ratio = non_ascii / len(text)
        if ratio > _MAX_NON_ASCII_RATIO_PY:
            reasons.append(f"non-ASCII={ratio:.1%} > {_MAX_NON_ASCII_RATIO_PY:.0%}")

    if reasons:
        return Verdict.CORRUPT, reasons

    # Odd number of ``` fences means the file ends mid-block (aider
    # truncation). Doesn't justify destruction, but worth flagging.
    if text.count("```") % 2 == 1:
        return Verdict.SUSPECT, ["unclosed code fence"]

    return Verdict.CLEAN, []


def quarantine_file(path: Path, repo_root: Path) -> Path:
    """Move `path` to <repo_root>/.aider-quarantine/<YYYYMMDD-HHMMSS>/
    preserving its repo-relative path. Returns the new location."""
    _ensure_gitignore_entry(repo_root)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    quarantine_root = repo_root / _QUARANTINE_DIR_NAME / timestamp

    # Files outside the repo root (rare in practice — should never happen
    # for step-declared targets) get flattened to the dir root by name only.
    try:
        rel = path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        rel = Path(path.name)

    dest = quarantine_root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(dest))
    return dest


def _ensure_gitignore_entry(repo_root: Path) -> None:
    """Append `.aider-quarantine/` to <repo_root>/.gitignore if absent.
    Creates the file if it doesn't exist. Idempotent."""
    gitignore = repo_root / ".gitignore"
    target = _GITIGNORE_LINE.rstrip("/")
    if gitignore.exists():
        existing = gitignore.read_text(encoding="utf-8", errors="replace")
        # Match on a whole line — substring `in` would false-positive on
        # patterns like `.aider-quarantine/keep-me.txt`.
        for line in existing.splitlines():
            stripped = line.strip()
            if stripped == target or stripped == _GITIGNORE_LINE:
                return
        sep = "" if existing.endswith("\n") else "\n"
        gitignore.write_text(existing + sep + _GITIGNORE_LINE + "\n", encoding="utf-8")
    else:
        gitignore.write_text(_GITIGNORE_LINE + "\n", encoding="utf-8")
