"""Claude Code CLI wrapper.

Handles account switching (CLAUDE_CONFIG_DIR), the ultrathink prefix,
and model routing (sonnet / opus / explicit tag). Used by the coding
mode; debug mode no longer calls Claude after the Step 5 migration to
a local LLM.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


# Map of account identifiers to their CLAUDE_CONFIG_DIR locations.
# Extend this dict if more accounts are added.
ACCOUNT_CONFIG_DIRS: dict[str, str] = {
    "claude1": str(Path.home() / ".clauderivalon"),
    "claude2": str(Path.home() / ".claudefksogbetun"),
}


class ClaudeError(RuntimeError):
    """Raised when the `claude` CLI exits non-zero."""


def resolve_account_dir(account: str) -> str:
    key = account.lower()
    if key not in ACCOUNT_CONFIG_DIRS:
        valid = ", ".join(sorted(ACCOUNT_CONFIG_DIRS))
        raise ClaudeError(f"Unknown Claude account '{account}'. Expected one of: {valid}")
    return ACCOUNT_CONFIG_DIRS[key]


def invoke_claude(
    prompt: str,
    *,
    model: str,
    account: str = "Claude1",
    output_format: str = "text",
) -> str:
    """Run `claude --model <m> --output-format <f>` with `prompt` piped
    via stdin. Returns the captured stdout. Raises ClaudeError on failure."""
    env = os.environ.copy()
    env["CLAUDE_CONFIG_DIR"] = resolve_account_dir(account)

    cmd = ["claude", "--model", model, "--output-format", output_format]
    proc = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    if proc.returncode != 0:
        raise ClaudeError(
            f"claude CLI failed (exit {proc.returncode}). "
            f"stderr:\n{proc.stderr}"
        )
    return proc.stdout.strip()
