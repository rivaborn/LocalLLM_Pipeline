#!/usr/bin/env python3
"""Entry point: parse aidercommands.md and run aider per step.

Usage:
    python run_aider.py                                # run all steps against local Ollama
    python run_aider.py --from-step 5                  # resume from step 5
    python run_aider.py --dry-run                      # preview without running
    python run_aider.py --model ollama_chat/other:tag  # override model verbatim
    python run_aider.py --pyright                      # enable pyright symbol resolution

Invoked from the repo root you want aider to edit, e.g.::

    cd C:\\Coding\\nmonClaude
    python .\\LocalLLMCoding\\run_aider.py

Implementation lives in the sibling _aider/ package; this file is a
thin shim that fixes up sys.path (so submodules can import from
Common/_pipeline/) and then calls _aider.cli.main().
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TOOLKIT_ROOT = _HERE.parent    # e.g. C:\Coding\LocalLLM_Pipeline\
sys.path.insert(0, str(_TOOLKIT_ROOT / "Common"))
sys.path.insert(0, str(_HERE))

from _aider.cli import main  # noqa: E402


if __name__ == "__main__":
    main()
