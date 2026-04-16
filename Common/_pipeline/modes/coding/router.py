"""Mode / engine routing and the single entry point that dispatches a
stage prompt to either Claude or the local Ollama server.

Stage defaults (per sub-stage, not per top-level stage number):
    0  sonnet  think=false  local_model=<planning default>
    1  sonnet  think=false  local_model=<planning default>
    2a opus    think=true   local_model=<planning default>
    2b opus    think=true   local_model=<planning default>
    3a opus    think=true   local_model=qwen3-coder:30b
    3b sonnet  think=false  local_model=qwen3-coder:30b
"""
from __future__ import annotations

import argparse
from pathlib import Path

from ...claude import invoke_claude
from ...ollama import invoke_local_llm
from ...ui import Color, cprint


STAGE_DEFAULTS: dict[str, dict] = {
    "0":  {"model": "sonnet", "think": False, "local_model": ""},
    "1":  {"model": "sonnet", "think": False, "local_model": ""},
    "2a": {"model": "opus",   "think": True,  "local_model": ""},
    "2b": {"model": "opus",   "think": True,  "local_model": ""},
    "3a": {"model": "opus",   "think": True,  "local_model": "qwen3-coder:30b"},
    "3b": {"model": "sonnet", "think": False, "local_model": "qwen3-coder:30b"},
}


def get_mode(args: argparse.Namespace) -> str:
    if args.all_claude:
        return "allclaude"
    if args.local:
        return "local"
    return "default"


def get_engine(sub_stage: str, mode: str) -> str:
    if mode == "allclaude":
        return "claude"
    if mode == "local":
        return "local"
    return "claude" if sub_stage == "1" else "local"


def _think_prefix(sub_stage: str, args: argparse.Namespace, engine: str) -> str:
    """'ultrathink. ' prefix for Claude stages only; no-op for local."""
    if engine == "local":
        return ""
    use = STAGE_DEFAULTS[sub_stage]["think"]
    if args.ultrathink:
        use = True
    if args.no_ultrathink:
        use = False
    return "ultrathink. " if use else ""


def _resolve_local_model(sub_stage: str, user_supplied: str | None, fallback: str) -> str:
    if user_supplied:
        return user_supplied
    override = STAGE_DEFAULTS[sub_stage]["local_model"]
    return override or fallback


def _resolve_claude_model(sub_stage: str, user_override: str | None) -> str:
    if user_override:
        return user_override
    return STAGE_DEFAULTS[sub_stage]["model"]


def invoke_stage(
    prompt: str,
    sub_stage: str,
    *,
    args: argparse.Namespace,
    env: dict[str, str],
    planning_cfg: dict,
    thinking_file: Path | None = None,
) -> str:
    mode = get_mode(args)
    engine = get_engine(sub_stage, mode)
    think_pfx = _think_prefix(sub_stage, args, engine)
    full_prompt = think_pfx + prompt

    if engine == "claude":
        model = _resolve_claude_model(sub_stage, args.model)
        cprint(f"    [claude model={model}]", Color.BLUE)
        if args.dry_run:
            return f"[DRY RUN Claude output for stage {sub_stage}]"
        return invoke_claude(full_prompt, model=model, account=args.claude)

    # local engine
    local_model = _resolve_local_model(sub_stage, args.local_model, planning_cfg["model"])
    # Per-stage coder-model override disables thinking automatically (qwen3-coder
    # is not a reasoning model; sending think:true to it wastes budget).
    stage_think = planning_cfg["think"] and local_model == planning_cfg["model"]
    cprint(
        f"    [local: {local_model} @ {planning_cfg['endpoint']} "
        f"ctx={planning_cfg['num_ctx']} think={stage_think}]",
        Color.BLUE,
    )
    if args.dry_run:
        return f"[DRY RUN local output for stage {sub_stage}]"

    return invoke_local_llm(
        full_prompt,
        env=env,
        endpoint=planning_cfg["endpoint"],
        model=local_model,
        num_ctx=planning_cfg["num_ctx"],
        max_tokens=planning_cfg["max_tokens"],
        timeout=planning_cfg["timeout"],
        temperature=planning_cfg["temperature"],
        think=stage_think,
        thinking_file=thinking_file if planning_cfg["save_thinking"] else None,
    )
