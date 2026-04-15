"""Per-mode orchestrator implementations.

Each mode exposes `register(subparsers)` to add its CLI subcommand
and `run(args)` to execute it. The entry point in ArchPipeline.py
imports each module and wires them up.
"""
