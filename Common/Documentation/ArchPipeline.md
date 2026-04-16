# ArchPipeline.py

Unified orchestrator for the `LocalLLM_Pipeline` toolkit. Replaces three separate legacy scripts with a single Python entry point exposing three sub-commands: `analysis`, `debug`, and `coding`.

---

## Table of contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [General invocation](#general-invocation)
4. [Mode: analysis](#mode-analysis)
5. [Mode: debug](#mode-debug)
6. [Mode: coding](#mode-coding)
7. [Configuration — `Common/.env`](#configuration--commonenv)
8. [Progress tracking and resume](#progress-tracking-and-resume)
9. [Prompt templates](#prompt-templates)
10. [File layout](#file-layout)
11. [Exit codes](#exit-codes)
12. [Troubleshooting](#troubleshooting)
13. [Migration from legacy scripts](#migration-from-legacy-scripts)

---

## Overview

`ArchPipeline.py` consolidates three previously separate entry points:

| Legacy script | Replaced by |
|---|---|
| `LocalLLMAnalysis/Arch_Analysis_Pipeline.py` | `ArchPipeline.py analysis` |
| `LocalLLMDebug/Arch_Debug_Pipeline.ps1` | `ArchPipeline.py debug` |
| `LocalLLMCoding/Arch_Coding_Pipeline.ps1` | `ArchPipeline.py coding` |

Each mode preserves the legacy script's behavior, flag set (renamed to kebab-case), progress-file format, and output paths so a partially-completed legacy run can be resumed under the new orchestrator. PowerShell worker scripts under `LocalLLMAnalysis/`, `LocalLLMDebug/` are invoked as subprocesses and unchanged.

### Design goals

- **One entry point** — eliminates the cognitive tax of remembering three invocation patterns.
- **Python** — `argparse`, `subprocess`, exception handling, and HTTP clients are all cleaner than the equivalent PowerShell.
- **External prompt templates** — every LLM prompt lives in `_pipeline/prompts/*.md` as a plain text file, editable without touching Python.
- **No behavior regressions** — stage numbering, resume semantics, per-stage model selection, `ultrathink.` prefixing, and engine routing (Claude vs local LLM) all match the legacy scripts exactly.

---

## Prerequisites

| Requirement | Why |
|---|---|
| Python 3.10+ | Type-hint syntax used throughout |
| PowerShell (Windows PowerShell 5.1 or pwsh) | Worker scripts in analysis/debug modes are `.ps1` |
| An Ollama server reachable at `LLM_ENDPOINT` or `LLM_HOST:LLM_PORT` | All three modes call Ollama at some point |
| `claude` CLI on PATH | Only needed for coding-mode Claude stages (default: Stage 1) |
| `aider` CLI on PATH | Needed if you subsequently run `run_aider.py` against the coding-mode output (not by `ArchPipeline` itself) |

Run the orchestrator from the **target project's** directory, not from inside the toolkit. `Path.cwd()` is used as the repository root throughout.

```powershell
cd C:\Coding\MyProject
python C:\Coding\LocalLLM_Pipeline\Common\ArchPipeline.py <mode> [flags]
```

---

## General invocation

```
python ArchPipeline.py {analysis|debug|coding} [flags]
```

`--help` works at every level:

```powershell
python ArchPipeline.py --help              # list modes
python ArchPipeline.py analysis --help     # mode-specific flags
python ArchPipeline.py debug    --help
python ArchPipeline.py coding   --help
```

The orchestrator does not add any global flags; each mode declares its own flag set independently.

---

## Mode: `analysis`

Architecture analysis pipeline. Walks every subsection declared in `Common/.env` between `#Subsections begin` / `#Subsections end`, invoking six PowerShell workers per subsection to produce per-file docs, cross-reference indexes, Mermaid diagrams, an architecture overview, and a two-pass deep analysis.

### Flags

| Flag | Default | Purpose |
|---|---|---|
| `--dry-run` | off | Print commands without invoking workers. Logs what *would* run. |
| `--start-from N` | `1` | Skip subsections 1…N-1 (1-indexed). |
| `--skip-lsp` | off | Skip the one-time `generate_compile_commands.py` + `serena_extract.ps1` setup. Use after the first successful run, since those outputs don't change. |

### Steps (per subsection)

| # | Worker | Produces |
|---|---|---|
| 1 | `archgen_local.ps1` | `architecture/<rel>/*.md` — per-file documentation |
| 2 | `archxref.ps1` | `architecture/xref_index.md` — cross-reference index |
| 3 | `archgraph.ps1` | `architecture/*.mmd` — Mermaid diagrams |
| 4 | `arch_overview_local.ps1` | `architecture/OVERVIEW.md` — architecture overview |
| 5 | `archpass2_context.ps1` | `architecture/.pass2_context/` — per-file context bundles |
| 6 | `archpass2_local.ps1` | `architecture/architecture.md` — pass-2 synthesized analysis |

One-time setup (runs before the per-subsection loop, unless `--skip-lsp`):
- `generate_compile_commands.py` — builds `compile_commands.json` for Serena LSP
- `serena_extract.ps1` — extracts per-symbol context via Serena

### Output location

All output lands in `<cwd>/architecture/`. The legacy renaming-on-completion step (`architecture/` → `N. <subsection>`) was removed in the port; if you're processing multiple subsections, move or rename the output folder manually between runs.

### Legacy-compat skip marker

`is_subsection_completed()` still scans for `^\d+\. <name>` directories (the old rename convention). If you have legacy-renamed folders, subsections matching them are auto-skipped. New runs produce only an unrenamed `architecture/`.

### Example

```powershell
cd C:\Coding\MyProject
python C:\Coding\LocalLLM_Pipeline\Common\ArchPipeline.py analysis --dry-run
python C:\Coding\LocalLLM_Pipeline\Common\ArchPipeline.py analysis --skip-lsp
python C:\Coding\LocalLLM_Pipeline\Common\ArchPipeline.py analysis --start-from 3 --skip-lsp
```

---

## Mode: `debug`

Six-step debug pipeline. Runs data-flow, interface, test-gap, and bug-hunt analyses, then invokes the local LLM to fix each file with identified bugs, and finally archives the change log into `Implemented Plans/Bug Fix Changes N.md`.

### Flags

| Flag | Default | Purpose |
|---|---|---|
| `--target-dir DIR` | *required* | Source directory to analyze (e.g. `src/nmon`). |
| `--test-dir DIR` | `tests` | Test directory passed to `testgap_local.ps1`. |
| `--restart` | off | Ignore `.debug_progress` and start from step 1. |
| `--dry-run` | off | Print commands without running them. |

### Steps

| # | Stage | Source | Writes to |
|---|---|---|---|
| 1 | Data-flow analysis | `dataflow_local.ps1` | `architecture/DATA_FLOW.md` |
| 2 | Interface extraction | `interfaces_local.ps1` | `architecture/INTERFACES.md`, `architecture/interfaces/*.iface.md` |
| 3 | Test-gap analysis | `testgap_local.ps1` | `test_gaps/GAP_REPORT.md`, `test_gaps/src/*.gap.md` |
| 4 | Bug hunt | `bughunt_local.ps1` | `bug_reports/SUMMARY.md`, `bug_reports/src/*.md` |
| 5 | **Per-file bug fixing** (inline, local LLM) | `_pipeline/prompts/debug_fix.md` | Edits source files in place; appends summaries to `.debug_changes.md` |
| 6 | Archive | inline | `Implemented Plans/Bug Fix Changes N.md` (N auto-incremented) |

### Step 5 detail

Unlike steps 1–4, step 5 is inline Python (not a worker script). Each file with a bug report is processed as follows:

1. Read `bug_reports/src/<rel>.py.md`. If it matches "no significant bugs" / "no issues found" / short length → skip, log as clean.
2. Build the prompt from `debug_fix.md` + bug report + per-file interface contract + per-file test gap + system-wide data flow + the current source file contents.
3. Call `invoke_local_llm()` (default model: `LLM_MODEL` from `.env` — `qwen3-coder:30b`).
4. Expect the response to be a ```python fenced block containing the complete corrected file, followed by a summary.
5. Parse the fenced block and overwrite the source file. Append the summary to `.debug_changes.md`.
6. If parsing fails, dump the raw response to `debug_response.txt` and exit — source files are *never* overwritten with unparseable content.

### Analysis-pipeline dependency

Step 5 requires `architecture/INTERFACES.md` and `architecture/DATA_FLOW.md` to exist. Step 2 (`interfaces_local.ps1`) produces `INTERFACES.md`; step 1 (`dataflow_local.ps1`) produces `DATA_FLOW.md`. If either is missing when step 5 starts, it exits with a clear error. You typically don't hit this because steps 1–2 run first.

### Example

```powershell
cd C:\Coding\MyProject
python C:\Coding\LocalLLM_Pipeline\Common\ArchPipeline.py debug --target-dir src/nmon
python C:\Coding\LocalLLM_Pipeline\Common\ArchPipeline.py debug --target-dir src/nmon --restart
```

---

## Mode: `coding`

Four-stage planning pipeline that turns an `InitialPrompt.md` into an `aidercommands.md` ready to feed into `run_aider.py`. Routes each stage to either Claude Code or the local Ollama server based on mode and per-stage defaults.

### Flags

| Flag | Default | Purpose |
|---|---|---|
| `--target-dir DIR` | `<cwd>/LocalLLMCodePrompts` | Where to read `InitialPrompt.md` and write outputs. |
| `--claude ACCOUNT` | `Claude1` | Account identifier (maps to a `CLAUDE_CONFIG_DIR`). See [claude.py](../_pipeline/claude.py). |
| `--model NAME` | per-stage | Override Claude model for ALL Claude stages (`sonnet`, `opus`, or explicit tag). |
| `--local-model NAME` | per-stage | Override local model for ALL local stages. |
| `--local-endpoint URL` | `.env LLM_ENDPOINT` | Override Ollama endpoint URL. |
| `--local` | off | Use local Ollama for **every** stage (including Stage 1). |
| `--all-claude` | off | Use Claude Code for **every** stage. Mutually exclusive with `--local`. |
| `--ultrathink` | off | Force `ultrathink. ` prefix for ALL Claude stages. |
| `--no-ultrathink` | off | Disable `ultrathink. ` prefix for ALL Claude stages. |
| `--from-stage N` | `1` | Skip stages 0…N-1. Useful to rerun Stage 3 only. |
| `--skip-stage N [N…]` | — | Skip specific stage numbers entirely. |
| `--restart` | off | Ignore `.progress` and start fresh. |
| `--dry-run` | off | Print prompts without invoking LLMs. |
| `--force` | off | Skip overwrite-confirmation prompts. |

### Stages

| Stage | Purpose | Default engine | Default model | Ultrathink default | Per-stage local override |
|---|---|---|---|---|---|
| 0 | Summarize existing `Implemented Plans/*.md` into `Codebase Summary.md` | Claude | sonnet | off | — |
| 1 | Read `InitialPrompt.md`, produce `Implementation Planning Prompt.md` + `PromptUpdates.md` | Claude | sonnet | off | — |
| 2a | Produce `.section_plan.md` — a `SECTION N | Title | Desc` list | local | — | — | — |
| 2b | Per-section → append each section to `Architecture Plan.md` | local | `LLM_PLANNING_MODEL` (gemma4:26b) | off | — |
| 3a | Produce `.step_plan.md` — a `STEP N | Title | files` list | local | — | — | `qwen3-coder:30b` |
| 3b | Per-step → append each step (command + prompt) to `aidercommands.md` | local | — | off | `qwen3-coder:30b` |

The per-stage `local_model` override (applied to stages 3a/3b) automatically disables `think` because `qwen3-coder:30b` is not a reasoning model; sending `think:true` wastes the `num_predict` budget on empty reasoning. See `_invoke_stage()` in `coding.py`.

### Engine routing

The `mode` value determines which engine each stage uses:

| Mode | Set by | Stages on Claude | Stages on local |
|---|---|---|---|
| `default` | *no flags* | 1 | 0, 2a, 2b, 3a, 3b |
| `local` | `--local` | *(none)* | 0, 1, 2a, 2b, 3a, 3b |
| `allclaude` | `--all-claude` | 0, 1, 2a, 2b, 3a, 3b | *(none)* |

The mode is stored in `.progress` as `Mode=<value>`. If you resume with a different mode flag, the orchestrator refuses (`ERROR: saved progress used mode 'X' but current run uses 'Y'`) and tells you which flag to re-pass, or to use `--restart`.

### Architecture plan slicing (Stage 3b)

When `--local` is passed, Stage 3b trims the architecture plan per-step: it keeps only the sections whose `##` heading matches one of the step's file names, plus always-include sections (Project Structure, Data Model, Data Pipeline, Configuration, Dependencies, Build/Run, Testing). This keeps the prompt inside the local model's context window.

Default and `--all-claude` modes always send the *full* architecture plan to Stage 3b.

### Output files

All relative to `--target-dir` (default `LocalLLMCodePrompts/`):

| File | Produced by | Purpose |
|---|---|---|
| `Implementation Planning Prompt.md` | Stage 1 | Refined plan prompt, input to Stage 2a |
| `PromptUpdates.md` | Stage 1 | Critique of the original `InitialPrompt.md` |
| `Architecture Plan.md` | Stage 2b | Consolidated architecture (appended section-by-section) |
| `aidercommands.md` | Stage 3b | Aider commands + prompts (appended step-by-step) |
| `.section_plan.md` | Stage 2a | Ephemeral; deleted after Stage 2b finishes |
| `.step_plan.md` | Stage 3a | Ephemeral; deleted after Stage 3b finishes |
| `<file>.thinking.md` sidecars | when `LLM_SAVE_THINKING=true` | Reasoning tokens from thinking models; audit/debug only |
| `.progress` | progress tracker | Resume state |

Plus `Implemented Plans/Codebase Summary.md` at the **repo root** (not under target-dir), produced by Stage 0.

### Resume behavior

- If `.progress` has `LastCompleted=N`, the orchestrator auto-advances `--from-stage` to `N+1`.
- If a stage is partially complete (e.g. Stage 2b wrote 5 of 12 sections before crashing), the `SubStep=K` line in `.progress` tells the resumed run to skip sections 1…K and pick up at K+1.
- The partial `Architecture Plan.md` / `aidercommands.md` files on disk are **preserved** across restarts — each section/step is appended directly, so the on-disk file always reflects completed work.

### Examples

```powershell
cd C:\Coding\MyProject

# Default mode: Stage 1 on Claude, others on local
python C:\Coding\LocalLLM_Pipeline\Common\ArchPipeline.py coding

# Everything local (requires InitialPrompt.md)
python C:\Coding\LocalLLM_Pipeline\Common\ArchPipeline.py coding --local

# Everything on Claude
python C:\Coding\LocalLLM_Pipeline\Common\ArchPipeline.py coding --all-claude --model opus --ultrathink

# Regenerate only Stage 3
python C:\Coding\LocalLLM_Pipeline\Common\ArchPipeline.py coding --from-stage 3

# Start fresh
python C:\Coding\LocalLLM_Pipeline\Common\ArchPipeline.py coding --restart
```

---

## Configuration — `Common/.env`

Single source of truth for endpoints, model tags, context sizes, and subsection lists. Parsed by `_pipeline/config.py` as plain `KEY=VALUE` lines.

### Endpoint

| Key | Default | Used by |
|---|---|---|
| `LLM_ENDPOINT` | unset | If set, overrides `LLM_HOST`/`LLM_PORT`. Form: `http://HOST:PORT`. |
| `LLM_HOST` | `192.168.1.126` | Combined with `LLM_PORT` into a URL. |
| `LLM_PORT` | `11434` | Combined with `LLM_HOST`. |

### Models

| Key | Default | Used by |
|---|---|---|
| `LLM_PLANNING_MODEL` | `gemma4:26b` | coding stages 2a, 2b (thinking model) |
| `LLM_AIDER_MODEL` | `qwen3.5:27b` | `run_aider.py` (not this pipeline) |
| `LLM_FIX_IMPORTS_MODEL` | `qwen3-coder:30b` | `fix_imports.py` (not this pipeline) |
| `LLM_MODEL` | `qwen3-coder:30b` | debug mode Step 5; analysis workers |

Coding stages 3a and 3b use a hardcoded per-stage override of `qwen3-coder:30b` regardless of `LLM_PLANNING_MODEL`. See `STAGE_DEFAULTS` in [coding.py](../_pipeline/modes/coding.py).

### Context windows and generation budgets

| Key | Default | Purpose |
|---|---|---|
| `LLM_NUM_CTX` | `32768` | Default context for analysis/debug calls. |
| `LLM_PLANNING_NUM_CTX` | `65536` | Coding stages 2–3 context. |
| `LLM_PLANNING_MAX_TOKENS` | `49152` | `num_predict` for coding stages. Must be large enough for thinking + content combined. |
| `LLM_FIX_MAX_TOKENS` | `16384` | Debug Step 5 `num_predict`. |

### Timeouts

| Key | Default | Purpose |
|---|---|---|
| `LLM_TIMEOUT` | `300` | HTTP timeout for analysis/debug calls. |
| `LLM_PLANNING_TIMEOUT` | `1200` | Longer timeout for coding stages (thinking models generating 16k+ tokens can take 5–15 minutes). |

### Thinking-model support

| Key | Default | Purpose |
|---|---|---|
| `LLM_THINK` | `true` | Sends `think:true` on `/api/chat` for coding stages 2a/2b (only when the model equals `LLM_PLANNING_MODEL`). |
| `LLM_SAVE_THINKING` | `true` | Writes `<output>.thinking.md` sidecars with reasoning tokens. |

### Debug ↔ Analysis integration

| Key | Default | Purpose |
|---|---|---|
| `ARCHITECTURE_DIR` | `architecture` | Directory debug mode reads for `INTERFACES.md`, `DATA_FLOW.md`, `interfaces/*.iface.md`. Update if your analysis output lives elsewhere. |
| `SERENA_CONTEXT_DIR` | `architecture/.serena_context` | Per-file context bundles consumed by interface synthesis. |

### Subsections block (analysis mode only)

```
#Subsections begin
src
# ~16 Python files -- nmon package
#Subsections end
```

Each non-comment, non-blank line between the markers is a subsection path (relative to the repo root). Analysis mode processes one per iteration.

---

## Progress tracking and resume

Both debug and coding modes write progress files with the same schema so the legacy PowerShell scripts can read them mid-run and vice-versa.

### File format (`.progress` for coding, `.debug_progress` for debug)

```
LastCompleted=2
SubStep=7
Mode=default
TargetDir=src/nmon
Timestamp=2026-04-15 10:23:11
```

| Key | Meaning |
|---|---|
| `LastCompleted` | Highest completed top-level stage number. `-1` if no stages completed. |
| `SubStep` | Within-stage progress (sections in Stage 2b, steps in Stage 3b, files in debug Step 5). Absent when not applicable. |
| `Mode` | `default` / `local` / `allclaude` for coding; `debug` for debug. Guards against resuming with mismatched flags. |
| `TargetDir` | For debug mode — which source dir this progress file belongs to. |
| `Timestamp` | Informational, last save time. |

### Resume precedence

1. **`--restart`** — always wipes the progress file; starts fresh.
2. **Mode mismatch** — refuses to resume; prints the correct flag to use.
3. **`--from-stage N` explicitly passed** — overrides auto-advance.
4. **`LastCompleted` found** — auto-advances `--from-stage` to `LastCompleted + 1`.

Progress is wiped on successful completion so the next invocation doesn't auto-skip everything. Use `--restart` to re-run a fully-completed pipeline.

---

## Prompt templates

Every LLM prompt lives in `_pipeline/prompts/` as a plain `.md` file. They are read fresh on each invocation — no restart required after editing.

| File | Used by |
|---|---|
| `stage0_summarize.md` | Coding Stage 0 |
| `stage1_improve_prompt.md` | Coding Stage 1 |
| `stage2a_section_plan.md` | Coding Stage 2a |
| `stage2b_section.md` | Coding Stage 2b (with `SECTITLE` / `SECDESC` placeholders) |
| `stage3a_step_plan_head.md` + `..._tail.md` | Coding Stage 3a (architecture plan injected between head and tail) |
| `stage3b_step_head.md` + `..._tail.md` | Coding Stage 3b (architecture context injected between; with `STEPNUM` / `STEPTITLE` / `AIDERFILES` placeholders) |
| `debug_fix.md` | Debug Step 5 (with `SRCPATH` placeholder) |

### Placeholder substitution

Placeholders are uppercase tokens replaced via `str.replace()` at runtime:

| Token | Replaced with |
|---|---|
| `SECTITLE` | Section title from `.section_plan.md` |
| `SECDESC` | Section description |
| `STEPNUM` | Step number |
| `STEPTITLE` | Step title |
| `AIDERFILES` | Space-separated file list |
| `SRCPATH` | Source file path (debug mode) |

Add new placeholders by editing the mode module and the prompt file together.

---

## File layout

```
C:\Coding\LocalLLM_Pipeline\Common\
  ArchPipeline.py                       # entry point (argparse + dispatch)
  .env                                  # shared config
  Documentation\
    ArchPipeline.md                     # this document
  _pipeline\
    __init__.py
    config.py                           # .env parser, subsection parser
    ui.py                               # ANSI colors, Ctrl+Q, logging, banner()
    subprocess_runner.py                # streaming Popen wrapper, StepFailed/UserCancelled
    ollama.py                           # invoke_local_llm() — /api/chat client
    claude.py                           # invoke_claude() — CLI wrapper + account switching
    progress.py                         # ProgressFile class
    prompts\                            # see above
    modes\
      __init__.py
      analysis.py                       # analysis mode orchestration
      debug.py                          # debug mode orchestration (+ inline Step 5)
      coding.py                         # coding mode orchestration (stages 0–3)
```

### Module responsibilities

- **`config`** — only reads; never writes. Returns dicts/lists.
- **`ui`** — side-effectful (writes to stderr, sets up logging handlers).
- **`subprocess_runner`** — raises `StepFailed` on exit≠0, `UserCancelled` on exit=130.
- **`ollama`** — HTTP client with retry, thinking-budget diagnostics, content-sanity check. Raises `LLMError`.
- **`claude`** — subprocess wrapper. Raises `ClaudeError`.
- **`progress`** — `ProgressFile` class with `.read()` / `.save()` / `.clear()`.
- **`modes/*`** — each exposes `register(subparsers)` and `run(args)`. Entry point imports all three and wires them to the argparse subparsers.

---

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Pipeline completed successfully. |
| `1` | A step failed (`StepFailed`, `LLMError`, `ClaudeError`, missing required file). |
| `2` | Unexpected exception. |
| `130` | User cancelled via Ctrl+Q (only while a child process is running). |

---

## Troubleshooting

### "Empty response from LLM" / "Model exhausted budget inside <thinking>"

The local model consumed `num_predict` on reasoning and produced no content. Raise `LLM_PLANNING_MAX_TOKENS` and/or `LLM_PLANNING_NUM_CTX` in `Common/.env`. For thinking models, 16k tokens of reasoning per call is not uncommon — budget 32k+ of `num_predict`.

### "LLM returned suspiciously short/garbled content"

The model emitted one stop token (e.g. a single CJK character) as its entire content because thinking ate the full budget. Same fix as above. If it persists, switch the offending stage to a non-thinking coder model.

### "ERROR: Stage 3a produced no parseable STEP lines"

The model didn't emit the `STEP N | Title | files` format. Check `.step_plan.md` — if the model prose-dumped the architecture plan instead of the step list, that stage is a bad fit for the current model. The default-override to `qwen3-coder:30b` for Stage 3a usually resolves this.

### "Required file not found: architecture/INTERFACES.md"

Debug Step 5 requires the output of Step 2 (`interfaces_local.ps1`) and Step 1 (`dataflow_local.ps1`). Either they haven't run yet, or `ARCHITECTURE_DIR` in `.env` points to a different folder. If you have analysis output in a `N. <name>` folder (legacy rename), update `ARCHITECTURE_DIR` to match.

### "UnicodeDecodeError: 'charmap' codec"

Python on Windows defaults to `cp1252` for text reads. `config.py` uses UTF-8 explicitly — if you hit this elsewhere, check that new code reading `.env` or `.md` files passes `encoding='utf-8'`.

### Coding mode: "saved progress used mode 'X' but current run uses 'Y'"

`--restart` to start fresh, or pass the flag that matches the original run (e.g. the old run used `--local`, so the current run must too).

### Analysis mode: subsection auto-skipped unexpectedly

`is_subsection_completed()` matches folders like `1. src` (legacy rename). Remove the numbered folder (or rename it) to force re-processing.

### `claude` command not found

Claude stages call `claude --model <m> --output-format text` via subprocess. Install with `npm install -g @anthropic-ai/claude-code` (or the current installer). Verify `claude --help` runs before using coding mode's default engine routing.

---

## Migration from legacy scripts

### From `Arch_Analysis_Pipeline.py`

| Legacy | New |
|---|---|
| `python LocalLLMAnalysis\Arch_Analysis_Pipeline.py --dry-run` | `python Common\ArchPipeline.py analysis --dry-run` |
| `--start-from 2` | `--start-from 2` |
| `--skip-lsp` | `--skip-lsp` |

Progress is not shared (legacy had none). Output paths match exactly. **Behavior difference:** legacy renamed `architecture/` to `N. <subsection>` on completion; the new pipeline does not. See [mode: analysis](#mode-analysis) above.

### From `Arch_Debug_Pipeline.ps1`

| Legacy | New |
|---|---|
| `.\LocalLLMDebug\Arch_Debug_Pipeline.ps1 -TargetDir src/nmon` | `python Common\ArchPipeline.py debug --target-dir src/nmon` |
| `-TestDir tests` | `--test-dir tests` |
| `-Restart` | `--restart` |
| `-DryRun` | `--dry-run` |
| `-Claude Claude2` | *(no-op; Step 5 uses local LLM in both versions)* |
| `-Model` / `-Ultrathink` / `-NoUltrathink` | *(no-op; Claude was removed from Step 5 earlier)* |

`.debug_progress` format is identical. A run started under the legacy script can be resumed under the new one.

### From `Arch_Coding_Pipeline.ps1`

| Legacy | New |
|---|---|
| `.\LocalLLMCoding\Arch_Coding_Pipeline.ps1` | `python Common\ArchPipeline.py coding` |
| `-TargetDir .\LocalLLMCodePrompts_V2` | `--target-dir .\LocalLLMCodePrompts_V2` |
| `-Claude Claude2` | `--claude Claude2` |
| `-Model opus` | `--model opus` |
| `-LocalModel qwen3:32b` | `--local-model qwen3:32b` |
| `-LocalEndpoint http://localhost:11434` | `--local-endpoint http://localhost:11434` |
| `-Local` | `--local` |
| `-AllClaude` | `--all-claude` |
| `-Ultrathink` / `-NoUltrathink` | `--ultrathink` / `--no-ultrathink` |
| `-FromStage 3` | `--from-stage 3` |
| `-SkipStage 1,2` | `--skip-stage 1 2` |
| `-Restart` | `--restart` |
| `-DryRun` | `--dry-run` |
| `-Force` | `--force` |

`.progress` format is identical; a mid-run resume across legacy/new works.

---

## See also

- `../_pipeline/ollama.py` — Ollama client implementation.
- `../_pipeline/claude.py` — Claude CLI wrapper and account map.
- `../.env` — runtime configuration.
- `../../LocalLLMCoding/run_aider.py` — downstream consumer of `aidercommands.md`.
- `../../LocalLLMCoding/fix_imports.py` — post-generation import-error repair (independent of `ArchPipeline`).
