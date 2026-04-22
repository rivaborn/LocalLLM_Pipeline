"""Tests for the pre-step sanity check + quarantine helpers.

Run with:
    cd C:\\Coding\\LocalLLM_Pipeline\\LocalLLMCoding
    pytest test_sanity.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from _aider.sanity import (  # noqa: E402
    Verdict,
    _ensure_gitignore_entry,
    inspect_file,
    quarantine_file,
)


# ---- inspect_file: CLEAN cases ----

def test_missing_file_is_clean(tmp_path: Path) -> None:
    v, r = inspect_file(tmp_path / "nope.py")
    assert v is Verdict.CLEAN
    assert r == []


def test_empty_file_is_clean(tmp_path: Path) -> None:
    p = tmp_path / "empty.py"
    p.write_text("")
    assert inspect_file(p) == (Verdict.CLEAN, [])


def test_tiny_file_short_circuits(tmp_path: Path) -> None:
    """Files under the inspection minimum bypass all heuristics."""
    p = tmp_path / "tiny.py"
    p.write_text("x = 1\n")
    assert inspect_file(p) == (Verdict.CLEAN, [])


def test_normal_test_file_700_lines_is_clean(tmp_path: Path) -> None:
    """The 700-line tuning anchor (test_crud.py shape) must stay CLEAN."""
    p = tmp_path / "test_normal.py"
    body = "\n".join(f"def test_unique_{i}(): assert {i} == {i}" for i in range(700))
    p.write_text(body)
    v, r = inspect_file(p)
    assert v is Verdict.CLEAN, f"got {v} {r}"


# ---- inspect_file: CORRUPT cases (one heuristic at a time) ----

def test_size_alone_triggers_corrupt(tmp_path: Path) -> None:
    p = tmp_path / "huge.py"
    # All-unique varied content (no repetition), single long line per row
    body = "\n".join(f"variable_{i:09d} = {i*31 % 997}" for i in range(8000))
    p.write_text(body)
    v, r = inspect_file(p)
    assert v is Verdict.CORRUPT
    assert any("size=" in reason for reason in r)


def test_line_count_alone_triggers_corrupt(tmp_path: Path) -> None:
    p = tmp_path / "long.py"
    # 2500 unique short lines: clears size threshold but trips line count
    body = "\n".join(f"x_{i} = {i}" for i in range(2500))
    p.write_text(body)
    v, r = inspect_file(p)
    assert v is Verdict.CORRUPT
    assert any("lines=" in reason for reason in r)


def test_repetition_alone_triggers_corrupt(tmp_path: Path) -> None:
    p = tmp_path / "looped.py"
    # 100 copies of the same line - bytes>1024 (need ~10+ char lines)
    body = "\n".join(["the_same_line_repeated_over_and_over"] * 100)
    p.write_text(body)
    v, r = inspect_file(p)
    assert v is Verdict.CORRUPT
    assert any("repeated" in reason for reason in r)


def test_lang_drift_alone_triggers_corrupt(tmp_path: Path) -> None:
    """Hebrew-heavy .py file fires non-ASCII heuristic without other checks."""
    p = tmp_path / "drift.py"
    hebrew = "שלום עולם זה משפט"
    # 80 unique lines, ~6KB total - clears size minimum, under line/repetition
    body = "\n".join(f"{hebrew} {i:04d} פסוקית ייחודית unique end" for i in range(80))
    p.write_text(body, encoding="utf-8")
    v, r = inspect_file(p)
    assert v is Verdict.CORRUPT
    assert any("non-ASCII" in reason for reason in r)


def test_lang_drift_only_for_python_files(tmp_path: Path) -> None:
    """HTML/CSS/JSON can legitimately have heavy non-ASCII content."""
    p = tmp_path / "drift.html"
    hebrew = "שלום עולם זה משפט"
    body = "\n".join(f"{hebrew} {i:04d} פסוקית ייחודית unique end" for i in range(80))
    p.write_text(body, encoding="utf-8")
    v, r = inspect_file(p)
    assert v is Verdict.CLEAN, f"non-py file should not trigger lang-drift, got {v} {r}"


# ---- inspect_file: SUSPECT cases ----

def test_unclosed_fence_is_suspect(tmp_path: Path) -> None:
    p = tmp_path / "truncated.py"
    head = "\n".join(["def foo():", "    pass", "", "```python", "def bar():", "    pa"])
    # Varied padding to clear size minimum without tripping repetition
    pad = "\n".join(f"# unique pad line number {i:04d}" for i in range(80))
    p.write_text(head + "\n" + pad)
    v, r = inspect_file(p)
    assert v is Verdict.SUSPECT, f"got {v} {r}"
    assert "unclosed code fence" in r


# ---- quarantine_file ----

def test_quarantine_moves_file_preserving_relative_path(tmp_path: Path) -> None:
    target = tmp_path / "tests" / "test_x.py"
    target.parent.mkdir()
    target.write_text("garbage content")

    new = quarantine_file(target, tmp_path)

    assert not target.exists(), "original should be gone"
    assert new.exists(), "quarantined file should exist"
    assert new.read_text() == "garbage content"
    # Relative structure preserved under timestamp dir
    rel_parts = new.relative_to(tmp_path / ".aider-quarantine").parts
    assert len(rel_parts) >= 3, f"expected timestamp/tests/test_x.py, got {rel_parts}"
    assert rel_parts[1:] == ("tests", "test_x.py")


def test_quarantine_creates_gitignore_when_missing(tmp_path: Path) -> None:
    target = tmp_path / "garbage.py"
    target.write_text("X")
    quarantine_file(target, tmp_path)
    gi = tmp_path / ".gitignore"
    assert gi.exists()
    assert ".aider-quarantine/" in gi.read_text()


def test_ensure_gitignore_is_idempotent(tmp_path: Path) -> None:
    _ensure_gitignore_entry(tmp_path)
    _ensure_gitignore_entry(tmp_path)
    _ensure_gitignore_entry(tmp_path)
    content = (tmp_path / ".gitignore").read_text()
    assert content.count(".aider-quarantine/") == 1


def test_ensure_gitignore_appends_to_existing(tmp_path: Path) -> None:
    gi = tmp_path / ".gitignore"
    gi.write_text("__pycache__/\n*.log\n")
    _ensure_gitignore_entry(tmp_path)
    content = gi.read_text()
    assert "__pycache__/" in content
    assert "*.log" in content
    assert ".aider-quarantine/" in content


def test_ensure_gitignore_no_double_when_already_present(tmp_path: Path) -> None:
    gi = tmp_path / ".gitignore"
    gi.write_text("__pycache__/\n.aider-quarantine/\nbuild/\n")
    _ensure_gitignore_entry(tmp_path)
    assert gi.read_text().count(".aider-quarantine/") == 1
