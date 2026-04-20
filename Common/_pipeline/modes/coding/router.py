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
import datetime
from pathlib import Path

from ... import config as cfg
from ...claude import invoke_claude
from ...ollama import invoke_local_llm
from ...ui import Color, cprint


STAGE_DEFAULTS: dict[str, dict] = {
    "0":  {"model": "sonnet", "think": False, "local_model": ""},
    "1":  {"model": "sonnet", "think": False, "local_model": ""},
    "2a": {"model": "opus",   "think": True,  "local_model": "qwen3-coder:30b"},
    "2b": {"model": "opus",   "think": True,  "local_model": "qwen3-coder:30b"},
    "2c": {"model": "sonnet", "think": True,  "local_model": ""},
    "3a": {"model": "opus",   "think": True,  "local_model": "qwen3-coder:30b"},
    "3b": {"model": "sonnet", "think": False, "local_model": "qwen3-coder:30b"},
    "3c": {"model": "sonnet", "think": True,  "local_model": ""},
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
    # Default mode: Stage 1 (prompt improvement) and Stage 2c / 3c (reviews)
    # use Claude; everything else runs locally. Review is a cross-file audit
    # that a local 30B model handles poorly, so the review stages stay on
    # Claude in default mode.
    return "claude" if sub_stage in ("1", "2c", "3c") else "local"


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


def _adaptive_timeout(prompt: str, planning_cfg: dict) -> int:
    """Scale per-call LLM timeout with both input size AND output ceiling.

    Local 30B models generate at ~20-30 tok/s, so a high max_tokens
    (default LLM_PLANNING_MAX_TOKENS=49152) can legitimately need
    30+ minutes of generation time alone. Formula:
        300 s base
      + 1 s per 50 input chars   (prompt streaming + input processing)
      + 1 s per 25 max_tokens    (conservative output-generation budget)
    planning_cfg['timeout'] is the floor so LLM_PLANNING_TIMEOUT raises
    the minimum rather than capping the max.
    """
    input_term = len(prompt) // 50
    output_term = int(planning_cfg.get("max_tokens", 8000)) // 25
    estimated = 300 + input_term + output_term
    return max(estimated, planning_cfg["timeout"])


def invoke_stage(
    prompt: str,
    sub_stage: str,
    *,
    args: argparse.Namespace,
    env: dict[str, str],
    planning_cfg: dict,
    thinking_file: Path | None = None,
    stream_to: Path | None = None,
) -> str:
    mode = get_mode(args)
    engine = get_engine(sub_stage, mode)
    think_pfx = _think_prefix(sub_stage, args, engine)
    full_prompt = think_pfx + prompt

    ts = datetime.datetime.now().strftime("%H:%M:%S")

    if engine == "claude":
        model = _resolve_claude_model(sub_stage, args.model)
        # Review stages (2c, 3c) need Claude to auto-apply Edit tool calls
        # without waiting for a human permission prompt — the pipeline runs
        # non-interactively. Other stages produce text only and don't need
        # elevated permissions.
        permission_mode = "acceptEdits" if sub_stage in ("2c", "3c") else None
        stream_note = "" if stream_to is None else f" stream->{stream_to.name}"
        cprint(
            f"    [claude model={model}"
            f"{' perm=acceptEdits' if permission_mode else ''}"
            f"{stream_note}] - ({ts})",
            Color.BLUE,
        )
        if args.dry_run:
            return f"[DRY RUN Claude output for stage {sub_stage}]"
        return invoke_claude(full_prompt, model=model, account=args.claude,
                             permission_mode=permission_mode,
                             stream_to=stream_to)

    # local engine
    local_model = _resolve_local_model(sub_stage, args.local_model, planning_cfg["model"])
    # Per-stage coder-model override disables thinking automatically (qwen3-coder
    # is not a reasoning model; sending think:true to it wastes budget).
    stage_think = planning_cfg["think"] and local_model == planning_cfg["model"]
    adaptive = _adaptive_timeout(full_prompt, planning_cfg)
    cprint(
        f"    [local: {local_model} @ {planning_cfg['endpoint']} "
        f"ctx={planning_cfg['num_ctx']} think={stage_think} "
        f"timeout={adaptive}s] - ({ts})",
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
        timeout=adaptive,
        temperature=planning_cfg["temperature"],
        think=stage_think,
        thinking_file=thinking_file if planning_cfg["save_thinking"] else None,
    )


# Short role descriptions for the "Models for this run" table.
_STAGE_ROLES: dict[str, str] = {
    "0":  "Summarize implemented plans",
    "1":  "Improve initial prompt",
    "2a": "Section plan generation",
    "2b": "Per-section architecture",
    "2c": "Architecture Plan review+auto-fix",
    "3a": "Step plan generation",
    "3b": "Per-step aider commands",
    "3c": "aidercommands review+auto-fix",
    "4":  "Aider execution",
    "5":  "fix_imports advisory",
}


def describe_models(args: argparse.Namespace, env: dict[str, str],
                    planning_cfg: dict) -> list[dict]:
    """Return one row per stage describing the engine, model, think-flag,
    and run-status that `invoke_stage` (stages 0-3c) / the subprocess
    launchers (stages 4, 5) will actually use given the current CLI args.

    Consumed by cli.py to render the 'Models for this run:' table at the
    start of every coding-mode invocation. Keeps the displayed values in
    lockstep with actual behaviour by calling the same resolvers
    invoke_stage calls."""
    mode = get_mode(args)
    skip: set[int] = set(getattr(args, "skip_stage", ()) or ())
    from_stage: int = getattr(args, "from_stage", 1)
    review: bool = getattr(args, "review", False)

    def classify_status(top_stage: int, is_review: bool = False) -> str:
        """Return a short status string for this sub-stage."""
        if top_stage in skip:
            return "skipped (--skip-stage)"
        if from_stage > top_stage:
            return f"skipped (--from-stage {from_stage})"
        if is_review and not review:
            return "skipped (needs --review)"
        if is_review and top_stage in skip:
            return "skipped (--skip-stage)"
        return "will run"

    rows: list[dict] = []

    # Sub-stages 0 through 3c — resolved via router helpers.
    for sub_stage in ("0", "1", "2a", "2b", "2c", "3a", "3b", "3c"):
        top_stage = int(sub_stage[0])
        is_review = sub_stage in ("2c", "3c")
        engine = get_engine(sub_stage, mode)
        if engine == "claude":
            model = _resolve_claude_model(sub_stage, args.model)
            think_default = STAGE_DEFAULTS[sub_stage]["think"]
            if args.ultrathink:
                think = True
            elif args.no_ultrathink:
                think = False
            else:
                think = think_default
            think_str = "on" if think else "off"
        else:
            local_model = _resolve_local_model(
                sub_stage, args.local_model, planning_cfg["model"]
            )
            model = local_model
            # Same logic as invoke_stage: think is disabled when a per-stage
            # coder-model override is in effect (qwen3-coder is not a
            # reasoning model).
            stage_think = planning_cfg["think"] and local_model == planning_cfg["model"]
            think_str = "on" if stage_think else "off"
        rows.append({
            "stage":  sub_stage,
            "role":   _STAGE_ROLES[sub_stage],
            "engine": "Claude" if engine == "claude" else "local",
            "model":  model,
            "think":  think_str,
            "status": classify_status(top_stage, is_review=is_review),
        })

    # Stage 4 — subprocess (run_aider.py). Model comes from LLM_AIDER_MODEL
    # (falls back to LLM_DEFAULT_MODEL via cfg.resolve_model).
    aider_model = cfg.resolve_model(env, "LLM_AIDER_MODEL", "qwen3-coder:30b")
    rows.append({
        "stage":  "4",
        "role":   _STAGE_ROLES["4"],
        "engine": "subprocess",
        "model":  f"ollama_chat/{aider_model}",
        "think":  "-",
        "status": classify_status(4),
    })

    # Stage 5 — subprocess (fix_imports.py). Shares LLM_AIDER_MODEL with
    # Stage 4 (LLM_FIX_IMPORTS_MODEL was deprecated; both always pointed at
    # the same coder model in practice).
    fix_model = cfg.resolve_model(env, "LLM_AIDER_MODEL", "qwen3-coder:30b")
    rows.append({
        "stage":  "5",
        "role":   _STAGE_ROLES["5"],
        "engine": "subprocess",
        "model":  fix_model,
        "think":  "-",
        "status": classify_status(5),
    })

    return rows
