# ArchPipeline.py

Unified orchestrator for the `LocalLLM_Pipeline` toolkit. Replaces three separate legacy scripts with a single Python entry point exposing three sub-commands: `analysis`, `debug`, and `coding`.

---

## Table of contents

1. [Overview](#overview)
2. [Stages at a glance](#stages-at-a-glance)
3. [Prerequisites](#prerequisites)
4. [General invocation](#general-invocation)
5. [Mode: analysis](#mode-analysis)
6. [Mode: debug](#mode-debug)
7. [Mode: coding](#mode-coding)
8. [Configuration — `Common/.env`](#configuration--commonenv)
9. [Progress tracking and resume](#progress-tracking-and-resume)
10. [Prompt templates](#prompt-templates)
11. [File layout](#file-layout)
12. [Exit codes](#exit-codes)
13. [Troubleshooting](#troubleshooting)
14. [Migration from legacy scripts](#migration-from-legacy-scripts)

---

## Overview

`ArchPipeline.py` consolidates three previously separate entry points:

| Legacy script                                  | Replaced by                   |
| ---------------------------------------------- | ----------------------------- |
| `LocalLLMAnalysis/Arch_Analysis_Pipeline.py`   | `ArchPipeline.py analysis`    |
| `LocalLLMDebug/Arch_Debug_Pipeline.ps1`        | `ArchPipeline.py debug`       |
| `LocalLLMCoding/Arch_Coding_Pipeline.ps1`      | `ArchPipeline.py coding`      |

Each mode preserves the legacy script's behavior, flag set (renamed to kebab-case), progress-file format, and output paths so a partially-completed legacy run can be resumed under the new orchestrator. PowerShell worker scripts under `LocalLLMAnalysis/`, `LocalLLMDebug/` are invoked as subprocesses and unchanged.

### Design goals

- **One entry point** — eliminates the cognitive tax of remembering three invocation patterns.
- **Python** — `argparse`, `subprocess`, exception handling, and HTTP clients are all cleaner than the equivalent PowerShell.
- **External prompt templates** — every LLM prompt lives in `_pipeline/prompts/*.md` as a plain text file, editable without touching Python.
- **No behavior regressions** — stage numbering, resume semantics, per-stage model selection, `ultrathink.` prefixing, and engine routing (Claude vs local LLM) all match the legacy scripts exactly.

---

## Stages at a glance

One-row-per-stage summary across all three modes. Click through to each mode section for full details.

### `analysis` mode — per-subsection steps

| #     | Name                                                                           | Engine        | Produces                                                             |
| ----- | ------------------------------------------------------------------------------ | ------------- | -------------------------------------------------------------------- |
| 0     | `generate_compile_commands` + `serena_extract` (one-time, unless `--skip-lsp`) | local tooling | `compile_commands.json`, per-symbol Serena context                   |
| 1     | `archgen_local`                                                                | local LLM     | `architecture/<rel>/*.md` — per-file documentation                   |
| 2     | `archxref`                                                                     | local tool    | `architecture/xref_index.md` — cross-reference index                 |
| 3     | `archgraph`                                                                    | local tool    | `architecture/*.mmd` — Mermaid diagrams                              |
| 4     | `arch_overview_local`                                                          | local LLM     | `architecture/OVERVIEW.md` — architecture overview                   |
| 5     | `archpass2_context`                                                            | local tool    | `architecture/.pass2_context/` — per-file context bundles            |
| 6     | `archpass2_local`                                                              | local LLM     | `architecture/architecture.md` — pass-2 synthesized analysis         |

### `debug` mode — six-step pipeline

| #     | Name                   | Engine      | Produces                                                                    |
| ----- | ---------------------- | ----------- | --------------------------------------------------------------------------- |
| 1     | Data-flow analysis     | local LLM   | `architecture/DATA_FLOW.md`                                                 |
| 2     | Interface extraction   | local LLM   | `architecture/INTERFACES.md`, `architecture/interfaces/*.iface.md`          |
| 3     | Test-gap analysis      | local LLM   | `test_gaps/GAP_REPORT.md`, `test_gaps/src/*.gap.md`                         |
| 4     | Bug hunt               | local LLM   | `bug_reports/SUMMARY.md`, `bug_reports/src/*.md`                            |
| 5     | Per-file bug fixing    | local LLM   | Edits source files in place; appends summaries to `.debug_changes.md`       |
| 6     | Archive                | inline      | `Implemented Plans/Bug Fix Changes N.md` (N auto-incremented)               |

### `coding` mode — planning + review + execution

| #     | Name                                             | Engine (default mode)   | Produces                                                                                   |
| ----- | ------------------------------------------------ | ----------------------- | ------------------------------------------------------------------------------------------ |
| 0     | Summarize implemented plans                      | Claude                  | `Implemented Plans/Codebase Summary.md` (repo root)                                        |
| 1     | Improve initial prompt                           | Claude                  | `Implementation Planning Prompt.md`, `PromptUpdates.md`                                    |
| 2a    | Section-plan generation                          | local LLM               | `.section_plan.md` (ephemeral — `SECTION N \| Title \| Desc` list)                         |
| 2b    | Per-section architecture                         | local LLM               | `Architecture Plan.md` (appended section-by-section)                                       |
| 2c    | **Audit + fix review of arch plan** (opt-in)     | Claude                  | `Architecture Plan.review.md`, in-place patches to `Architecture Plan.md`, `.bak` snapshot |
| 3a    | Step-plan generation                             | local LLM               | `.step_plan.md` (ephemeral — `STEP N \| Title \| files` list)                              |
| 3b    | Per-step aider commands                          | local LLM               | `aidercommands.md` (appended step-by-step)                                                 |
| 3c    | **Audit + fix review of aidercommands** (opt-in) | Claude                  | `aidercommands.review.md`, in-place patches to `aidercommands.md`, `.bak` snapshot         |
| 4     | Run aider                                        | `run_aider.py`          | Source files under `src/` and `tests/` per step                                            |
| 5     | Fix imports                                      | `fix_imports.py`        | Edits imports in the generated package to resolve renames/misses                           |

---

## Prerequisites

| Requirement                                                               | Why                                                                     |
| ------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| Python 3.10+                                                              | Type-hint syntax used throughout                                        |
| PowerShell (Windows PowerShell 5.1 or pwsh)                               | Worker scripts in analysis/debug modes are `.ps1`                       |
| An Ollama server reachable at `LLM_ENDPOINT` or `LLM_HOST:LLM_PORT`       | All three modes call Ollama at some point                               |
| `claude` CLI on PATH                                                      | Needed for coding-mode Claude stages (Stage 1 + Stage 3c review)        |
| `aider` CLI on PATH                                                       | Needed for Stage 4 (`run_aider.py`) — not by `ArchPipeline` directly    |

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

| Flag               | Default   | Purpose                                                                                                                    |
| ------------------ | --------- | -------------------------------------------------------------------------------------------------------------------------- |
| `--dry-run`        | off       | Print commands without invoking workers. Logs what *would* run.                                                            |
| `--start-from N`   | `1`       | Skip subsections 1…N-1 (1-indexed).                                                                                        |
| `--skip-lsp`       | off       | Skip the one-time `generate_compile_commands.py` + `serena_extract.ps1` setup. Use after the first successful run.         |

### Steps (per subsection)

| #   | Worker                      | Produces                                                        |
| --- | --------------------------- | --------------------------------------------------------------- |
| 1   | `archgen_local.ps1`         | `architecture/<rel>/*.md` — per-file documentation              |
| 2   | `archxref.ps1`              | `architecture/xref_index.md` — cross-reference index            |
| 3   | `archgraph.ps1`             | `architecture/*.mmd` — Mermaid diagrams                         |
| 4   | `arch_overview_local.ps1`   | `architecture/OVERVIEW.md` — architecture overview              |
| 5   | `archpass2_context.ps1`     | `architecture/.pass2_context/` — per-file context bundles       |
| 6   | `archpass2_local.ps1`       | `architecture/architecture.md` — pass-2 synthesized analysis    |

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

| Flag                 | Default      | Purpose                                                          |
| -------------------- | ------------ | ---------------------------------------------------------------- |
| `--target-dir DIR`   | *required*   | Source directory to analyze (e.g. `src/nmon`).                   |
| `--test-dir DIR`     | `tests`      | Test directory passed to `testgap_local.ps1`.                    |
| `--restart`          | off          | Ignore `.debug_progress` and start from step 1.                  |
| `--dry-run`          | off          | Print commands without running them.                             |

### Steps

| #   | Stage                                         | Source                              | Writes to                                                                 |
| --- | --------------------------------------------- | ----------------------------------- | ------------------------------------------------------------------------- |
| 1   | Data-flow analysis                            | `dataflow_local.ps1`                | `architecture/DATA_FLOW.md`                                               |
| 2   | Interface extraction                          | `interfaces_local.ps1`              | `architecture/INTERFACES.md`, `architecture/interfaces/*.iface.md`        |
| 3   | Test-gap analysis                             | `testgap_local.ps1`                 | `test_gaps/GAP_REPORT.md`, `test_gaps/src/*.gap.md`                       |
| 4   | Bug hunt                                      | `bughunt_local.ps1`                 | `bug_reports/SUMMARY.md`, `bug_reports/src/*.md`                          |
| 5   | **Per-file bug fixing** (inline, local LLM)   | `_pipeline/prompts/debug_fix.md`    | Edits source files in place; appends summaries to `.debug_changes.md`     |
| 6   | Archive                                       | inline                              | `Implemented Plans/Bug Fix Changes N.md` (N auto-incremented)             |

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

Multi-stage planning pipeline that turns an `InitialPrompt.md` into an `aidercommands.md` ready to feed into `run_aider.py`. Routes each stage to either Claude Code or the local Ollama server based on mode and per-stage defaults. An opt-in Stage 3c runs a Claude audit + in-place fix of `aidercommands.md` before Stage 4 executes.

### Flags

| Flag                     | Default                     | Purpose                                                                                                                                                                                                                                                                                                                  |
| ------------------------ | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `--target-dir DIR`       | `<cwd>/LocalLLMCodePrompts` | Where to read `InitialPrompt.md` and write outputs.                                                                                                                                                                                                                                                                      |
| `--claude ACCOUNT`       | `Claude1`                   | Account identifier (maps to a `CLAUDE_CONFIG_DIR`). See [claude.py](../_pipeline/claude.py).                                                                                                                                                                                                                             |
| `--model NAME`           | per-stage                   | Override Claude model for ALL Claude stages (`sonnet`, `opus`, or explicit tag).                                                                                                                                                                                                                                         |
| `--local-model NAME`     | per-stage                   | Override local model for ALL local stages.                                                                                                                                                                                                                                                                               |
| `--local-endpoint URL`   | `.env LLM_ENDPOINT`         | Override Ollama endpoint URL.                                                                                                                                                                                                                                                                                            |
| `--local`                | off                         | Use local Ollama for **every** stage (including Stage 1). Mutually exclusive with `--all-claude`.                                                                                                                                                                                                                        |
| `--all-claude`           | off                         | Use Claude Code for **every** stage. Mutually exclusive with `--local`.                                                                                                                                                                                                                                                  |
| `--ultrathink`           | off                         | Force `ultrathink. ` prefix for ALL Claude stages.                                                                                                                                                                                                                                                                       |
| `--no-ultrathink`        | off                         | Disable `ultrathink. ` prefix for ALL Claude stages.                                                                                                                                                                                                                                                                     |
| `--from-stage N`         | `1`                         | Skip stages 0…N-1. Useful to rerun Stage 3 only.                                                                                                                                                                                                                                                                         |
| `--skip-stage N [N…]`    | —                           | Skip specific stage numbers entirely.                                                                                                                                                                                                                                                                                    |
| `--restart`              | off                         | Ignore `.progress` and start fresh.                                                                                                                                                                                                                                                                                      |
| `--dry-run`              | off                         | Print prompts without invoking LLMs.                                                                                                                                                                                                                                                                                     |
| `--force`                | off                         | Skip overwrite-confirmation prompts.                                                                                                                                                                                                                                                                                     |
| `--review`               | off                         | Run Claude audit + auto-fix at TWO checkpoints: Stage 2c (after Arch Plan generation; audits vs Planning Prompt) and Stage 3c (after aidercommands generation). Each stage BLOCKS the next generation stage on remaining blocking findings. Pre-patch snapshots saved to `.bak` files; audit logs to `.review.md` files. |

### Stages

| Stage   | Purpose                                                                                      | Default engine   | Default model                     | Ultrathink default   | Per-stage local override   |
| ------- | -------------------------------------------------------------------------------------------- | ---------------- | --------------------------------- | -------------------- | -------------------------- |
| 0       | Summarize existing `Implemented Plans/*.md` into `Codebase Summary.md`                       | Claude           | sonnet                            | off                  | —                          |
| 1       | Read `InitialPrompt.md`, produce `Implementation Planning Prompt.md` + `PromptUpdates.md`    | Claude           | sonnet                            | off                  | —                          |
| 2a      | Produce `.section_plan.md` — a `SECTION N \| Title \| Desc` list                             | local            | `LLM_PLANNING_MODEL`              | on                   | `qwen3-coder:30b`          |
| 2b      | Per-section → append each section to `Architecture Plan.md`                                  | local            | `LLM_PLANNING_MODEL`              | on                   | `qwen3-coder:30b`          |
| 2c      | (opt-in) Audit `Architecture Plan.md` + apply safe fixes; block Stage 3 on remaining issues  | Claude           | sonnet                            | on                   | —                          |
| 3a      | Produce `.step_plan.md` — a `STEP N \| Title \| files` list                                  | local            | `qwen3-coder:30b` (per-stage)     | on                   | `qwen3-coder:30b`          |
| 3b      | Per-step → append each step (command + prompt) to `aidercommands.md`                         | local            | `qwen3-coder:30b` (per-stage)     | off                  | `qwen3-coder:30b`          |
| 3c      | (opt-in) Audit `aidercommands.md` + apply safe fixes; block Stage 4 on remaining issues      | Claude           | sonnet                            | on                   | —                          |
| 4       | Run aider per step via `run_aider.py`                                                        | subprocess       | —                                 | —                    | —                          |
| 5       | Fix imports via `fix_imports.py`                                                             | subprocess       | —                                 | —                    | —                          |

The per-stage `local_model` override (applied to stages 3a/3b) automatically disables `think` because `qwen3-coder:30b` is not a reasoning model; sending `think:true` wastes the `num_predict` budget on empty reasoning. See `router.py::invoke_stage()`.

### Engine routing

The `mode` value determines which engine each stage uses:

| Mode          | Set by            | Stages on Claude                         | Stages on local                          |
| ------------- | ----------------- | ---------------------------------------- | ---------------------------------------- |
| `default`     | *no flags*        | 1, 2c, 3c                                | 0, 2a, 2b, 3a, 3b                        |
| `local`       | `--local`         | *(none)*                                 | 0, 1, 2a, 2b, 2c, 3a, 3b, 3c             |
| `allclaude`   | `--all-claude`    | 0, 1, 2a, 2b, 2c, 3a, 3b, 3c             | *(none)*                                 |

Stages 2c and 3c run only when `--review` is passed. In `default` and `allclaude` modes they always use Claude (cross-file audit quality suffers on small local models). In `--local` mode they route to the local LLM to respect the all-local request, but review quality will be lower.

The mode is stored in `.progress` as `Mode=<value>`. If you resume with a different mode flag, the orchestrator refuses (`ERROR: saved progress used mode 'X' but current run uses 'Y'`) and tells you which flag to re-pass, or to use `--restart`.

### Architecture plan slicing (Stage 3b)

When `--local` is passed, Stage 3b trims the architecture plan per-step: it keeps only the sections whose `##` heading matches one of the step's file names, plus always-include sections (Project Structure, Data Model, Data Pipeline, Configuration, Dependencies, Build/Run, Testing). This keeps the prompt inside the local model's context window.

Test-step file names (`test_<stem>.py`) additionally match progressively-stripped variants of their stem (`<stem>.py`, and if `<stem>` contains underscores, each trailing-segment form like `gpu_monitor.py` → `monitor.py`). This routes the corresponding production-module section — and any `### Testing strategy` subsection it contains — to test steps. Accepts minor over-match for modules sharing a basename (e.g. `gpu/monitor.py` + `llm/monitor.py`); disambiguation signal comes from the step title and target file.

Default and `--all-claude` modes always send the *full* architecture plan to Stage 3b.

### Stage 2c — review + auto-fix of Architecture Plan.md

Opt-in via `--review` (the same flag that enables Stage 3c). Runs immediately after Stage 2 completes, or whenever `Architecture Plan.md` already exists and `--review` is passed (even if Stage 2 is skipped in the current invocation). Claude reads `Implementation Planning Prompt.md` and `Architecture Plan.md` directly via its Read tool and audits for 12 failure classes specific to the arch plan:

| Class   | Name                                | Auto-fixed?   | Blocking?   |
| ------- | ----------------------------------- | ------------- | ----------- |
| A       | OPEN_QUESTIONS_LEAKAGE              | yes           | yes         |
| B       | MISSING_DESIGN_DECISIONS            | yes           | yes         |
| C       | DUPLICATE_SYMBOL                    | yes           | yes         |
| D       | SIGNATURE_DRIFT                     | yes           | yes         |
| E       | IMPORT_PATH_DRIFT                   | yes           | yes         |
| F       | PHANTOM_MODULE                      | no            | yes         |
| G       | CONSTANT_MISPLACED                  | yes           | yes         |
| H       | HEADING_FORMAT_DRIFT                | yes           | yes         |
| I       | MISSING_IMPORTS_BULLET              | yes           | no          |
| J       | MISSING_TESTING_STRATEGY            | yes           | yes         |
| K       | STUB_METHOD_BODIES                  | yes           | yes         |
| L       | PROJECT_STRUCTURE_SCOPE_VIOLATION   | yes           | no          |

Before invoking Claude, the Python wrapper snapshots `Architecture Plan.md` → `Architecture Plan.md.bak`. Claude emits the same five-section report format as Stage 3c (`SUMMARY`, `FINDINGS`, `PATCHES_APPLIED`, `MANUAL_REMAINING`, `VERDICT`) written to `Architecture Plan.review.md`. `PASS` lets Stage 3 proceed; `BLOCK` halts the pipeline with exit 1.

Rollback: `mv "Architecture Plan.md.bak" "Architecture Plan.md"`.

### Stage 3c — review + auto-fix of aidercommands.md

Opt-in via `--review`. Invoked after Stage 3 completes (whether in the current run or previously resumed). Claude reads `Architecture Plan.md` and `aidercommands.md` directly (via its Read tool — neither is inlined into the prompt) and audits for these failure classes:

| Class   | Name                     | Auto-fixed?                            | Blocking?   |
| ------- | ------------------------ | -------------------------------------- | ----------- |
| A       | COVERAGE_GAP             | no                                     | yes         |
| B       | TEST_DRIFT               | yes                                    | yes         |
| C       | STUB_SKELETON            | yes                                    | yes         |
| D       | PIPELINE_OUTPUT_STEP     | yes                                    | yes         |
| E       | TITLE_AMBIGUITY          | yes                                    | no          |
| F       | PROMPT_FILE_MISMATCH     | yes                                    | yes         |
| G       | SYMBOL_DRIFT             | yes (if canonical form is unambiguous) | no          |
| H       | SIGNATURE_DRIFT          | no                                     | yes         |
| I       | ORDER_VIOLATION          | no                                     | no          |

Before invoking Claude, the Python wrapper snapshots `aidercommands.md` → `aidercommands.md.bak`. Claude emits a structured report (`SUMMARY`, `FINDINGS`, `PATCHES_APPLIED`, `MANUAL_REMAINING`, `VERDICT`) written to `aidercommands.review.md`. The wrapper parses the final `VERDICT:` line; `PASS` lets Stage 4 proceed, `BLOCK` halts the pipeline with exit 1 and lists steps needing manual rewrite. `PATCHES_APPLIED` is echoed to the console inline so the user sees the changes without opening the review file.

Rollback is a plain file move: `mv aidercommands.md.bak aidercommands.md` restores the pre-review state.

### Output files

All relative to `--target-dir` (default `LocalLLMCodePrompts/`):

| File                                    | Produced by                       | Purpose                                                                |
| --------------------------------------- | --------------------------------- | ---------------------------------------------------------------------- |
| `Implementation Planning Prompt.md`     | Stage 1                           | Refined plan prompt, input to Stage 2a                                 |
| `PromptUpdates.md`                      | Stage 1                           | Critique of the original `InitialPrompt.md`                            |
| `Architecture Plan.md`                  | Stage 2b                          | Consolidated architecture (appended section-by-section)                |
| `Architecture Plan.md.bak`              | Stage 2c (when `--review`)        | Pre-patch snapshot of the arch plan, for rollback                      |
| `Architecture Plan.review.md`           | Stage 2c (when `--review`)        | Arch-plan audit report: findings + patches applied + verdict           |
| `aidercommands.md`                      | Stage 3b                          | Aider commands + prompts (appended step-by-step)                       |
| `aidercommands.md.bak`                  | Stage 3c (when `--review`)        | Pre-patch snapshot, for rollback                                       |
| `aidercommands.review.md`               | Stage 3c (when `--review`)        | Aidercommands audit report: findings + patches applied + verdict       |
| `.section_plan.md`                      | Stage 2a                          | Ephemeral; deleted after Stage 2b finishes                             |
| `.step_plan.md`                         | Stage 3a                          | Ephemeral; deleted after Stage 3b finishes                             |
| `<file>.thinking.md` sidecars           | when `LLM_SAVE_THINKING=true`     | Reasoning tokens from thinking models; audit/debug only                |
| `.progress`                             | progress tracker                  | Resume state                                                           |

Plus `Implemented Plans/Codebase Summary.md` at the **repo root** (not under target-dir), produced by Stage 0.

### Resume behavior

- If `.progress` has `LastCompleted=N`, the orchestrator auto-advances `--from-stage` to `N+1`.
- If a stage is partially complete (e.g. Stage 2b wrote 5 of 12 sections before crashing), the `SubStep=K` line in `.progress` tells the resumed run to skip sections 1…K and pick up at K+1.
- The partial `Architecture Plan.md` / `aidercommands.md` files on disk are **preserved** across restarts — each section/step is appended directly, so the on-disk file always reflects completed work.
- Stage 3c (`--review`) is stateless with respect to `.progress` — it runs whenever `--review` is set and `aidercommands.md` exists, regardless of where resume starts.

### Examples

```powershell
cd C:\Coding\MyProject

# Default mode: Stage 1 on Claude, others on local
python C:\Coding\LocalLLM_Pipeline\Common\ArchPipeline.py coding

# Everything local (requires InitialPrompt.md)
python C:\Coding\LocalLLM_Pipeline\Common\ArchPipeline.py coding --local

# Everything on Claude
python C:\Coding\LocalLLM_Pipeline\Common\ArchPipeline.py coding --all-claude --model opus --ultrathink

# Regenerate only Stage 3 and then review+fix the output
python C:\Coding\LocalLLM_Pipeline\Common\ArchPipeline.py coding --from-stage 3 --review

# Review an existing aidercommands.md without re-running any generation
python C:\Coding\LocalLLM_Pipeline\Common\ArchPipeline.py coding --from-stage 4 --skip-stage 4 5 --review

# Start fresh
python C:\Coding\LocalLLM_Pipeline\Common\ArchPipeline.py coding --restart
```

---

## Configuration — `Common/.env`

Single source of truth for endpoints, model tags, context sizes, and subsection lists. Parsed by `_pipeline/config.py` as plain `KEY=VALUE` lines.

### Endpoint

| Key              | Default           | Used by                                                                |
| ---------------- | ----------------- | ---------------------------------------------------------------------- |
| `LLM_ENDPOINT`   | unset             | If set, overrides `LLM_HOST`/`LLM_PORT`. Form: `http://HOST:PORT`.     |
| `LLM_HOST`       | `192.168.1.126`   | Combined with `LLM_PORT` into a URL.                                   |
| `LLM_PORT`       | `11434`           | Combined with `LLM_HOST`.                                              |

### Models

| Key                       | Default             | Used by                                                                   |
| ------------------------- | ------------------- | ------------------------------------------------------------------------- |
| `LLM_DEFAULT_MODEL`       | `qwen3-coder:30b`   | Universal fallback — every role key below chains to it when blank/unset.  |
| `LLM_PLANNING_MODEL`      | `gemma4:26b`        | coding stages 2a, 2b, 3a, 3b (thinking model)                             |
| `LLM_AIDER_MODEL`         | blank (→ DEFAULT)   | `run_aider.py` (Stage 4) and `fix_imports.py` (Stage 5)                   |
| `LLM_MODEL`               | blank (→ DEFAULT)   | debug mode Step 5; analysis workers                                       |

Coding stages 3a and 3b use a hardcoded per-stage override of `qwen3-coder:30b` regardless of `LLM_PLANNING_MODEL`. See `STAGE_DEFAULTS` in [router.py](../_pipeline/modes/coding/router.py).

### Context windows and generation budgets

| Key                         | Default   | Purpose                                                                                          |
| --------------------------- | --------- | ------------------------------------------------------------------------------------------------ |
| `LLM_NUM_CTX`               | `32768`   | Default context for analysis/debug calls.                                                        |
| `LLM_PLANNING_NUM_CTX`      | `65536`   | Coding stages 2–3 context.                                                                       |
| `LLM_PLANNING_MAX_TOKENS`   | `49152`   | `num_predict` for coding stages. Must be large enough for thinking + content combined.           |
| `LLM_FIX_MAX_TOKENS`        | `16384`   | Debug Step 5 `num_predict`.                                                                      |

### Timeouts

| Key                       | Default   | Purpose                                                                                                                                                    |
| ------------------------- | --------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `LLM_TIMEOUT`             | `300`     | HTTP timeout for analysis/debug calls.                                                                                                                     |
| `LLM_PLANNING_TIMEOUT`    | `1200`    | Floor for coding-stage timeout. Coding stages compute an adaptive timeout per call scaled by prompt size + `max_tokens`; this value acts as a lower bound. |

### Thinking-model support

| Key                   | Default   | Purpose                                                                                                                  |
| --------------------- | --------- | ------------------------------------------------------------------------------------------------------------------------ |
| `LLM_THINK`           | `true`    | Sends `think:true` on `/api/chat` for coding stages 2a/2b (only when the model equals `LLM_PLANNING_MODEL`).             |
| `LLM_SAVE_THINKING`   | `true`    | Writes `<output>.thinking.md` sidecars with reasoning tokens.                                                            |

### Debug ↔ Analysis integration

| Key                    | Default                           | Purpose                                                                                                        |
| ---------------------- | --------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `ARCHITECTURE_DIR`     | `architecture`                    | Directory debug mode reads for `INTERFACES.md`, `DATA_FLOW.md`, `interfaces/*.iface.md`.                       |
| `SERENA_CONTEXT_DIR`   | `architecture/.serena_context`    | Per-file context bundles consumed by interface synthesis.                                                      |

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

| Key               | Meaning                                                                                                                  |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `LastCompleted`   | Highest completed top-level stage number. `-1` if no stages completed.                                                   |
| `SubStep`         | Within-stage progress (sections in Stage 2b, steps in Stage 3b, files in debug Step 5). Absent when not applicable.      |
| `Mode`            | `default` / `local` / `allclaude` for coding; `debug` for debug. Guards against resuming with mismatched flags.          |
| `TargetDir`       | For debug mode — which source dir this progress file belongs to.                                                         |
| `Timestamp`       | Informational, last save time.                                                                                           |

### Resume precedence

1. **`--restart`** — always wipes the progress file; starts fresh.
2. **Mode mismatch** — refuses to resume; prints the correct flag to use.
3. **`--from-stage N` explicitly passed** — overrides auto-advance.
4. **`LastCompleted` found** — auto-advances `--from-stage` to `LastCompleted + 1`.

Progress is wiped on successful completion so the next invocation doesn't auto-skip everything. Use `--restart` to re-run a fully-completed pipeline.

---

## Prompt templates

Every LLM prompt lives in `_pipeline/prompts/` as a plain `.md` file. They are read fresh on each invocation — no restart required after editing.

| File                                            | Used by                                                                                       |
| ----------------------------------------------- | --------------------------------------------------------------------------------------------- |
| `stage0_summarize.md`                           | Coding Stage 0                                                                                |
| `stage1_improve_prompt.md`                      | Coding Stage 1                                                                                |
| `stage2a_section_plan.md`                       | Coding Stage 2a                                                                               |
| `stage2b_section.md`                            | Coding Stage 2b (with `SECTITLE` / `SECDESC` placeholders)                                    |
| `stage3a_step_plan_head.md` + `..._tail.md`     | Coding Stage 3a (architecture plan injected between head and tail)                            |
| `stage3b_step_head.md` + `..._tail.md`          | Coding Stage 3b, production steps (with `STEPNUM` / `STEPTITLE` / `AIDERFILES`)               |
| `stage3b_step_test_head.md`                     | Coding Stage 3b, test steps (shares the same tail; same placeholders)                         |
| `stage2c_review.md`                             | Coding Stage 2c review of Architecture Plan.md (with `TARGET_DIR` / `REPO_ROOT` placeholders) |
| `stage3c_review.md`                             | Coding Stage 3c review of aidercommands.md (with `TARGET_DIR` / `REPO_ROOT` placeholders)     |
| `debug_fix.md`                                  | Debug Step 5 (with `SRCPATH` placeholder)                                                     |

### Placeholder substitution

Placeholders are uppercase tokens replaced via `str.replace()` at runtime:

| Token           | Replaced with                                                       |
| --------------- | ------------------------------------------------------------------- |
| `SECTITLE`      | Section title from `.section_plan.md`                               |
| `SECDESC`       | Section description                                                 |
| `STEPNUM`       | Step number                                                         |
| `STEPTITLE`     | Step title                                                          |
| `AIDERFILES`    | Space-separated file list                                           |
| `SRCPATH`       | Source file path (debug mode)                                       |
| `TARGET_DIR`    | Absolute path to target directory (Stage 3c review)                 |
| `REPO_ROOT`     | Absolute path to repo root (Stage 3c review)                        |

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
      all_modes.py                      # `all` sub-command (runs coding → analysis → debug)
      analysis.py                       # analysis mode orchestration
      debug\                            # debug mode (package)
      coding\                           # coding mode (package: cli, router, stages_llm, stages_exec, fileops)
```

### Module responsibilities

- **`config`** — only reads; never writes. Returns dicts/lists.
- **`ui`** — side-effectful (writes to stderr, sets up logging handlers).
- **`subprocess_runner`** — raises `StepFailed` on exit≠0, `UserCancelled` on exit=130.
- **`ollama`** — HTTP client with retry, thinking-budget diagnostics, content-sanity check. Raises `LLMError`. Catches `TimeoutError` separately so socket timeouts trigger retry and surface as `LLMError` with a clear "raise LLM_PLANNING_TIMEOUT" hint.
- **`claude`** — subprocess wrapper. Raises `ClaudeError`.
- **`progress`** — `ProgressFile` class with `.read()` / `.save()` / `.clear()`.
- **`modes/coding/router.py`** — per-stage defaults, engine routing, adaptive timeout computation, mode resolution.
- **`modes/coding/stages_llm.py`** — Stage 0–3c planning / review logic.
- **`modes/coding/stages_exec.py`** — Stage 4 (`run_aider.py`) and Stage 5 (`fix_imports.py`) subprocess dispatch.
- **`modes/coding/fileops.py`** — helpers: prompt loading, architecture slicing, pipeline-output filtering, `PIPELINE_OUTPUT_FILES`.
- **`modes/coding/cli.py`** — argparse registration + top-level `run()` that wires stages together.
- **`modes/*`** — each exposes `register(subparsers)` and `run(args)`. Entry point imports them and wires them to the argparse subparsers.

---

## Exit codes

| Code    | Meaning                                                                                           |
| ------- | ------------------------------------------------------------------------------------------------- |
| `0`     | Pipeline completed successfully.                                                                  |
| `1`     | A step failed (`StepFailed`, `LLMError`, `ClaudeError`, missing required file, review BLOCK).     |
| `2`     | Unexpected exception.                                                                             |
| `130`   | User cancelled via Ctrl+Q (only while a child process is running).                                |

---

## Troubleshooting

### "Empty response from LLM" / "Model exhausted budget inside <thinking>"

The local model consumed `num_predict` on reasoning and produced no content. Raise `LLM_PLANNING_MAX_TOKENS` and/or `LLM_PLANNING_NUM_CTX` in `Common/.env`. For thinking models, 16k tokens of reasoning per call is not uncommon — budget 32k+ of `num_predict`.

### "LLM request timed out after Ns — raise LLM_PLANNING_TIMEOUT"

The adaptive per-call timeout (floor = `LLM_PLANNING_TIMEOUT`) was exhausted. Raise the floor in `.env` if the model legitimately needs longer, or lower `num_ctx` / `max_tokens` so generation fits in the existing budget.

### "LLM returned suspiciously short/garbled content"

The model emitted one stop token (e.g. a single CJK character) as its entire content because thinking ate the full budget. Same fix as above. If it persists, switch the offending stage to a non-thinking coder model.

### "ERROR: Stage 3a produced no parseable STEP lines"

The model didn't emit the `STEP N | Title | files` format. Check `.step_plan.md` — if the model prose-dumped the architecture plan instead of the step list, that stage is a bad fit for the current model. The default-override to `qwen3-coder:30b` for Stage 3a usually resolves this.

### Stage 3c: "VERDICT: BLOCK"

Open `aidercommands.review.md` in target_dir. The `FINDINGS` and `MANUAL_REMAINING` sections list what's wrong; `PATCHES_APPLIED` shows what Claude already fixed. Edit `aidercommands.md` (or revert from `aidercommands.md.bak`) and re-run with or without `--review`.

### Stage 4: "aider not on PATH" / "FileNotFoundError WinError 2"

Install aider: `py -m pip install aider-chat`. `runner.py` falls back to `python -m aider` if the `aider` executable isn't on PATH, but the package must still be installed in the Python interpreter that runs `run_aider.py`.

### "Required file not found: architecture/INTERFACES.md"

Debug Step 5 requires the output of Step 2 (`interfaces_local.ps1`) and Step 1 (`dataflow_local.ps1`). Either they haven't run yet, or `ARCHITECTURE_DIR` in `.env` points to a different folder. If you have analysis output in a `N. <name>` folder (legacy rename), update `ARCHITECTURE_DIR` to match.

### "UnicodeDecodeError: 'charmap' codec"

Python on Windows defaults to `cp1252` for text reads. `config.py` uses UTF-8 explicitly — if you hit this elsewhere, check that new code reading `.env` or `.md` files passes `encoding='utf-8'`.

### Coding mode: "saved progress used mode 'X' but current run uses 'Y'"

`--restart` to start fresh, or pass the flag that matches the original run (e.g. the old run used `--local`, so the current run must too).

### Analysis mode: subsection auto-skipped unexpectedly

`is_subsection_completed()` matches folders like `1. src` (legacy rename). Remove the numbered folder (or rename it) to force re-processing.

### `claude` command not found

Claude stages call `claude --model <m> --output-format text` via subprocess. Install with `npm install -g @anthropic-ai/claude-code` (or the current installer). Verify `claude --help` runs before using coding mode's default engine routing or `--review`.

---

## Migration from legacy scripts

### From `Arch_Analysis_Pipeline.py`

| Legacy                                                           | New                                                   |
| ---------------------------------------------------------------- | ----------------------------------------------------- |
| `python LocalLLMAnalysis\Arch_Analysis_Pipeline.py --dry-run`    | `python Common\ArchPipeline.py analysis --dry-run`    |
| `--start-from 2`                                                 | `--start-from 2`                                      |
| `--skip-lsp`                                                     | `--skip-lsp`                                          |

Progress is not shared (legacy had none). Output paths match exactly. **Behavior difference:** legacy renamed `architecture/` to `N. <subsection>` on completion; the new pipeline does not. See [mode: analysis](#mode-analysis) above.

### From `Arch_Debug_Pipeline.ps1`

| Legacy                                                                | New                                                               |
| --------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `.\LocalLLMDebug\Arch_Debug_Pipeline.ps1 -TargetDir src/nmon`         | `python Common\ArchPipeline.py debug --target-dir src/nmon`       |
| `-TestDir tests`                                                      | `--test-dir tests`                                                |
| `-Restart`                                                            | `--restart`                                                       |
| `-DryRun`                                                             | `--dry-run`                                                       |
| `-Claude Claude2`                                                     | *(no-op; Step 5 uses local LLM in both versions)*                 |
| `-Model` / `-Ultrathink` / `-NoUltrathink`                            | *(no-op; Claude was removed from Step 5 earlier)*                 |

`.debug_progress` format is identical. A run started under the legacy script can be resumed under the new one.

### From `Arch_Coding_Pipeline.ps1`

| Legacy                                         | New                                            |
| ---------------------------------------------- | ---------------------------------------------- |
| `.\LocalLLMCoding\Arch_Coding_Pipeline.ps1`    | `python Common\ArchPipeline.py coding`         |
| `-TargetDir .\LocalLLMCodePrompts_V2`          | `--target-dir .\LocalLLMCodePrompts_V2`        |
| `-Claude Claude2`                              | `--claude Claude2`                             |
| `-Model opus`                                  | `--model opus`                                 |
| `-LocalModel qwen3:32b`                        | `--local-model qwen3:32b`                      |
| `-LocalEndpoint http://localhost:11434`        | `--local-endpoint http://localhost:11434`      |
| `-Local`                                       | `--local`                                      |
| `-AllClaude`                                   | `--all-claude`                                 |
| `-Ultrathink` / `-NoUltrathink`                | `--ultrathink` / `--no-ultrathink`             |
| `-FromStage 3`                                 | `--from-stage 3`                               |
| `-SkipStage 1,2`                               | `--skip-stage 1 2`                             |
| `-Restart`                                     | `--restart`                                    |
| `-DryRun`                                      | `--dry-run`                                    |
| `-Force`                                       | `--force`                                      |
| *(no equivalent)*                              | `--review`                                     |

`.progress` format is identical; a mid-run resume across legacy/new works. The `--review` flag is new in the Python orchestrator — no legacy analogue.

---

## See also

- `../_pipeline/ollama.py` — Ollama client implementation.
- `../_pipeline/claude.py` — Claude CLI wrapper and account map.
- `../_pipeline/modes/coding/` — coding-mode package (router, stages_llm, stages_exec, fileops).
- `../.env` — runtime configuration.
- `../../LocalLLMCoding/run_aider.py` — downstream consumer of `aidercommands.md` (Stage 4).
- `../../LocalLLMCoding/fix_imports.py` — post-generation import-error repair (Stage 5).
