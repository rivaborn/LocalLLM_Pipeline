"""File-based progress tracking.

Preserves the key=value line format used by the legacy PowerShell
pipelines so an in-progress run migrated between the old and new
orchestrator reads identically. Two helpers:
    - ProgressFile: generic last-completed + sub-step tracker
    - coding mode and debug mode each instantiate one against their
      respective sentinel file (.progress / .debug_progress).
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ProgressState:
    last_completed: int
    sub_step: int | None
    mode: str | None
    target_dir: str | None


class ProgressFile:
    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> ProgressState:
        if not self.path.exists():
            return ProgressState(last_completed=-1, sub_step=None, mode=None, target_dir=None)
        last = -1
        sub: int | None = None
        mode: str | None = None
        target: str | None = None
        for line in self.path.read_text(encoding="utf-8").splitlines():
            key, _, val = line.strip().partition("=")
            val = val.strip()
            if key == "LastCompleted" and val.isdigit():
                last = int(val)
            elif key == "SubStep" and val.isdigit():
                sub = int(val)
            elif key == "Mode":
                mode = val
            elif key == "TargetDir":
                target = val
            elif key == "Engine":
                # Legacy compat (pre-AllClaude): 'claude' -> 'allclaude',
                # 'local' -> nearest-equivalent 'default'.
                if mode is None:
                    mode = {"claude": "allclaude", "local": "default"}.get(val)
        return ProgressState(
            last_completed=last, sub_step=sub, mode=mode, target_dir=target
        )

    def save(
        self,
        step: int,
        *,
        sub_step: int | None = None,
        mode: str | None = None,
        target_dir: str | None = None,
    ) -> None:
        lines = [
            f"LastCompleted={step}",
            f"Timestamp={datetime.datetime.now():%Y-%m-%d %H:%M:%S}",
        ]
        if sub_step is not None and sub_step >= 0:
            lines.append(f"SubStep={sub_step}")
        if mode:
            lines.append(f"Mode={mode}")
        if target_dir:
            lines.append(f"TargetDir={target_dir}")
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()
