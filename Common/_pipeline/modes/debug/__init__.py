"""Architecture debug mode.

Six-step pipeline:
    1-4  PowerShell workers (dataflow / interfaces / testgap / bughunt)
    5    Per-file bug fix via local LLM (qwen3-coder:30b by default)
    6    Archive .debug_changes.md to Implemented Plans/

Submodules:
    cli        -- argparse register() + top-level run()
    workers    -- PowerShell worker dispatcher for steps 1-4
    fix_bugs   -- step 5 per-file LLM fix loop + helpers
    archive    -- step 6 archive of .debug_changes.md

Callers (ArchPipeline.py, all_modes.py) do
    from _pipeline.modes import debug
    debug.register(subparsers); debug.run(args)
so this __init__ re-exports both.
"""
from .cli import register, run

__all__ = ["register", "run"]
