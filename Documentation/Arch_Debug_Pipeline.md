# Arch_Debug_Pipeline.ps1

## Overview

`Arch_Debug_Pipeline.ps1` is a six-step automated debugging pipeline that analyzes a
codebase using local LLM-powered tools, then invokes Claude Code to fix all identified
bugs. It archives a summary of changes to `Implemented Plans/` with timestamps, feeding
into the iterative development workflow used by `Arch_Coding_Pipeline.ps1`.

The pipeline bridges two systems:
- **Steps 1-4**: Run on the local LLM server (Ollama) via the LocalLLMDebug analysis
  scripts. These are cheap, fast, and produce structured reports.
- **Step 5**: Uses Claude Code to read the reports and apply fixes to the actual source
  code. This is where the real reasoning and code editing happens.
- **Step 6**: Archives the changes for future context.

The analysis scripts and this orchestrator share configuration via
`LocalLLM_Pipeline/Common/.env`. The shared LLM helper (`Invoke-LocalLLM`) lives in
`LocalLLM_Pipeline/Common/llm_common.ps1`. Default analysis model is `qwen3-coder:30b`
with a per-request `num_ctx=32768`, passed via the native Ollama `/api/chat`
endpoint (no custom `Modelfile` required).

### Optional: consume LocalLLMAnalysis outputs

Three Debug steps optionally inject artifacts from the Analysis pipeline for
better cross-file context. All injections gracefully skip when the expected
file is missing, so the Debug pipeline works whether or not Analysis has been
run.

| Debug step                  | Analysis artifact consumed                               | `.env` key            |
|-----------------------------|----------------------------------------------------------|-----------------------|
| `bughunt_local.ps1`         | `<ARCH>/xref_index.md` (prepended to every file prompt)  | `ARCHITECTURE_DIR`    |
| `dataflow_local.ps1` (synth)| `<ARCH>/architecture.md` (prepended to synth prompt)     | `ARCHITECTURE_DIR`    |
| `interfaces_local.ps1`      | `<SERENA>/<rel>.serena_context.txt` per file             | `SERENA_CONTEXT_DIR`  |

Set `ARCHITECTURE_DIR` to the Analysis output folder (default `architecture`;
update to `N. <subsection>` after Analysis completes and renames the folder).
Set `SERENA_CONTEXT_DIR` to where `serena_extract.ps1` wrote its context files
(default `.serena_context`). Leave either empty to disable that integration.

**Caveats when enabling these integrations:**

- **Cache invalidation.** `interfaces_local.ps1` SHA1-caches per-file
  extractions on the source file hash. Cached extractions from before you
  enabled `SERENA_CONTEXT_DIR` will keep being reused until the source file
  changes. Run `interfaces_local.ps1 -Clean` once to force fresh extraction
  with LSP context injected.
- **Token budget.** At `LLM_NUM_CTX=32768` the xref/architecture injections
  typically add 2-10K tokens per call on top of existing prompts. Large
  codebases may approach the window ceiling — see DebugWorkflow.md for
  mitigation.
- **VRAM / KV cache.** Bumping `LLM_NUM_CTX` is not free. On `qwen3-coder:30b`,
  KV cache costs roughly +1 GB per 8K of context on a 24 GB card. If you need
  more headroom, check `ollama ps` after the change — `PROCESSOR` must read
  `100% GPU`, otherwise inference slows 3-4× from partial CPU offload.

## Pipeline Steps

### Step 1 -- Data Flow Analysis

- **Script**: `dataflow_local.ps1`
- **Output**: `architecture/DATA_FLOW.md`
- **What it does**: Two-pass pipeline. Pass 1 extracts each file's pipeline interface
  (types, inputs, outputs, threading, error surface). Pass 2 synthesizes all extractions
  into a single debugging-focused data flow document.
- **Caching**: Per-file extractions are cached by SHA1 hash. Only changed files are
  re-analyzed on subsequent runs.

### Step 2 -- Interface Contract Extraction

- **Script**: `interfaces_local.ps1`
- **Output**: `architecture/INTERFACES.md` + per-file contracts in `architecture/interfaces/`
- **What it does**: Two-pass pipeline. Pass 1 extracts the precise contract of every
  public class and function (preconditions, postconditions, raises, silent failure modes,
  thread safety, resource lifecycle). Pass 2 synthesizes into a reference document with
  cross-module obligations and a silent-failure inventory.
- **Caching**: SHA1-based cache in `architecture/.interfaces_state/cache/`.

### Step 3 -- Test Gap Analysis

- **Script**: `testgap_local.ps1`
- **Output**: `test_gaps/GAP_REPORT.md` + per-file gap reports in `test_gaps/src/`
- **What it does**: Three-pass pipeline. Pass 0 maps source files to test files. Pass 1
  sends each source + test pair to the LLM to identify coverage gaps. Pass 2 synthesizes
  into a prioritized gap report.
- **Caching**: SHA1-based cache in `test_gaps/.testgap_state/cache/`.

### Step 4 -- Bug Hunt

- **Script**: `bughunt_local.ps1`
- **Output**: `bug_reports/SUMMARY.md` + per-file reports in `bug_reports/src/`
- **What it does**: Single-pass analysis. Runs bug-focused LLM analysis over every source
  file, producing a per-file bug report.
- **Caching**: SHA1-based skip via `bug_reports/.bughunt_state/hashes.tsv`. Only files
  whose content has changed since the last run are re-analyzed.

### Step 5 -- Claude Code: Fix Bugs (per-file)

- **Tool**: Claude Code CLI
- **Output**: Fixes applied to source files in-place + `.debug_changes.md` (running log)
- **What it does**: Iterates through each per-file bug report. For each file with bugs,
  sends Claude a focused prompt containing:
  - The bug report for that specific file
  - The per-file interface contract (from `architecture/interfaces/`)
  - The per-file test gap report (from `test_gaps/src/`)
  - The system-wide data flow context
  - The actual source code of the file
- **Per-file resumability**: Progress is tracked per-file. If Claude fails at file 8 of
  15, re-running skips files 1-7 and retries file 8.
- **Smart model selection**: See [Model Selection](#model-selection) section below.

### Step 6 -- Archive Bug Fix Changes

- **Output**: `Implemented Plans/Bug Fix Changes N.md`
- **What it does**: Writes the accumulated change log from Step 5 to a numbered archive
  file with a `<!-- Timestamp: ... -->` header. Also stamps any existing Architecture
  Plan files that don't have timestamps yet.

## CLI Parameters

| Parameter       | Type     | Default   | Description |
|-----------------|----------|-----------|-------------|
| `-TargetDir`    | `string` | (required)| Source code directory to analyze (e.g. `src/nmon`) |
| `-TestDir`      | `string` | `tests`   | Test directory for testgap analysis |
| `-Claude`       | `string` | `Claude1` | Claude account. Maps to `CLAUDE_CONFIG_DIR`: `Claude1` = `.clauderivalon`, `Claude2` = `.claudefksogbetun` |
| `-Model`        | `string` | (auto)    | Override Claude model for ALL files. When omitted, auto-selects per file: Opus for bugs, Sonnet for clean |
| `-Ultrathink`   | `switch` | off       | Force extended thinking for ALL files |
| `-NoUltrathink` | `switch` | off       | Disable extended thinking for ALL files |
| `-EnvFile`      | `string` | (none)    | Override path to the `.env` file for the analysis scripts. Default is `LocalLLM_Pipeline/Common/.env`. |
| `-Restart`      | `switch` | off       | Ignore saved progress and start from step 1 |
| `-DryRun`       | `switch` | off       | Preview all steps without running anything |

## Resumability

The pipeline is resumable at two levels:

### Step-level resume

A `.debug_progress` file in the project root tracks the last completed step. If the
pipeline is interrupted, re-running it skips completed steps:

```
Resuming from step 3 (steps 1-2 completed previously)
  Use -Restart to start over
```

### Per-file resume (Step 5)

Within Step 5, progress is tracked per source file. If Claude fails on file 8 of 15
(rate limit, network error, etc.), the progress file records `SubStep=7`. On re-run:

```
  Resuming from file 8 (7 of 15 done)
    1/15 - src/nmon/models.py           [already done]
    2/15 - src/nmon/config.py           [already done]
    ...
    7/15 - src/nmon/collector.py        [already done]
    8/15 - src/nmon/gpu/nvml_source.py  [opus + ultrathink]
```

### Analysis script caching (Steps 1-4)

The analysis scripts have their own SHA1-based caching. Files whose content hasn't
changed since the last analysis are skipped automatically, regardless of the pipeline's
step-level progress. This means even if you `-Restart` the pipeline, steps 1-4 will
be fast if the source code hasn't changed.

## Model Selection

### Steps 1-4: Local LLM

Steps 1-4 run on the local LLM server via Ollama. The model is controlled by
`LLM_MODEL` in `LocalLLM_Pipeline/Common/.env` (default: `qwen3-coder:30b`). The
context window is controlled by `LLM_NUM_CTX` (default: `32768`); when set, the
analysis scripts route requests through Ollama's native `/api/chat` endpoint
with `options.num_ctx` so the model gets the full window per call. The `-Model`
parameter on this orchestrator has no effect on these steps.

### Step 5: Smart per-file selection

Not all bug reports are equal. Some files have genuine bugs (threading issues, boundary
conditions, contract violations) that require deep reasoning. Others have "No significant
bugs found" reports where Claude just needs to confirm no changes are needed.

The pipeline reads each bug report before calling Claude and classifies it:

**Files with real bugs** -- detected by the absence of "clean" indicators and reports
longer than 200 characters:
- Model: **Opus** (default)
- Ultrathink: **On** (default)
- Rationale: Bug fixes require understanding the module's role in the system, thread
  safety implications, and data flow invariants. Applying a fix that doesn't account for
  callers or concurrent access can introduce new bugs. Extended thinking lets Claude
  reason through implications before editing.

**Files with no significant bugs** -- detected by patterns like "no significant bugs",
"no issues found", "clean", or very short reports:
- Model: **Sonnet** (default)
- Ultrathink: **Off** (default)
- Rationale: The task is just confirming "no changes needed". This is a quick check
  that doesn't require deep reasoning, and Sonnet handles it well at lower cost.

### Console output

The pipeline shows which model is selected for each file:

```
    1/15 - src/nmon/__init__.py         [clean - sonnet]
    2/15 - src/nmon/models.py           [opus + ultrathink]
    3/15 - src/nmon/config.py           [opus + ultrathink]
    4/15 - src/nmon/storage.py          [opus + ultrathink]
    5/15 - src/nmon/gpu/__init__.py     [clean - sonnet]
    6/15 - src/nmon/gpu/base.py         [clean - sonnet]
    7/15 - src/nmon/gpu/nvml_source.py  [opus + ultrathink]
```

### Override behavior

| Flags | Files with bugs | Clean files |
|-------|----------------|-------------|
| (none) | Opus + ultrathink | Sonnet, no ultrathink |
| `-Model opus` | Opus + ultrathink | Opus, no ultrathink |
| `-Model sonnet` | Sonnet + ultrathink | Sonnet, no ultrathink |
| `-Ultrathink` | Opus + ultrathink | Sonnet + ultrathink |
| `-NoUltrathink` | Opus, no ultrathink | Sonnet, no ultrathink |
| `-Model opus -Ultrathink` | Opus + ultrathink | Opus + ultrathink |
| `-Model sonnet -NoUltrathink` | Sonnet, no ultrathink | Sonnet, no ultrathink |

### Cost implications

A typical run with 15 source files where 8 have real bugs:
- **Without smart selection**: 15 Opus + ultrathink calls
- **With smart selection**: 8 Opus + ultrathink calls + 7 Sonnet calls

The Sonnet calls for clean files are fast and cheap -- they typically return
"No changes needed for src/nmon/__init__.py" in a few seconds. The Opus calls are
reserved for files where the reasoning quality directly impacts code correctness.

## File Layout

### Temporary files (cleaned up on completion)

| File | Purpose |
|------|---------|
| `.debug_progress` | Tracks last completed step and sub-step |
| `.debug_changes.md` | Running log of per-file changes from Step 5 |

### Output files

| File | Step | Purpose |
|------|------|---------|
| `architecture/DATA_FLOW.md` | 1 | System-wide data flow analysis |
| `architecture/INTERFACES.md` | 2 | Synthesized interface contracts |
| `architecture/interfaces/*.iface.md` | 2 | Per-file interface contracts |
| `test_gaps/GAP_REPORT.md` | 3 | Prioritized test gap report |
| `test_gaps/src/*.gap.md` | 3 | Per-file test gap analysis |
| `bug_reports/SUMMARY.md` | 4 | Bug hunt summary |
| `bug_reports/src/*.md` | 4 | Per-file bug reports |
| `Implemented Plans/Bug Fix Changes N.md` | 6 | Archived change log with timestamp |

## Integration with Arch_Coding_Pipeline.ps1

The `Bug Fix Changes N.md` files archived in Step 6 are read by
`Arch_Coding_Pipeline.ps1` during its Stage 0 (Summarize Existing Codebase). This
means the next time you generate new aider commands, Claude will know:

- What bugs were found and fixed
- What code patterns led to those bugs (so it avoids reintroducing them)
- The current state of the codebase after fixes

The chronological ordering is preserved via `<!-- Timestamp: ... -->` headers on all
files in `Implemented Plans/`, so the codebase summary reflects the correct sequence:
initial architecture, then bug fixes, then new features.

## Usage Examples

```powershell
# Basic usage
..\LocalLLM_Pipeline\LocalLLMDebug\Arch_Debug_Pipeline.ps1 -TargetDir src/nmon

# Use second Claude account
..\LocalLLM_Pipeline\LocalLLMDebug\Arch_Debug_Pipeline.ps1 -TargetDir src/nmon -Claude Claude2

# Force Opus for all files (skip smart selection)
..\LocalLLM_Pipeline\LocalLLMDebug\Arch_Debug_Pipeline.ps1 -TargetDir src/nmon -Model opus

# Force ultrathink for all files including clean ones
..\LocalLLM_Pipeline\LocalLLMDebug\Arch_Debug_Pipeline.ps1 -TargetDir src/nmon -Ultrathink

# Budget mode: Sonnet everywhere, no ultrathink
..\LocalLLM_Pipeline\LocalLLMDebug\Arch_Debug_Pipeline.ps1 -TargetDir src/nmon -Model sonnet -NoUltrathink

# Resume after interruption (automatic)
..\LocalLLM_Pipeline\LocalLLMDebug\Arch_Debug_Pipeline.ps1 -TargetDir src/nmon

# Start over from scratch
..\LocalLLM_Pipeline\LocalLLMDebug\Arch_Debug_Pipeline.ps1 -TargetDir src/nmon -Restart

# Preview without running
..\LocalLLM_Pipeline\LocalLLMDebug\Arch_Debug_Pipeline.ps1 -TargetDir src/nmon -DryRun

# Override the .env file (default is LocalLLM_Pipeline/Common/.env)
..\LocalLLM_Pipeline\LocalLLMDebug\Arch_Debug_Pipeline.ps1 -TargetDir src/nmon -EnvFile .\my_custom\.env
```

## Prerequisites

- **Claude Code CLI** (`claude`) installed and available in PATH
- **Ollama server** running and accessible (for steps 1-4)
- **PowerShell 5.1+** (Windows PowerShell or PowerShell Core)
- At least one Claude account configured (Claude1 or Claude2)
- The LocalLLMDebug analysis scripts and their prompt templates
- Source code in the target directory with an existing test suite
