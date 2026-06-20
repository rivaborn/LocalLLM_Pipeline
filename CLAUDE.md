# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A toolkit (not a deployable app) that drives **local LLMs (vLLM via LLMConfig, or Ollama) + Claude
Code** through three multi-stage pipelines run against *some other* target codebase:

- **coding**   — turn an `InitialPrompt.md` into an `Architecture Plan.md` + `aidercommands.md`, then execute it with aider.
- **analysis** — generate per-file docs, cross-refs, Mermaid diagrams, and an architecture overview for each subsection.
- **debug**    — data-flow / interface / test-gap / bug-hunt analysis, then propose+apply fixes.

You run the pipeline *from the target project's directory*; cwd is treated as the repo root for path
resolution. The toolkit scripts live at a fixed path and locate themselves via `__file__`.

## Hard rules (do not violate without explicit user OK)

- **No emojis** anywhere — source, docs, commits, CLI output, chat.
- **No defensive programming.** No try/except for can't-happen cases, no input validation in trusted internal code. Validate only at system boundaries (user input, subprocess output, external APIs).
- **No backwards-compat shims.** No `# removed` comments, dead re-exports, or renamed `_unused` vars — delete instead.
- **Comments are rare** — only when the WHY is non-obvious (hidden constraint, subtle invariant, bug workaround). Never explain WHAT. No "added for X flow" notes; they rot.
- **Don't add features beyond the task.** No speculative helpers or premature abstractions.
- **Never `--no-verify` / `--no-gpg-sign`.** Fix pre-commit hook failures, don't bypass.
- **Read `Common/Documentation/<module>.md` before editing that module**, and update it in the same task when behavior changes.
- Confirm before risky/irreversible actions (destructive git, force-push, deleting files, pushing, closing PRs). Never force-push `main`. Create new commits, don't amend unless asked. No `Co-Authored-By` unless requested.

## Commands

```bash
# Run a pipeline (cd into the TARGET project first; cwd = its repo root)
python <toolkit>/Common/ArchPipeline.py            # = "all": coding -> analysis -> debug
python <toolkit>/Common/ArchPipeline.py coding   --initial-prompt ./InitialPrompt.md
python <toolkit>/Common/ArchPipeline.py analysis
python <toolkit>/Common/ArchPipeline.py debug    --target-dir src/<pkg>

# Dry run any mode (prints what would run; no workers/LLMs invoked)
python <toolkit>/Common/ArchPipeline.py <mode> --dry-run

# Tests (no root pytest config; run per-directory)
cd LocalLLMCoding   && pytest test_sanity.py -v
cd LocalLLMAnalysis && pytest test_arch_analysis_pipeline.py -v
```

Useful coding-mode flags: `--local` (everything local), `--all-claude` (everything Claude),
`--from-stage N`, `--skip-stage N...`, `--from-step N` (resume aider mid-`aidercommands.md`),
`--review` (enable Stage 2c/3c Claude review+auto-fix), `--restart`, `--package-name <NAME>`,
`--claude Claude1|Claude2`. Resumes are automatic via progress files (`.debug_progress` etc.); pass
`--restart` to ignore them.

## Architecture

**Entry point:** `Common/ArchPipeline.py` is a thin argparse shim that registers four subcommands
(`all`, `coding`, `analysis`, `debug`) and inserts `Common/` onto `sys.path` so the `_pipeline`
package imports regardless of invocation cwd. `all` is the default when no subcommand is given.

**Python orchestrator:** `Common/_pipeline/`
- `config.py` — `.env` loader (UTF-8), subsection parser (`#Subsections begin/end` block), backend selection (`resolve_backend` -> `vllm`/`ollama`), backend-aware endpoint resolution, and `resolve_model(role_key -> LLM_DEFAULT_MODEL -> fallback)`. All backend/model/endpoint precedence lives here; PowerShell workers mirror the same chain via `Get-LLMBackend`/`Get-LLMModel`.
- `claude.py` — wraps the `claude` CLI. Account switching via `CLAUDE_CONFIG_DIR` (`Claude1`/`Claude2`, overridable per-machine through `CLAUDE1_CONFIG_DIR`/`CLAUDE2_CONFIG_DIR` in `.env`). Supports batch (`--output-format text`) and a streaming NDJSON path (`stream_to=`) that preserves partial output on rate-limit so resume works. Only assistant `text_delta` events are persisted — thinking blocks are deliberately excluded so they don't poison resume prompts.
- `ollama.py` — local LLM invocation; dispatches to the vLLM gateway (`/v1/chat/completions`) or native Ollama (`/api/chat`) by backend. `subprocess_runner.py` — PowerShell/subprocess launcher with `StepFailed`/`UserCancelled` (Ctrl+Q cancel, checked between steps). `progress.py` — resume state. `ui.py` — colored output + Windows ANSI. `symbols.py`, `lsp_pyright.py` — symbol/LSP helpers.
- `modes/` — one module per pipeline. Each exposes `register(subparsers)` + `run(args)`.
  - `all_modes.py` — runs coding→analysis→debug, threading shared args (`--from-section`, `--continue-on-error`); defaults `--debug-target-dir` from `--package-name`.
  - `analysis.py` — iterates `.env` subsections, runs 6 PowerShell workers (`archgen_local`, `archxref`, `archgraph`, `arch_overview_local`, `archpass2_context`, `archpass2_local`) in `LocalLLMAnalysis/`, all on one local model.
  - `debug/` — 6 steps: 4 PowerShell workers (`dataflow`, `interfaces`, `testgap`, `bughunt`) in `LocalLLMDebug/`, then inline `fix_bugs.py` (step 5), then archive proposals to `Implemented Plans/` (step 6).
  - `coding/` — the most complex; see below.
- `prompts/` — markdown prompt templates for stages 0–3c (e.g. `stage2b_section.md`, `stage3b_step_*.md`).

**PowerShell workers:** `LocalLLMAnalysis/`, `LocalLLMDebug/`, `LocalLLMCoding/` each hold the
per-step `.ps1` scripts (+ their `*_prompt.txt`) that the orchestrator shells out to. `Common/*.ps1`
(`llm_common.ps1`, `llm_core.ps1`, `file_helpers.ps1`) are the shared PowerShell library.

**Stage 4/5 subprocesses:** `LocalLLMCoding/run_aider.py` (executes `aidercommands.md` per-step via
aider) and `fix_imports.py` are launched as subprocesses, not in-process, because `run_aider`
maintains long-lived state (pyright LSP, ctags cache). Their logic lives in `LocalLLMCoding/_aider/`.

**Config flows through `Common/.env`** (gitignored; template `.env.example`). It holds the backend
(`LLM_BACKEND`) and endpoint, per-role model overrides (`LLM_DEFAULT_MODEL` +
`LLM_PLANNING_MODEL`/`LLM_AIDER_MODEL`/…), token/ctx/timeout budgets, the target codebase
`PRESET`/`CODEBASE_DESC`, file-filter regexes, and the `#Subsections begin/end` list that analysis iterates.

**`legacy/`** holds the pre-`ArchPipeline.py` entry points — unmaintained, reference only.

## LLM backend (vLLM by default, Ollama selectable)

The local LLM is reached through **LLMConfig** (`C:\Coding\rivaborn\LLMConfig`) — a per-GPU arbiter on
the ollama-host (`http://<host>:11430`) that wraps both vLLM and Ollama behind one OpenAI `/v1` gateway
with **auto-load** (the first request for a model swaps it onto the GPU with full-VRAM packing, then
forwards). The pipeline runs sequentially, so a stage needing a different model triggers a clean
blocking load inside the gateway — no pre-load/arbitration code is needed here.

- `LLM_BACKEND` (default `vllm`) selects the path: `vllm` -> gateway `/v1/chat/completions`; `ollama` -> native `/api/chat`. Selection in `config.py`; dispatch in `ollama.py` (Python) and `llm_core.ps1` (PowerShell, via `Get-LLMBackend`).
- **Model id formats differ:** vLLM served-names use `-` (`qwen3-coder-30b`, `qwen3.5-27b`); Ollama tags use `:` (`qwen3-coder:30b`). The gateway routes by format — use served-names with `vllm`, tags with `ollama`.
- **Context is fixed per vLLM alias at serve time** (both pipeline aliases serve at 65536), not per-request: `num_ctx` / `LLM_PLANNING_NUM_CTX` are ignored on the vLLM path; keep `max_tokens` under the alias ceiling.
- **`think` / thinking-sidecars are inert on vLLM** (the aliases set no `--reasoning-parser`); they apply only to `LLM_BACKEND=ollama`.

## Coding mode — stage routing (the coupled part)

`modes/coding/router.py` dispatches each sub-stage to Claude or the local backend (vLLM/Ollama).
Sub-stages are `0, 1, 2a, 2b, 2c, 3a, 3b, 3c` (planning, via `invoke_stage`) plus `4, 5` (subprocesses):

- **Default mode:** stages `1`, `2c`, `3c` run on Claude (prompt improvement + cross-file review — a 30B local model handles these poorly); everything else runs local.
- `--local` forces all local; `--all-claude` forces all Claude.
- `STAGE_DEFAULTS` holds per-sub-stage model + think flag + local-model override.
- **Review stages `2c`/`3c` must pass `permission_mode="acceptEdits"` to `invoke_claude`** — the pipeline is non-interactive and otherwise hangs on Edit-tool permission prompts.
- A per-stage coder-model override auto-disables thinking on the Ollama path (qwen3-coder isn't a reasoning model); thinking is inert on vLLM regardless.

### When adding or modifying a coding-mode stage, touch ALL of these (they're coupled):

- `STAGE_DEFAULTS` + `_STAGE_ROLES` in `modes/coding/router.py`
- `get_engine()` if default-mode routing changes; `describe_models()` if new status cases appear (it must call the same resolvers `invoke_stage` does, so the "Models for this run" table stays truthful)
- Prompt template in `Common/_pipeline/prompts/stage<N><letter>_*.md`
- CLI wiring in `modes/coding/cli.py` (skip-stage / from-stage gating)
- Stage tables in `Common/Documentation/ArchPipeline.md`
- Living record `Common/PromptOptimizations.md` (Part 1 = implemented defenses, Part 2 = proposed) — update alongside ANY prompt-template change; it tracks what guards against LLM drift.

### Don't soften these guards (they catch real drift, not theoretical):

- `_detect_aider_drift` / `_is_src_path` in `LocalLLMCoding/_aider/runner.py` — stops test-only steps from writing to `src/`.
- `parse_steps` in `LocalLLMCoding/_aider/parser.py` — fails loudly on malformed `## Step N` sections by design; do not change to a silent skip.

## Analysis ↔ debug integration

When the analysis pipeline has run, debug workers consume its outputs (`xref_index.md`,
`architecture.md`, per-file `.serena_context.txt`) via `ARCHITECTURE_DIR` / `SERENA_CONTEXT_DIR` in
`.env`. All integrations skip gracefully when the expected file is missing. The usage workflow for
these artifacts is in `Common/Documentation/Cline.md`.
