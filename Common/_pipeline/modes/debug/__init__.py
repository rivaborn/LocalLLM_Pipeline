"""Architecture debug mode.

Six-step pipeline:
    1-4  PowerShell workers (dataflow / interfaces / testgap / bughunt)
    5    Per-file bug fix via local LLM (qwen3-coder:30b by default)
    6    Archive .debug_changes.md to Implemented Plans/

Submodules:
    cli        -- argparse register() + run() + worker/archive helpers
    fix_bugs   -- step 5 per-file LLM fix loop

Callers do `from _pipeline.modes import debug; debug.register(); debug.run()`.
"""
from .cli import register, run

__all__ = ["register", "run"]
