"""Endpoint + model resolution for run_aider.

Delegates .env parsing and endpoint lookup to the shared
`_pipeline.config` module so the precedence rules stay identical
across run_aider, fix_imports, and the coding-mode planning stages.
"""
from __future__ import annotations

from pathlib import Path

from _pipeline import config as cfg


# Exposed for callers that want to display the source of truth.
TOOLKIT_ROOT: Path = cfg.toolkit_root()
COMMON_ENV: Path = cfg.ENV_PATH

DEFAULT_LOCAL_MODEL = "qwen3.5:27b"


def resolve_local_config() -> tuple[str, str]:
    """Return (endpoint, model). See cfg.resolve_ollama_endpoint for the
    full endpoint precedence; model comes from .env LLM_AIDER_MODEL
    (falls back to DEFAULT_LOCAL_MODEL)."""
    env = cfg.load_env()
    endpoint = cfg.resolve_ollama_endpoint(env)
    model = env.get("LLM_AIDER_MODEL", DEFAULT_LOCAL_MODEL)
    return endpoint, model
