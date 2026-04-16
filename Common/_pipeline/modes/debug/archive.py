"""Step 6: archive .debug_changes.md to Implemented Plans/Bug Fix Changes N.md."""
from __future__ import annotations

import logging
import re
from pathlib import Path

from ...progress import ProgressFile
from ...ui import Color, cprint


def _next_bugfix_number(impl_dir: Path) -> int:
    if not impl_dir.is_dir():
        return 1
    highest = 0
    for p in impl_dir.glob("Bug Fix Changes *.md"):
        m = re.match(r"Bug Fix Changes (\d+)\.md$", p.name)
        if m:
            highest = max(highest, int(m.group(1)))
    return highest + 1


def step6_archive(
    repo_root: Path,
    progress: ProgressFile,
    target_dir: str,
    logger: logging.Logger,
    dry_run: bool,
) -> None:
    cprint("\n  Step 6/6 - Archive Bug Fix Changes", Color.CYAN + Color.BOLD)
    logger.info("Step 6/6: Archive Bug Fix Changes")
    impl_dir = repo_root / "Implemented Plans"
    change_log = repo_root / ".debug_changes.md"

    if dry_run:
        num = _next_bugfix_number(impl_dir)
        cprint(f"  [DRY RUN] Would write: Implemented Plans/Bug Fix Changes {num}.md",
               Color.BLUE)
        return

    if not change_log.exists():
        cprint("  No .debug_changes.md to archive - nothing to do", Color.YELLOW)
        progress.save(6, mode="debug", target_dir=target_dir)
        return

    impl_dir.mkdir(parents=True, exist_ok=True)
    num = _next_bugfix_number(impl_dir)
    dst = impl_dir / f"Bug Fix Changes {num}.md"
    dst.write_text(change_log.read_text(encoding="utf-8"), encoding="utf-8")
    cprint(f"  Archived -> {dst.relative_to(repo_root)}", Color.GREEN)
    change_log.unlink()
    progress.save(6, mode="debug", target_dir=target_dir)
