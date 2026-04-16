"""Architecture coding mode.

Public entry points only. Implementation is split into submodules:
    router         -- mode/engine selection + LLM invocation
    fileops        -- path helpers, prompt loader, plan discovery
    stages_llm     -- stages 0-3 (LLM-driven planning)
    stages_exec    -- stages 4-5 (aider / fix_imports subprocesses)
    cli            -- argparse register() + top-level run()

Callers (ArchPipeline.py, all_modes.py) do
    from _pipeline.modes import coding
    coding.register(subparsers); coding.run(args)
so this module re-exports both names.
"""
from .cli import register, run

__all__ = ["register", "run"]
