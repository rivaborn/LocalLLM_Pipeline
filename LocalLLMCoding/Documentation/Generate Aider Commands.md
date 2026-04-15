# Arch_Coding_Pipeline.ps1

## Overview

`Arch_Coding_Pipeline.ps1` is a four-stage PowerShell pipeline that turns a rough
project idea into ready-to-execute aider commands. It bridges human intent and local-LLM
code generation: Claude (or optionally the local LLM for later stages) handles the
architectural thinking, and the local LLM via aider handles the implementation.

The script lives in `LocalLLM_Pipeline/LocalLLMCoding/` and operates on prompt files in a
target directory (default: `LocalLLM_Pipeline/LocalLLMCodePrompts/`). Shared LLM helpers and
configuration live in `LocalLLM_Pipeline/Common/` (`llm_common.ps1`, `.env`). It is designed
to be run repeatedly as a project evolves -- each iteration builds on previously
implemented plans.

Stages 2 and 3 use a two-pass approach: a planning pass decomposes the work into
sections/steps, then each section/step is generated individually. This avoids output
truncation on large documents and makes the pipeline resumable at a granular level.

## Two engines: Claude and the local LLM

The pipeline reserves Claude for the single task where its judgement matters most —
**prompt refinement (Stage 1)** — and runs every other stage on the local LLM by
default.

| Mode | Stage 0 (Codebase Summary) | Stage 1 (Improve Prompt) | Stages 2a / 2b / 3a / 3b |
|---|---|---|---|
| **default** | local | **Claude** | local |
| `-Local` | local | local | local |
| `-AllClaude` | Claude | Claude | Claude |

**Why this split:** Stage 1 refines a rough human idea into a rigorous, unambiguous
planning prompt. The quality of this one call determines how well every downstream
stage performs, and an under-specified prompt is the single biggest cause of weak
architecture plans. Claude's judgement here is worth reserving a call for. Everything
else — codebase synthesis, section decomposition, step generation — is structured
transcription work that a capable local model handles well at a fraction of the cost.

The local model, endpoint, and per-request context window all live in
`LocalLLM_Pipeline/Common/.env`:

```ini
LLM_ENDPOINT=http://192.168.1.126:11434   # or LLM_HOST + LLM_PORT
LLM_PLANNING_MODEL=gemma4:26b             # used by every local stage (0, 2a/2b, 3a/3b)
LLM_PLANNING_NUM_CTX=24576
```

See [Engine routing modes](#engine-routing-modes) below for full details.

## Pipeline Stages

### Stage 0 -- Summarize Existing Codebase (automatic)

Runs automatically when `Implemented Plans/` contains one or more previously completed
architecture plans. Skipped entirely on the first run when no prior plans exist.

- **Input**: All `Architecture Plan *.md` files from `Implemented Plans/`
- **Output**: `Implemented Plans/Codebase Summary.md`
- **Claude calls**: 1
- **What Claude does**: Reads every implemented plan in chronological order and produces
  a single consolidated summary of the codebase as it exists today. Where later plans
  modified earlier designs, the summary reflects the final state only. The summary
  covers project structure, data models, module inventory, dependencies, configuration,
  and established patterns.
- **Why**: Raw plans accumulate over iterations and can grow to 100KB+ with overlapping
  or contradictory details. A compact summary gives stages 2 and 3 clean context
  without bloating the prompt.

### Stage 1 -- Improve Initial Prompt

- **Input**: `InitialPrompt.md` (in the target directory)
- **Output**: `Implementation Planning Prompt.md` + `PromptUpdates.md`
- **Claude calls**: 1
- **What Claude does**: Reviews the rough prompt for gaps, contradictions, and ambiguity.
  Produces a refined planning prompt that specifies tech stack, data model, UI
  requirements, testing strategy, and architecture deliverables. Separately outputs a
  critique documenting what was changed and why.
- **Separator protocol**: Claude's response is split on the literal line
  `---PROMPT_UPDATES---`. Everything before it becomes the improved prompt; everything
  after becomes the critique saved in `PromptUpdates.md`.

### Stage 2 -- Generate Architecture Plan (two-pass)

- **Input**: `Implementation Planning Prompt.md` + `Codebase Summary.md` (if it exists)
- **Output**: `Implemented Plans/Plans/Section N.md` (one per section) + a consolidated
  `Architecture Plan.md` in the target directory for Stage 3 consumption
- **LLM calls**: 1 (planning) + N (one per section, typically 10-15)
- **Engine in `-Local` mode**: local LLM (Opus by default without `-Local`)

Stage 2 uses a two-pass approach to avoid output truncation on large architecture plans:

**Stage 2a -- Section planning** (one LLM call): Decomposes the architecture plan into
a numbered section list. Each entry specifies a section title and what it covers. The
list is saved to `.section_plan.md` in the target directory.

**Stage 2b -- Per-section generation** (one LLM call per section): Loops through each
section in the plan. Each call receives the full planning prompt as context and generates
one detailed section. **Each section is written to its own file** at
`Implemented Plans/Plans/Section N.md` rather than appended to a single growing
document -- the LLM never sees or edits a consolidated plan, so a later section cannot
corrupt an earlier one and individual sections can be inspected or regenerated
independently.

After every section has been generated, Stage 2b concatenates all `Section *.md` files
in numeric order into `Architecture Plan.md` at the target directory (with a
`# Architecture Plan` H1). Stage 3 reads that consolidated file -- the per-section
files remain in `Plans/` for reference, editing, or partial regeneration.

Progress is saved after each section so interruption recovers cleanly (see
[Resumability](#resumability)).

### Stage 3 -- Generate Aider Commands (two-pass)

- **Input**: `Architecture Plan.md` + `Codebase Summary.md` (if it exists)
- **Output**: `aidercommands.md`
- **LLM calls**: 1 (planning) + N (one per step, typically 15-25)
- **Engine in `-Local` mode**: local LLM (Opus/Sonnet by default without `-Local`)

Stage 3 uses the same two-pass approach:

**Stage 3a -- Step planning** (one LLM call): Decomposes the architecture plan into
ordered implementation steps. Each entry specifies a step number, title, and target
files. The list is saved to `.step_plan.md` in the target directory.

**Stage 3b -- Per-step generation** (one LLM call per step): Loops through each step.
Each call receives the architecture plan as context and generates one self-contained
aider command with complete type definitions, function signatures, imports, and
pseudocode. Each step is appended to `aidercommands.md` as it completes.

**Smart architecture slicing in `-Local` mode** -- when `-Local` is set, Stage 3b does
not inject the full `Architecture Plan.md` into every step's prompt. Instead it parses
the plan by `##` headings and injects only the sections relevant to the step's files
(matched by basename) plus always-include sections (Project Structure, Data Model,
Configuration, Dependencies, Build/Run, Testing). This keeps each prompt inside the
local model's context window. Claude mode still receives the full plan.

### Output Format of aidercommands.md

Each step in the generated file follows this structure:

```
## Step N -- Title

\```bash
aider --yes src/project/module.py
\```
\```
<self-contained prompt with full context for the local LLM>
\```
```

The prompts are intentionally verbose because the local LLM has no access to the
architecture plan or codebase summary. Every type, signature, and behavioral detail
needed for implementation is repeated inline.

## Resumability

The script tracks progress in a `.progress` file in the target directory. If the script
is interrupted (rate limit, network error, Ctrl+C), re-running it automatically resumes
from where it stopped.

### How it works

- After each stage or sub-step completes, the `.progress` file is updated with the last
  completed stage, sub-step number, and **engine** (`claude` or `local`)
- On startup, the script reads `.progress` and adjusts `-FromStage` to skip completed
  work
- Stages 2 and 3 also track per-section/per-step progress via `.section_plan.md` and
  `.step_plan.md` files that persist between runs

### Engine-mismatch guard

The engine used for a run is recorded in `.progress`. If you start a run with `-Local`,
interrupt partway, then re-run without `-Local` (or vice versa), the script refuses to
resume and prints:

```
ERROR: saved progress used engine 'local' but current run is 'claude'.
  Re-run with -Local to resume, or use -Restart to start over.
```

This prevents a Frankenstein output where sections 1-13 are local-flavoured and 14+
are Claude-flavoured. `-Restart` always overrides the guard.

### Resume scenarios

| Scenario                          | What happens on re-run                           |
|-----------------------------------|--------------------------------------------------|
| Interrupted during Stage 1        | Restarts Stage 1 (single call, no sub-steps)     |
| Interrupted at Stage 2, section 5 | Skips sections 1-4, resumes from section 5       |
| Rate limited at Stage 3, step 12  | Skips stages 0-2 and steps 1-11, resumes step 12 |
| All stages completed              | Prints message and exits (use `-Restart`)        |

### Controlling resume behavior

```powershell
# Auto-resume (default) -- picks up where it left off
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1

# Ignore saved progress, start fresh
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1 -Restart

# Manual override -- start from a specific stage regardless of progress
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1 -FromStage 2 -Restart
```

The `.progress` file is automatically deleted when all stages complete successfully.

## CLI Parameters

| Parameter         | Type       | Default              | Description                                                         |
|-------------------|------------|----------------------|---------------------------------------------------------------------|
| `-TargetDir`      | `string`   | `LocalLLMCodePrompts`| Folder containing `InitialPrompt.md` and where output files are written. Relative paths resolve against the current working directory. |
| `-Claude`         | `string`   | `Claude1`            | Claude account to use. Maps to `CLAUDE_CONFIG_DIR`: `Claude1` = `.clauderivalon`, `Claude2` = `.claudefksogbetun`. |
| `-Model`          | `string`   | (per-stage defaults) | Override the Claude model for **all Claude stages**. Accepts aliases (`sonnet`, `opus`, `haiku`) or full model IDs. When omitted, each stage uses its per-stage default (Sonnet: 0, 1, 3b; Opus: 2a, 2b, 3a). Does not affect local-LLM stages. |
| `-Ultrathink`     | `switch`   | `$false`             | Force extended thinking on for all Claude stages. Has no effect on local-LLM stages (local models have no extended-thinking mode). |
| `-NoUltrathink`   | `switch`   | `$false`             | Force extended thinking off for all Claude stages. |
| `-Local`          | `switch`   | `$false`             | Route **every** stage to the local LLM (no Claude at all, including Stage 1). Mutually exclusive with `-AllClaude`. See [Engine routing modes](#engine-routing-modes). |
| `-AllClaude`      | `switch`   | `$false`             | Route **every** stage to Claude (reverts to pre-default behaviour, useful when the local server is unreachable). Mutually exclusive with `-Local`. |
| `-LocalEndpoint`  | `string`   | (from `.env`)        | Override the Ollama endpoint URL for local stages. Precedence: this flag → `$env:LLM_ENDPOINT` → `Common/.env` value → default `http://192.168.1.126:11434`. |
| `-LocalModel`     | `string`   | (from `.env`)        | Override the local planning model. Defaults to `LLM_PLANNING_MODEL` in `Common/.env`. |
| `-SkipStage`      | `int[]`    | (none)               | Skip one or more stages. Accepts a list: `-SkipStage 1,2` skips stages 1 and 2. Stage 0 cannot be skipped (it runs automatically when prior plans exist). |
| `-FromStage`      | `int`      | `1`                  | Start from a specific stage, skipping all earlier stages. Valid values: 1, 2, 3. Stage 0 always runs if applicable. |
| `-Restart`        | `switch`   | `$false`             | Ignore saved progress and start from stage 1 (or the stage specified by `-FromStage`). Without this flag, the script auto-resumes from where it last stopped. |
| `-Force`          | `switch`   | `$false`             | Overwrite existing output files without prompting. By default, the script shows file name, size, and last-modified date before overwriting and asks for confirmation. |
| `-DryRun`         | `switch`   | `$false`             | Parse and preview all stages without calling any LLM. Shows prompt sizes and engine routing. |

## Overwrite Protection

Before each stage writes output, the script checks if the target files already exist.
If they do, it displays:

```
  The following output files already exist:
    Architecture Plan.md  (35.3 KB, modified 2026-04-13 10:30:00)
  Overwrite? [y/N]
```

Answering `N` (or pressing Enter) skips that stage. Each stage is checked independently,
so you can decline to overwrite the architecture plan but still regenerate
aidercommands.md. Use `-Force` to bypass all prompts.

## Account Selection

The `-Claude` parameter controls which Claude account is used. The script sets
`CLAUDE_CONFIG_DIR` directly (mirroring the wrapper functions in the PowerShell
profile) and calls `claude.exe`, which avoids stdin-forwarding issues that occur when
piping through PowerShell function wrappers.

| Value     | Config Directory                    |
|-----------|-------------------------------------|
| `Claude1` | `$env:USERPROFILE\.clauderivalon`   |
| `Claude2` | `$env:USERPROFILE\.claudefksogbetun`|

## Model Selection and Extended Thinking

The `-Model` parameter currently applies to all stages. All stage prompts include
`ultrathink.` to trigger Claude's extended thinking mode. However, not all stages
benefit equally from Opus or extended thinking.

### Analysis by stage

**Stage 0 -- Summarize Existing Codebase**

The task is synthesis: read multiple documents and produce a consolidated view. This is
mostly mechanical -- resolve contradictions, keep the latest version of each design
decision, and format the result. Extended thinking adds little value here because the
challenge is comprehensiveness, not deep reasoning.

Recommended: **Sonnet**, no ultrathink.

**Stage 1 -- Improve Initial Prompt**

A short review/editing task. The initial prompt is typically small (under 1KB) and the
improvements are straightforward -- identify missing details, resolve contradictions,
add specificity. This does not require the depth of reasoning that Opus provides.

Recommended: **Sonnet**, no ultrathink.

**Stage 2a -- Section Planning**

Structural decomposition of requirements into architecture plan sections. Getting the
section breakdown right matters because it determines the completeness of the
architecture plan. A poor decomposition results in missing modules or overlooked
concerns. This is a single short call, so cost is not a factor.

Recommended: **Opus** with ultrathink.

**Stage 2b -- Per-Section Architecture**

This is where the real architectural reasoning happens. Each section requires Claude to
make design decisions: choosing data structures, defining interfaces, planning error
handling, resolving tradeoffs between simplicity and flexibility. The quality of function
signatures, pseudocode, and module boundaries directly determines how well the local LLM
can implement each file. Bad decisions here cascade through the entire codebase.

This is the most thinking-intensive stage of the pipeline.

Recommended: **Opus** with ultrathink.

**Stage 3a -- Step Planning**

Dependency analysis: decompose the architecture into ordered implementation steps. The
architecture plan already defines the modules, so this is mostly about determining build
order -- what depends on what, when to introduce tests. Correct ordering matters (you
can't import a module that doesn't exist yet), but the architecture plan provides
enough structure that this is more analytical than creative.

Recommended: **Opus**, ultrathink optional.

**Stage 3b -- Per-Step Aider Commands**

Extraction and formatting: take the relevant portion of the architecture plan and
reformat it as a self-contained prompt for the local LLM. The architectural decisions
are already made in Stage 2 -- this stage is about being thorough in transcribing
every type, signature, import, and behavioral detail into the prompt. Sonnet excels at
this kind of structured, detail-oriented work.

This is also the highest-volume stage (one call per implementation step, typically
15-25 calls), making it the most expensive stage in the pipeline. Using Sonnet here
significantly reduces total cost.

Recommended: **Sonnet**, no ultrathink.

### Summary table

| Sub-stage           | Task type                 | Recommended model    | Ultrathink | Call volume |
|---------------------|---------------------------|----------------------|------------|-------------|
| Stage 0             | Synthesis/summarization   | Sonnet               | No         | 1           |
| Stage 1             | Review/editing            | Sonnet               | No         | 1           |
| Stage 2a (plan)     | Structural decomposition  | Opus                 | Yes        | 1           |
| Stage 2b (sections) | Architectural reasoning   | Opus                 | Yes        | 10-15       |
| Stage 3a (plan)     | Dependency analysis       | Opus                 | Optional   | 1           |
| Stage 3b (steps)    | Extraction/formatting     | Sonnet               | No         | 15-25       |

### Cost implications

A typical run with 12 architecture sections and 20 implementation steps makes
approximately 36 Claude API calls. Using Opus for all of them is expensive. Applying
the per-stage recommendations:

- **Opus calls**: 2a + 2b + 3a = 1 + 12 + 1 = 14 calls (architectural work)
- **Sonnet calls**: 0 + 1 + 3b = 1 + 1 + 20 = 22 calls (synthesis and formatting)

This focuses Opus on the stages where deep reasoning produces measurably better output,
and uses Sonnet for the high-volume stages where the task is structured extraction
rather than creative reasoning.

### Current implementation

Per-stage defaults are already wired into `$StageDefaults` inside the script:

| Sub-stage | Default model | Default ultrathink |
|-----------|---------------|--------------------|
| 0         | sonnet        | off                |
| 1         | sonnet        | off                |
| 2a        | opus          | on                 |
| 2b        | opus          | on                 |
| 3a        | opus          | on                 |
| 3b        | sonnet        | off                |

`-Model` overrides the model for every Claude stage in one go; `-Ultrathink` /
`-NoUltrathink` force the ultrathink prefix on/off across all Claude stages. There is no
per-stage CLI override today -- to change a single stage's default, edit `$StageDefaults`
directly in the script.

## Engine routing modes

Three routing modes control which engine runs which stage:

| Stage | default | `-Local` | `-AllClaude` | Model source (local) | Model source (Claude) |
|-------|:-:|:-:|:-:|---|---|
| 0 (Codebase Summary) | local | local | Claude | `LLM_PLANNING_MODEL` | Sonnet (`-Model` override) |
| **1 (Improve Prompt)** | **Claude** | local | Claude | `LLM_PLANNING_MODEL` | Sonnet (`-Model` override) |
| 2a (Section list)    | local | local | Claude | `LLM_PLANNING_MODEL` | Opus (`-Model` override) |
| 2b (Per-section)     | local | local | Claude | same | Opus |
| 3a (Step list)       | local | local | Claude | same | Opus |
| 3b (Per-step)        | local | local | Claude | same | Sonnet |

**Default rationale.** Stage 1 refines a rough human idea into a rigorous, unambiguous
planning prompt. The quality of this one call determines how well every downstream
stage performs, so Claude's judgement is worth reserving for it. Every other stage is
structured transcription/decomposition work that the local LLM handles well at a
fraction of the cost.

**When to override:**
- `-Local` — every stage uses the local LLM. Use when Claude is unavailable (rate limits, offline) or when you want to keep the entire pipeline local.
- `-AllClaude` — every stage uses Claude. Use when the local server is down, or when you've moved to a cloud-only workflow.

The two flags are mutually exclusive. Omitting both gives the default split.

### Local LLM implementation

The local LLM call goes through `Invoke-LocalLLM` in `Common/llm_common.ps1`. It posts
to the Ollama native `/api/chat` endpoint with `options.num_ctx = LLM_PLANNING_NUM_CTX`
so the model gets the full context window on every request (no `Modelfile`
pre-registration required).

Endpoint precedence (first match wins):

1. `-LocalEndpoint <url>` on the command line
2. `$env:LLM_ENDPOINT` in the shell
3. `LLM_ENDPOINT` in `Common/.env`
4. `LLM_HOST` + `LLM_PORT` in `Common/.env`
5. Hardcoded default `http://192.168.1.126:11434`

Model precedence: `-LocalModel` flag → `LLM_PLANNING_MODEL` in `Common/.env`.

### Startup banner

Shows the active mode plus resolved endpoint/model when the local LLM is used:

```
Mode: default -- Stage 1 = Claude; Stages 0, 2a, 2b, 3a, 3b = local
  Local endpoint: http://192.168.1.126:11434
  Local model:    gemma4:26b (num_ctx=24576)
```

Ultrathink is automatically suppressed on local stages — the prefix is a Claude
extended-thinking trigger that local models don't recognise.

### Stage 0 context-window caveat

In the default and `-Local` modes, Stage 0 (Codebase Summary) runs against the local
planning model. Stage 0 concatenates every `Architecture Plan *.md` and
`Bug Fix Changes *.md` under `Implemented Plans/`, so its input grows with each
iteration. A typical mid-project prompt is already ~20K tokens; with
`LLM_PLANNING_NUM_CTX=24576` that leaves little headroom for the output.

If Stage 0 starts truncating input silently on large histories, either:
- Bump `LLM_PLANNING_NUM_CTX` in `Common/.env` (watch VRAM on `ollama ps`).
- Switch to `-AllClaude` for the summary-heavy run (Claude Sonnet handles large input comfortably).
- Temporarily remove or archive stale plans from `Implemented Plans/`.

### Resume and mode-mismatch guard

Progress is recorded per sub-step in `.progress` alongside the active `Mode` key
(`default` / `local` / `allclaude`). Re-running with a different mode will abort:

```
ERROR: saved progress used mode 'default' but current run uses 'allclaude'.
  Re-run with no mode flags (the default) to resume, or use -Restart to start over.
```

This prevents mixing engines across sections of the same output document.

## File Layout

### Input (required in target directory)

| File                | Purpose                                              |
|---------------------|------------------------------------------------------|
| `InitialPrompt.md`  | Rough project idea or feature request                |

### Output (generated in target directory)

| File                                | Stage | Purpose                                                      |
|-------------------------------------|-------|--------------------------------------------------------------|
| `Implementation Planning Prompt.md` | 1     | Refined, unambiguous planning prompt                         |
| `PromptUpdates.md`                  | 1     | Critique of the initial prompt with changelog                |
| `Architecture Plan.md`              | 2     | Consolidated architecture plan (concatenated from `Plans/Section *.md` at end of 2b) |
| `aidercommands.md`                  | 3     | Self-contained aider steps for local LLM execution           |

### Output (generated in project root / `LocalLLM_Pipeline/`)

| File                                       | Stage | Purpose                                              |
|--------------------------------------------|-------|------------------------------------------------------|
| `Implemented Plans/Codebase Summary.md`    | 0     | Consolidated summary of all previously implemented plans |
| `Implemented Plans/Plans/Section N.md`     | 2b    | Per-section architecture plan files -- one file per section, never edited as a whole. Consolidated into `Architecture Plan.md` at end of Stage 2. Cleared on a fresh (non-resume) Stage 2 run. |

### Shared config

| File                               | Purpose                                                      |
|------------------------------------|--------------------------------------------------------------|
| `LocalLLM_Pipeline/Common/llm_common.ps1` | Shared PowerShell helpers: `Invoke-LocalLLM`, `Get-LLMEndpoint`, `Read-EnvFile`. |
| `LocalLLM_Pipeline/Common/.env`         | Shared config: endpoint, local models, context windows, analysis settings. |

### Temporary files (in target directory, cleaned up on completion)

| File                 | Stage | Purpose                                                    |
|----------------------|-------|------------------------------------------------------------|
| `.progress`          | All   | Tracks last completed stage and sub-step for resume        |
| `.section_plan.md`   | 2     | Section list for the architecture plan (deleted on completion) |
| `.step_plan.md`      | 3     | Step list for aider commands (deleted on completion)       |

## Iterative Development Workflow

The script is designed for repeated use as a project evolves:

```
First iteration (new project):

  InitialPrompt.md
       |
       v
  Arch_Coding_Pipeline.ps1     (no Stage 0 -- no prior plans)
       |
       v
  run_aider.py                   (executes aider steps with local LLM)
       |
       v
  Implemented Plans/             (Architecture Plan 1.md archived here)


Second iteration (adding features):

  New InitialPrompt.md
       |
       v
  Arch_Coding_Pipeline.ps1     (Stage 0 summarizes Plan 1)
       |                         (Stages 2-3 receive codebase context)
       v
  run_aider.py                   (extends existing code, not rewriting)
       |
       v
  Implemented Plans/             (Architecture Plan 2.md archived here)
```

After `run_aider.py` completes all steps, it:
1. Marks the prompts folder as completed (`.completed` marker)
2. Copies `Architecture Plan.md`, `aidercommands.md`, `Implementation Planning Prompt.md`,
   and `PromptUpdates.md` to `Implemented Plans/` with sequential numbering
   (e.g., `Architecture Plan 2.md`, `aidercommands 2.md`)

On the next run, `Arch_Coding_Pipeline.ps1` reads those archived plans in Stage 0
and generates a fresh `Codebase Summary.md`, ensuring Claude understands the current
state of the codebase before planning new work.

## Usage Examples

```powershell
# Full pipeline with defaults (Claude1, per-stage model defaults)
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1

# -Local: Claude for stages 0/1, local LLM for 2a/2b/3a/3b
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1 -Local

# -Local with an explicit endpoint override (one-off redirect)
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1 -Local -LocalEndpoint http://localhost:11434

# -Local with a different planning model
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1 -Local -LocalModel qwen3:32b

# Re-run after interruption -- auto-resumes from where it stopped
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1

# Ignore saved progress and start fresh
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1 -Restart

# Use a different prompts folder
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1 -TargetDir .\LocalLLMCodePrompts_V2

# Use second Claude account and force Sonnet for every Claude stage
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1 -Claude Claude2 -Model sonnet

# Skip prompt improvement, regenerate architecture and commands only
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1 -FromStage 2 -Restart

# Only regenerate aider commands from existing Architecture Plan
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1 -FromStage 3 -Restart

# Skip stages 1 and 2, only run stage 3
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1 -SkipStage 1,2

# Preview without calling any LLM
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1 -DryRun
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1 -Local -DryRun    # shows engine routing

# Overwrite everything without confirmation prompts
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1 -Force

# After generation, execute the plan with the local LLM
python ..\LocalLLM_Pipeline\LocalLLMCoding\run_aider.py --local
```

## Prerequisites

- **Claude Code CLI** (`claude`) installed and available in PATH
- **PowerShell 5.1+** (Windows PowerShell or PowerShell Core)
- At least one Claude account configured (Claude1 or Claude2 via `CLAUDE_CONFIG_DIR`)
- An `InitialPrompt.md` file in the target directory describing the work to be done
- **For `-Local` mode only:** an Ollama server reachable at the configured endpoint
  with the planning model pulled (e.g. `ollama pull gemma4:26b`)
