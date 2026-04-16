# Debugging Workflow — nmon2

This document describes the full LLM-assisted debugging pipeline available in this repository.
Each tool in `LocalLLMDebug/` is a local LLM pass with a specific focus: understanding the
architecture, finding bugs, tracing data flow, auditing test coverage, or mapping interface
contracts. Used together they give Claude Code (or any human reviewer) a multi-angle picture
of the codebase before starting a debugging session.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Arch_Analysis_Pipeline — Architecture Foundation](#2-arch_analysis_pipeline--architecture-foundation)
3. [bughunt_local — Quick Bug Scan](#3-bughunt_local--quick-bug-scan)
4. [bughunt_iterative_local — Iterative Bug Fix](#4-bughunt_iterative_local--iterative-bug-fix)
5. [dataflow_local — Data Flow Trace](#5-dataflow_local--data-flow-trace)
6. [testgap_local — Test Gap Analysis](#6-testgap_local--test-gap-analysis)
7. [interfaces_local — Interface Contract Summary](#7-interfaces_local--interface-contract-summary)
8. [Recommended Debugging Workflow](#8-recommended-debugging-workflow)
9. [Configuration Reference (.env)](#9-configuration-reference-env)
10. [Output File Index](#10-output-file-index)
11. [Reading the Outputs](#11-reading-the-outputs)

---

## 1. Prerequisites

### Ollama server

All scripts call the local LLM via the Ollama OpenAI-compatible API. The server must be running
and reachable before any script is invoked.

```
# Pull the model (first time only)
ollama pull qwen3-coder:30b        # or qwen2.5-coder:14b on smaller GPUs

# Verify the server is up
curl http://192.168.1.126:11434/api/tags
```

See **[Choosing a model](#choosing-a-model)** below for sizing and VRAM
considerations. A per-request `num_ctx=32768` is passed via the native Ollama
`/api/chat` endpoint, so no custom `Modelfile` is needed.

### Configuration

All scripts read `LocalLLM_Pipeline/Common/.env`. Critical keys:

| Key                  | Current value                     | Purpose                                           |
|----------------------|-----------------------------------|---------------------------------------------------|
| `LLM_ENDPOINT`       | (unset) / derived from HOST+PORT  | Full Ollama URL; when set, wins over HOST+PORT    |
| `LLM_HOST`           | `192.168.1.126`                   | Ollama server host (fallback if `LLM_ENDPOINT` unset) |
| `LLM_PORT`           | `11434`                           | Ollama server port                                |
| `LLM_MODEL`          | `qwen3-coder:30b`                 | Analysis-script model                             |
| `LLM_NUM_CTX`        | `32768`                           | Per-request context window (native `/api/chat`)   |
| `LLM_TIMEOUT`        | `300`                             | Per-request timeout (seconds)                     |
| `PRESET`             | `python`                          | File pattern preset (defined in `llm_common.ps1`) |
| `INCLUDE_EXT_REGEX`  | `\.(py\|toml)$`                   | Files to include                                  |
| `EXCLUDE_DIRS_REGEX` | `(\.git\|__pycache__\|\.venv\|…)` | Directories to skip                               |

`Common/.env` also defines `LLM_PLANNING_MODEL` / `LLM_PLANNING_NUM_CTX` (used
by `Arch_Coding_Pipeline.ps1 -Local`) and `LLM_AIDER_MODEL` /
`LLM_AIDER_NUM_CTX` (used by `run_aider.py --local`). The analysis scripts
documented here only consume the `LLM_MODEL` / `LLM_NUM_CTX` pair.

### Optional: LocalLLMAnalysis integration keys

Three Debug scripts can optionally consume artifacts from the Analysis pipeline
to improve their prompts. All three **silently skip** when the expected file
isn't present, so nothing breaks if Analysis hasn't been run.

| Debug script               | Injects                                          | Key                   | Default           |
|----------------------------|--------------------------------------------------|-----------------------|-------------------|
| `bughunt_local.ps1`        | `<ARCHITECTURE_DIR>/xref_index.md` per file      | `ARCHITECTURE_DIR`    | `architecture`    |
| `dataflow_local.ps1` synth | `<ARCHITECTURE_DIR>/architecture.md`             | `ARCHITECTURE_DIR`    | `architecture`    |
| `interfaces_local.ps1`     | `<SERENA_CONTEXT_DIR>/<rel>.serena_context.txt`  | `SERENA_CONTEXT_DIR`  | `.serena_context` |

**Workflow:** run `Arch_Analysis_Pipeline.py` first. After each subsection
completes, the Analysis pipeline renames `architecture/` to `N. <subsection>/`
— update `ARCHITECTURE_DIR` in `Common/.env` to match (e.g.
`ARCHITECTURE_DIR=1. src`). Then re-run the Debug scripts; they'll find the
new files and inject them as context.

**Why:** bughunt single-file review misses integration bugs that cross module
boundaries — injecting the xref index gives it caller/callee awareness.
Interfaces contract extraction is noisy when inferred from source text alone
— injecting LSP symbol context grounds it in authoritative type information.
Dataflow synth invents subsystem boundaries from per-file extractions —
injecting `architecture.md` gives it a pre-synthesized scaffold to anchor to.

**Caveats:**

- **Cache invalidation.** `interfaces_local.ps1` SHA1-caches per-file
  extractions on the *source file* hash, not on prompt content. Pre-integration
  cached extractions will be reused silently until the source changes — so
  flipping `SERENA_CONTEXT_DIR` on won't retroactively improve already-cached
  contracts. Run `-Clean` to force fresh extraction with LSP context.
  `dataflow_local.ps1` has a similar per-file cache for the extract pass, but
  the synth pass (which consumes `architecture.md`) is not cached, so the
  integration takes effect immediately there.
- **Token budget.** At `LLM_NUM_CTX=32768` the xref and architecture
  injections add 2-10K tokens on top of the existing 7-10K per-file prompts.
  Large codebases may approach the window ceiling. If you see truncation
  warnings, either bump `LLM_NUM_CTX` (see VRAM caveat below) or slim the
  injected artifact (trim `xref_index.md` to the subsystem you're reviewing).
- **KV cache / VRAM.** Raising `LLM_NUM_CTX` is not free. At `qwen3-coder:30b`
  (q4_K_M, ~20 GB weights), KV cache on a 24 GB card costs roughly +1 GB per
  8K of `num_ctx`. The jump from 32768 → 49152 (Analysis default) adds ~2 GB;
  going further to 65536 will likely force partial CPU offload and slow
  inference 3-4×. Verify with `ollama ps` — `PROCESSOR` must read `100% GPU`
  for full-speed inference. If you see anything else, drop `num_ctx` back
  down or switch to a smaller model.

### Working directory

**All scripts must be run from your target project's root** (e.g. `cd C:\Coding\nmon`), not from inside the toolkit. The toolkit is a sibling directory, so paths look like `..\LocalLLM_Pipeline\LocalLLMDebug\<script>.ps1`.

```powershell
# Correct
..\LocalLLM_Pipeline\LocalLLMDebug\bughunt_local.ps1

# Wrong — paths will be resolved incorrectly
cd LocalLLMDebug
.\bughunt_local.ps1
```

### Choosing a model

The analysis scripts default to **`qwen3-coder:30b`** with a per-request
`num_ctx=32768`. This pair is set in `LocalLLM_Pipeline/Common/.env`:

```ini
LLM_MODEL=qwen3-coder:30b
LLM_NUM_CTX=32768
```

`Invoke-LocalLLM` detects `LLM_NUM_CTX > 0` and routes requests through the
native Ollama `/api/chat` endpoint with `options.num_ctx` set per request, so
every call gets the full window without any pre-registered `Modelfile` variant.

#### Per-request `num_ctx` vs custom Modelfile

The older approach was to pre-register custom models like
`qwen2.5-coder:32b-8k` / `:32b-12k` via `Modelfile` (`PARAMETER num_ctx 8192`)
so each script could pick the right pre-baked window. That is obsolete. The
current scripts pass `num_ctx` per request, so a single base model tag covers
every script. You only need:

```powershell
ollama pull qwen3-coder:30b
```

If your server already has older `32b-8k` / `32b-12k` tags from previous
documentation, they still work — just set `LLM_MODEL` to whichever tag you
want and leave `LLM_NUM_CTX` unset (or set it to 0) to fall back to the
legacy `/v1/chat/completions` path with no per-request override.

#### VRAM considerations

KV cache scales roughly linearly with context length. On a 24 GB card at
`num_ctx=32768`, expect ~5-8 GB of KV cache on top of 17-20 GB of weights.
Verify with:

```powershell
ollama ps    # PROCESSOR column should say "100% GPU"
```

If `PROCESSOR` shows any CPU fraction (`75% GPU / 25% CPU` etc.), the model is
partially offloaded and inference slows 3-4×. Either drop `LLM_NUM_CTX` (e.g.
16384 or 12288) or switch to a smaller model:

```ini
# Smaller GPU config (<24 GB)
LLM_MODEL=qwen2.5-coder:14b
LLM_NUM_CTX=32768            # 14b is small enough that 32k fits
LLM_TIMEOUT=180
```

The 14b model finds roughly 80% of what the 30b finds in practice; it's a real
reviewer, not an agentic model, and it converges cleanly on the iterative
bug-fix loop.

#### What about devstral-small-2?

Don't. It's an agentic model, not a reviewer — tuned to take action on
SWE-bench tasks, which means on iterative review it aggressively rewrites
code, hallucinates new "bugs" each pass, and ballooned files by 2-6× in
earlier testing. All the convergence and bloat guards in
`bughunt_iterative_local.ps1` were added because of devstral behavior. If you
see `BLOAT` or `DIVERGING` statuses firing frequently, a weak/agentic model
is usually the cause.

#### Per-script context needs (approximate)

Approximate tokens per call for an 800-line source file (`MAX_FILE_LINES=800`):

| Script                                       | Max input | Output cap | Total needed |
|----------------------------------------------|-----------|------------|--------------|
| `archgen_local.ps1` (per-file)               | ~7400     | 900        | ~8300        |
| `arch_overview_local.ps1` (chunked synth)    | ~4000     | 1200       | ~5200        |
| `archpass2_local.ps1` (per-file)             | ~4000     | 900        | ~4900        |
| `bughunt_local.ps1`                          | ~7000     | 900        | ~7900        |
| `bughunt_iterative_local.ps1` (analysis)     | ~7000     | 900        | ~7900        |
| `bughunt_iterative_local.ps1` (fix call)     | ~7000     | 4000       | ~11000       |
| `dataflow_local.ps1` (per-file extract)      | ~6500     | 400        | ~6900        |
| `dataflow_local.ps1` (synthesis)             | ~6000     | 1800       | ~7800        |
| `interfaces_local.ps1` (per-file extract)    | ~7000     | 700        | ~7700        |
| `interfaces_local.ps1` (synthesis)           | ~10500    | 2000       | ~12500       |
| `testgap_local.ps1` (per-file)               | ~7500     | 700        | ~8200        |
| `testgap_local.ps1` (synthesis)              | ~10500    | 1800       | ~12300       |

All comfortably under `num_ctx=32768`. The `LLM_MODEL_HIGH_CTX` key is still
read by `bughunt_iterative_local.ps1`, `interfaces_local.ps1`, and
`testgap_local.ps1` for backward compatibility, but with per-request
`num_ctx` there is no reason to point it at a different model -- the default
`Common/.env` sets both `LLM_MODEL` and `LLM_MODEL_HIGH_CTX` to the same tag.

---

## 2. Arch_Analysis_Pipeline — Architecture Foundation

**Script:** `LocalLLMAnalysis/Arch_Analysis_Pipeline.py`
**Run:** `python LocalLLMAnalysis/Arch_Analysis_Pipeline.py`

### Purpose

`Arch_Analysis_Pipeline.py` is the orchestration entry point. It reads the `#Subsections begin` /
`#Subsections end` block from `.env`, iterates over each listed subdirectory, and runs the
full six-step architecture analysis pipeline on it. Results go into `architecture/` at the
repo root, which is renamed to `N. <subsection>` after each subsection completes.

### Pipeline steps (per subsection)

| Step | Script                    | `use_target_dir` | Description                                 |
|------|---------------------------|------------------|---------------------------------------------|
| 1    | `archgen_local.ps1`       | Yes              | Per-file `.md` architecture docs            |
| 2    | `archxref.ps1`            | No               | Cross-reference index (no LLM)              |
| 3    | `archgraph.ps1`           | No               | Mermaid call-graph diagrams (no LLM)        |
| 4    | `arch_overview_local.ps1` | No               | Subsystem-level overview doc                |
| 5    | `archpass2_context.ps1`   | No               | Targeted context for complex files (no LLM) |
| 6    | `archpass2_local.ps1`     | No               | Selective re-analysis of complex files      |

Steps 2, 3, and 5 are text-processing only — they run fast with no LLM calls. Steps 1, 4,
and 6 each invoke the local LLM, so they take the bulk of the time.

### Subsection configuration

The `.env` block controls which directories are processed:

```ini
#Subsections begin
src
# ~16 Python files -- nmon package
#Subsections end
```

Comment lines starting with `#` are ignored. Each non-comment line becomes a `-TargetDir`
argument passed to `archgen_local.ps1`. For nmon2 there is one subsection (`src`), so the
pipeline runs once and produces `1. src/` at the repo root.

### Skip logic

Before running each subsection, `Arch_Analysis_Pipeline.py` checks whether a numbered output folder
already exists (e.g. `1. src`). If it does, the subsection is skipped. To re-run a
completed subsection, delete or rename the output folder first.

### CLI options

```powershell
python LocalLLMAnalysis/Arch_Analysis_Pipeline.py
python LocalLLMAnalysis/Arch_Analysis_Pipeline.py --dry-run          # show commands, don't run
python LocalLLMAnalysis/Arch_Analysis_Pipeline.py --start-from 2     # skip subsection 1
python LocalLLMAnalysis/Arch_Analysis_Pipeline.py --skip-lsp         # omit generate_compile_commands + serena_extract
```

`--skip-lsp` is important for Python projects: the LSP steps (`generate_compile_commands.py`
and `serena_extract.ps1`) are designed for C++ codebases with clangd. Always pass
`--skip-lsp` when running on nmon2.

```powershell
python LocalLLMAnalysis/Arch_Analysis_Pipeline.py --skip-lsp
```

### Output

```
1. src/                         # renamed architecture/ after the subsection completes
  ├── <rel_path>.md             # per-file architecture docs (from archgen)
  ├── ARCHITECTURE.md           # subsystem overview (from arch_overview)
  ├── CROSS_REFERENCE.md        # symbol cross-reference index (from archxref)
  └── graphs/                   # Mermaid diagrams (from archgraph)
LocalLLMDebug/pipeline.log      # full run log with timestamps
```

### Role in the debugging workflow

The architecture docs generated by `Arch_Analysis_Pipeline.py` are the **foundation context** for all
subsequent debugging. Before running bug analysis, run the pipeline so that:

- Per-file `.md` docs explain each module's purpose and design
- `ARCHITECTURE.md` gives the subsystem overview
- Cross-reference index shows which modules call which
- Mermaid graphs visualise the call structure

These outputs do not feed directly into the other scripts (which all read source directly),
but they are what you load into a Claude Code session to orient the debugger.

---

## 3. bughunt_local — Quick Bug Scan

**Script:** `LocalLLMDebug/bughunt_local.ps1`
**Prompt:** `LocalLLMDebug/bughunt_prompt.txt`
**Output:** `bug_reports/`

### Purpose

`bughunt_local.ps1` is a **single-pass, non-modifying** bug scanner. It sends every source
file to the local LLM and asks it to identify bugs, then writes a report per file and a
combined `SUMMARY.md`. Nothing is modified. It is the right starting point when you want to
know *what* is broken before committing to automatic fixes.

### How it works

1. **File collection** — All files matching `INCLUDE_EXT_REGEX` under `$TargetDir` are
   gathered, excluding `architecture/`, `bug_reports/`, and directories matching
   `EXCLUDE_DIRS_REGEX`.

2. **SHA1 skip** — Each file's SHA1 is compared against `bug_reports/.bughunt_state/hashes.tsv`.
   Files whose hash and report both exist are skipped. This makes re-runs fast after editing
   a subset of files.

3. **LLM call** — For each file in the queue, the prompt schema (`bughunt_prompt.txt`) plus
   the file contents (truncated to `MAX_FILE_LINES=800` if necessary) are sent to the LLM.
   Max output tokens: `BUGHUNT_MAX_TOKENS` (default 900).

4. **Report write** — The response is written to `bug_reports/<rel_path>.md`. If the response
   doesn't start with a markdown heading, one is added automatically.

5. **SUMMARY.md** — After all files are processed, every `bug_reports/*.md` is re-read to
   count `[HIGH]`, `[MEDIUM]`, and `[LOW]` tags. Files with HIGH or MEDIUM findings are listed.

### Severity tags

The LLM uses these tags in report headings:

| Tag        | Meaning                                                  |
|------------|----------------------------------------------------------|
| `[HIGH]`   | Data loss, crash, or silent failure in a critical path   |
| `[MEDIUM]` | Incorrect behaviour under a reachable condition          |
| `[LOW]`    | Edge case unlikely in normal operation                   |
| `[INFO]`   | Defensiveness gap — missing validation, unclear contract |

The SUMMARY.md counts `[HIGH]` and `[MEDIUM]` per file. `[LOW]` and `[INFO]` appear in
individual reports but are not highlighted in the summary.

### Report format

Each `bug_reports/<rel>.md` follows this schema:

```markdown
# src/nmon/collector.py

## Findings

### Race condition in latest_sample() [HIGH]
- **Where:** `latest_sample()` ~line 47
- **Issue:** `_latest` dict read without acquiring `_lock`.
- **Impact:** Caller receives partial or corrupted snapshot when collector thread writes simultaneously.
- **Fix:** Acquire `_lock` before reading `_latest`.

## Verdict
ISSUES FOUND
```

Verdict is one of: `CLEAN` | `ISSUES FOUND` | `NEEDS REVIEW`.

### CLI reference

```powershell
# Scan all source files
..\LocalLLM_Pipeline\LocalLLMDebug\bughunt_local.ps1

# Scan only GPU subsystem
..\LocalLLM_Pipeline\LocalLLMDebug\bughunt_local.ps1 -TargetDir src/nmon/gpu

# Delete all reports and caches, then re-scan
..\LocalLLM_Pipeline\LocalLLMDebug\bughunt_local.ps1 -Clean

# Ignore SHA1 cache, re-scan every file regardless
..\LocalLLM_Pipeline\LocalLLMDebug\bughunt_local.ps1 -Force
```

### When to use

- **First pass after getting a new codebase** — run once to establish a baseline severity map
- **After a complex merge** — re-run to see if new HIGH/MEDIUM issues appeared
- **Before starting a fix session** — the `SUMMARY.md` tells you which files to prioritise
- **Quick triage** — faster than `bughunt_iterative_local.ps1` because it does not attempt fixes

### Output structure

```
bug_reports/
  SUMMARY.md                   # files with HIGH/MEDIUM, total counts
  src/
    nmon/
      collector.py.md          # bug report for collector.py
      storage.py.md
      gpu/
        nvml_source.py.md
  .bughunt_state/
    hashes.tsv                 # <sha> TAB <rel> — skip cache
    last_error.log             # LLM errors appended here
```

---

## 4. bughunt_iterative_local — Iterative Bug Fix

**Script:** `LocalLLMDebug/bughunt_iterative_local.ps1`
**Prompts:** `bughunt_prompt.txt`, `bughunt_dataflow_prompt.txt`,
            `bughunt_contracts_prompt.txt`, `bughunt_tests_prompt.txt`,
            `bughunt_fix_prompt.txt`
**Output:** `bug_fixes/`

### Purpose

`bughunt_iterative_local.ps1` goes beyond detection: for each file it **iteratively analyses
and fixes** until no HIGH or MEDIUM issues remain, a stop condition is hit, or `MaxIterations`
is reached. It runs up to four distinct analysis types per iteration and writes fixed files
to `bug_fixes/`. Fixes can optionally be written back to the source tree immediately with
`-ApplyFixes`.

**Convergence and bloat guards.** Because local LLMs tend to diverge on iterative review
tasks (finding fresh "bugs" each pass, rewriting code more aggressively, or ballooning file
size), the script tracks the **best** (lowest HIGH, tie-broken by lowest MED) version seen
across all iterations and reverts to it on any non-CLEAN termination. A **DIVERGING** status
aborts the loop when HIGH fails to improve for `BUGHUNT_DIVERGE_AFTER` consecutive iterations
(default 2). A **BLOAT** status rejects any fix that grows the file beyond `BUGHUNT_BLOAT_RATIO`
× the original line count (default 1.5, with a floor of `+BUGHUNT_BLOAT_MIN_SLACK` lines,
default 15). Combined, these mean a bad model run can no longer make a file *worse* than it
started.

### Four analysis dimensions

The script can run any combination of the following analysis types. All four are enabled by
default; each can be disabled independently.

#### 1. Bug analysis (`-SkipBugs` to disable)

Prompt: `bughunt_prompt.txt`

Covers the classic bug categories: unhandled exceptions, overly broad `except` clauses that
swallow errors, resource leaks (file handles, DB connections, GPU handles not released on
error paths), race conditions, off-by-one errors, API misuse, logic errors (wrong operator,
inverted condition), data loss from silent write failures, None/null dereferences, type
mismatches, and missing cleanup in error paths.

#### 2. Data flow analysis (`-SkipDataflow` to disable)

Prompt: `bughunt_dataflow_prompt.txt`

Focuses specifically on how data moves *between modules*:
- Data received from another module used without validation (unchecked None, unvalidated type,
  out-of-range index)
- Type mismatches at module boundaries (e.g. `bytes` returned where `str` expected)
- Incorrect data transformations (wrong scale factor, wrong field extracted)
- Missing or incorrect error propagation (exception caught but original error discarded,
  return value ignored, caller not notified of partial failure)
- Race conditions on shared mutable state accessed without a lock
- Stale data accepted as current (cache not invalidated, timestamp not checked)
- Data written to the wrong destination (wrong key, wrong field, wrong path)

#### 3. Contract analysis (`-SkipContracts` to disable)

Prompt: `bughunt_contracts_prompt.txt`

Verifies that each function's implicit contract is honoured:
- Missing precondition checks (parameter used without validation)
- Postcondition failures (function returns `None` where non-`None` is guaranteed, or returns
  a partial result without signalling incompleteness)
- Resource leaks — file handles, DB connections, sockets, GPU handles, or locks acquired but
  not guaranteed to be released on every exit path (missing `finally`, missing context manager,
  exception swallowed before cleanup)
- Invariant violations — object left in an inconsistent state after a failed operation
- Thread-safety contract violations — method expected to be thread-safe but reads/writes shared
  state without a lock

#### 4. Test quality analysis (`-SkipTests` to disable)

Prompt: `bughunt_tests_prompt.txt`

This analysis targets **test files**, not source files. It is run in a separate loop (see
below) and looks for:
- Tests that always pass regardless of the implementation (`assert True`, `assert x is not None`
  without checking the actual value, empty test body)
- Mocks whose interface contradicts the real source (wrong return type, wrong signature,
  missing `side_effect` for calls that should raise)
- Assertions too weak to catch regressions (e.g. `assert len(result) > 0` when the exact
  value is deterministic and testable)
- Test setup that defeats the purpose of the test (fixture returns empty data, `patch` replaces
  the exact function being tested)
- Missing tests for HIGH-risk source paths (exception handlers, thread-safety, error return
  paths, boundary conditions)
- Tests that depend on uncontrolled external state (real time, real filesystem, real network
  when not intended)
- Incorrect expected values (hardcoded value that does not match what the source actually
  computes)

### Source loop mechanics

For each source file in the queue, the script runs an iterative loop with up to `MaxIterations`
(default 3) cycles. Each cycle:

**Step 1 — Multi-type analysis.**
All enabled source analysis types (bugs, dataflow, contracts) are run as independent LLM
calls against the current working copy of the file. Each call produces a separate report. The
console shows per-type severity as each completes:

```
  Iter 1/3: analysing (143 lines)
    [bugs]...     H:1 M:1 L:0
    [dataflow]... H:0 M:1 L:0
    [contracts].. H:1 M:0 L:0
    Combined: HIGH:2  MED:2  LOW:0  (best@iter 1: H:2 M:2)
```

The trailing `best@iter N: H:x M:y` shows the running best-version seen so far — this is
what the script will write back if the run ends without reaching CLEAN.

If one analysis type fails (LLM error) but others succeed, the partial combined report is
still used. Only if all enabled types fail is the file marked `ERROR`.

**Step 2 — Combined report.** The individual reports are concatenated under headings
(`## Bug Analysis`, `## Data Flow Analysis`, `## Contract Analysis`) separated by `---`. The
combined report drives the stop/fix decision. `[HIGH]` and `[MEDIUM]` tags are counted across
all sections.

**Step 3 — Best-version tracking.** If the current iteration's HIGH is strictly lower than
the running best (or HIGH ties and MED is strictly lower), the current working content
becomes the new "best" and the non-improving streak resets. Otherwise the streak increments.

**Step 4 — Stop if clean.** If the combined report contains no `[HIGH]` or `[MEDIUM]` tags,
the file is marked `CLEAN` and the loop exits.

**Step 5 — Stop if diverging.** If the non-improving streak hits `BUGHUNT_DIVERGE_AFTER`
(default 2), the file is marked `DIVERGING`. The loop exits and the best version seen so far
is written (not the current, diverged content).

**Step 6 — Stop if last iteration.** If `MaxIterations` is reached without going clean, the
file is marked `MAX_ITER`. The best version seen is written (not necessarily the last one).

**Step 7 — Fix.** A single fix call is made with `bughunt_fix_prompt.txt` + the combined
report + the current file content. The fix LLM is instructed to fix ALL HIGH and MEDIUM items
from all analysis types in one pass. Max output tokens: `BUGHUNT_FIX_TOKENS` (default 4000).

**Step 8 — Code block extraction.** The fix response is parsed for a triple-backtick code
fence. The extraction tries the file's own language fence first, then `python`, then a generic
fence. If no valid code block is found, the file is marked `STUCK`.

**Step 9 — No-op detection.** If the extracted code is identical to the current working
content, the file is marked `STUCK` (the LLM cannot make progress).

**Step 10 — Bloat guard.** If the fixed code's line count exceeds `max(orig × BUGHUNT_BLOAT_RATIO,
orig + BUGHUNT_BLOAT_MIN_SLACK)`, the fix is rejected and the file is marked `BLOAT`. This
catches the common local-LLM failure mode where "fix" responses hallucinate large blocks of
defensive or duplicated code that the next iteration then flags as new problems.

**Step 11 — Syntax validation (Python files only).** The fixed code is written to a temp file
and compiled with `python -m py_compile`. If compilation fails, the fix is rejected and the
file is marked `SYNTAX_ERR`. (This step is hardened against PowerShell's `StrictMode + Stop`
stderr-escalation trap so a syntax error can never crash the whole run.)

**Step 12 — Accept.** The working content is updated to the fixed code and the next iteration
begins.

On any non-CLEAN exit (`MAX_ITER`, `DIVERGING`, `BLOAT`, `STUCK`, `SYNTAX_ERR`, `ERROR`), the
script writes the **best** version seen across all iterations — not the latest — so a bad run
can never leave a file worse than it started.

### Stop conditions

| Status       | Meaning                                                                            | Written content |
|--------------|------------------------------------------------------------------------------------|-----------------|
| `CLEAN`      | No HIGH or MEDIUM findings in the combined report                                  | Final iteration |
| `MAX_ITER`   | Reached `MaxIterations` without going clean — bugs remain                          | Best iteration  |
| `DIVERGING`  | HIGH failed to improve for `BUGHUNT_DIVERGE_AFTER` consecutive iterations          | Best iteration  |
| `BLOAT`      | Fix grew file beyond `BUGHUNT_BLOAT_RATIO` × original line count                   | Best iteration  |
| `STUCK`      | LLM returned no code block, or identical code; no further progress possible        | Best iteration  |
| `SYNTAX_ERR` | Fixed code failed `python -m py_compile` — rejected to prevent breaking the file   | Best iteration  |
| `ERROR`      | LLM call failed (network, timeout, model error) for every analysis type in an iter | Best iteration  |

### Test loop mechanics

After the source loop completes for all files, a separate test loop runs for every source
file that has a matched test file. Test file discovery uses `Find-TestFile`, which tries
three naming conventions:

- **C1** — `test_` + all path parts after `src/` joined with `_` + `.py`
  e.g. `src/nmon/gpu/nvml_source.py` → `tests/test_gpu_nvml_source.py`
- **C2** — `test_` + stem only
  e.g. → `tests/test_nvml_source.py`
- **C3** — strip common implementation suffixes (`_source`, `_base`, `_impl`, `_provider`,
  `_backend`) from the stem, rejoin with path parts
  e.g. `nvml_source` → `nvml` → `tests/test_gpu_nvml.py`

The first candidate that exists on disk wins. If none match, no test analysis is run for
that source file.

Each test iteration sends **both the source file content and the test file content** to the
test analysis LLM, so it can judge whether the test assertions match what the source actually
does. The fix call then operates on the test file only. Stop conditions are the same as the
source loop.

### Hash database

The hash DB at `bug_fixes/.bughunt_iter_state/hashes.tsv` uses two entry formats:

```
<sha1>  <rel_path>           # source file entry
<sha1>  test:<rel_path>      # test file entry
```

On re-run, files whose hash and iteration log both exist are skipped. Use `-Force` to ignore
the cache and re-process everything. Use `-Clean` to delete all output and start fresh.

### CLI reference

```powershell
# Run all four analysis types on all source files
..\LocalLLM_Pipeline\LocalLLMDebug\bughunt_iterative_local.ps1

# Run only bug analysis (skip dataflow, contracts, tests)
..\LocalLLM_Pipeline\LocalLLMDebug\bughunt_iterative_local.ps1 -SkipDataflow -SkipContracts -SkipTests

# Run only data flow and contract analysis, no bugs, no tests
..\LocalLLM_Pipeline\LocalLLMDebug\bughunt_iterative_local.ps1 -SkipBugs -SkipTests

# Scan a specific subdirectory; specify where tests live
..\LocalLLM_Pipeline\LocalLLMDebug\bughunt_iterative_local.ps1 -TargetDir src/nmon/gpu -TestDir tests

# Allow up to 6 fix attempts per file (default is 3)
..\LocalLLM_Pipeline\LocalLLMDebug\bughunt_iterative_local.ps1 -MaxIterations 6

# Write fixes back to source immediately after each file
..\LocalLLM_Pipeline\LocalLLMDebug\bughunt_iterative_local.ps1 -ApplyFixes

# Delete all output and caches, then run fresh
..\LocalLLM_Pipeline\LocalLLMDebug\bughunt_iterative_local.ps1 -Clean

# Ignore SHA1 cache, re-process all files
..\LocalLLM_Pipeline\LocalLLMDebug\bughunt_iterative_local.ps1 -Force
```

**Tuning the convergence guards** via `.env` (no CLI flags, these are env-only). Defaults
shown — only set these if you need to deviate:

```ini
# Tighten bloat limit for a weaker model that hallucinates large additions
BUGHUNT_BLOAT_RATIO=1.25      # default 1.5
BUGHUNT_BLOAT_MIN_SLACK=10    # default 15

# Or relax for a stronger model that restructures more
BUGHUNT_BLOAT_RATIO=2.0

# Give a slow-but-improving model more rope before aborting
BUGHUNT_DIVERGE_AFTER=3       # default 2

# 4000-token fix calls on 32B+ models can exceed the 300s default
LLM_TIMEOUT=600               # default 300 for this script
```

**Safety:** By default, `-ApplyFixes` is NOT set. Fixed files are staged in `bug_fixes/` for
manual review. Inspect the diff before copying to source:

```powershell
diff src/nmon/collector.py bug_fixes/src/nmon/collector.py
# or
code --diff src/nmon/collector.py bug_fixes/src/nmon/collector.py
```

### Output structure

```
bug_fixes/
  SUMMARY.md                          # source table + test table + totals
  src/
    nmon/
      collector.py                    # fixed source file (if changed)
      collector.py.iter_log.md        # per-iteration analysis + fix log
      storage.py.iter_log.md
      gpu/
        nvml_source.py.iter_log.md
  tests/
    test_collector.py                 # fixed test file (if changed)
    test_collector.py.iter_log.md
  .bughunt_iter_state/
    hashes.tsv                        # skip cache (source + test entries)
    last_error.log                    # LLM error log
```

The iteration log (`*.iter_log.md`) is the primary diagnostic when a file doesn't reach
`CLEAN`. It records the full analysis output for every iteration and the reason each fix was
accepted, rejected, or stopped.

### When to use

- **After bughunt_local.ps1 identifies HIGH issues** — switch to iterative to get LLM-assisted
  fixes staged automatically
- **Targeted subsystem fix** — use `-TargetDir src/nmon/gpu` to focus on one area
- **Before a code review** — run with `-ApplyFixes` on a branch to let the LLM pre-fix known
  issues, then review the diff as a whole
- **Test quality audit** — run with `-SkipBugs -SkipDataflow -SkipContracts` to focus
  exclusively on test file improvements

---

## 5. dataflow_local — Data Flow Trace

**Script:** `LocalLLMDebug/dataflow_local.ps1`
**Prompts:** `dataflow_extract_prompt.txt`, `dataflow_synth_prompt.txt`
**Output:** `architecture/DATA_FLOW.md`

### Purpose

`dataflow_local.ps1` builds a **debugging-focused map of how data moves through the nmon
pipeline**. The goal is to answer: "When a GPU metric is wrong on screen, at which stage did
the error enter?" It traces data from the GPU sources (NVML, nvidia-smi XML) through the
Collector, into SQLite storage, and out to the TUI renderer.

### Two-pass design

#### Pass 1 — Per-file interface extraction

Each source file is sent to the LLM with `dataflow_extract_prompt.txt`. The prompt asks for
a compact description of only the file's **pipeline interface**:

- **Role** — one sentence
- **Types Defined** — data types this file owns (e.g. `GPUSample`, `StorageConfig`)
- **Produces** — what data structures leave this module and where they go
- **Consumes** — what data structures arrive from other modules
- **Threading** — lock usage, thread ownership, shared state
- **Error Surface** — what exceptions propagate out vs. are swallowed internally

Results are cached in `architecture/.dataflow_state/extractions/<sha1>.txt`. Only files
whose SHA1 has changed since the last run are re-extracted. This means that after editing one
file, only that file's extraction is refreshed and synthesis reruns with the rest from cache.

Max output tokens per file: `DATAFLOW_EXTRACT_TOKENS` (default 400). The compact budget is
intentional — synthesis needs all extractions to fit in a single LLM context window.

#### Pass 2 — Synthesis

All per-file extractions are concatenated and sent to the LLM with `dataflow_synth_prompt.txt`.
The synthesis prompt produces a single structured document with five sections:

1. **Pipeline Overview** — ASCII diagram showing the data path from GPU driver through all
   stages to the TUI

2. **Shared Data Types** — table of every cross-module type: name, defined in, consumers,
   mutable flag

3. **Stage-by-Stage Flow** — table row per pipeline stage: stage name, input type, output
   type, key method, failure mode at this stage

4. **Thread Boundaries** — where thread switches occur, what synchronisation is used, what
   shared state exists

5. **Handoff Points** — the primary debugging guide: for each module boundary, exact method
   call, type in transit, what to inspect (attribute names, field values), and the failure
   mode if this handoff is broken

The synthesis call uses `DATAFLOW_SYNTH_TOKENS` (default 1800) and a timeout of
`LLM_TIMEOUT * 3` (default 360 seconds) because synthesising all modules in one pass is
compute-intensive.

If synthesis fails, the script falls back to writing the raw concatenated per-file extractions
to `DATA_FLOW.md` with an error note, so you still get useful output.

### SHA1 caching detail

Cache key: SHA1 of the source file. Cache file: `architecture/.dataflow_state/extractions/<sha1>.txt`.
The cache is per-file, so changing `collector.py` invalidates only `collector.py`'s
extraction; all other extractions are served from disk.

### CLI reference

```powershell
# Extract all files and synthesise
..\LocalLLM_Pipeline\LocalLLMDebug\dataflow_local.ps1

# Restrict extraction to a subdirectory
..\LocalLLM_Pipeline\LocalLLMDebug\dataflow_local.ps1 -TargetDir src/nmon

# Delete all extractions and output, then re-run
..\LocalLLM_Pipeline\LocalLLMDebug\dataflow_local.ps1 -Clean
```

There is no `-Force` flag. To re-extract a specific file without cleaning everything, delete
its cache entry from `architecture/.dataflow_state/extractions/`. The SHA1 filename maps to
the source file; use `Get-FileHash -Algorithm SHA1` to find the right entry.

### Output structure

```
architecture/
  DATA_FLOW.md                        # full data flow trace (load into Claude Code)
  .dataflow_state/
    extractions/
      <sha1>.txt                      # per-file cached extraction
    last_error.log
```

`DATA_FLOW.md` starts with an HTML comment header recording generation date, codebase
description, file count, and LLM model used.

### When to use

- **Before debugging a wrong metric value** — read `DATA_FLOW.md` Handoff Points section to
  identify which boundary to instrument first
- **When adding a new data source** — regenerate to see how the new source fits into the
  existing pipeline and whether any type contracts need updating
- **When a change in one module breaks another** — the Cross-Module Obligations section shows
  which callers rely on which postconditions
- **Combined with bughunt_iterative** — the data flow analysis dimension in `bughunt_iterative`
  uses the same conceptual focus but operates file-by-file during the fix loop; `dataflow_local`
  produces a holistic cross-module view suitable for loading into Claude Code

---

## 6. testgap_local — Test Gap Analysis

**Script:** `LocalLLMDebug/testgap_local.ps1`
**Prompts:** `testgap_file_prompt.txt`, `testgap_notest_prompt.txt`, `testgap_synth_prompt.txt`
**Output:** `test_gaps/GAP_REPORT.md`

### Purpose

`testgap_local.ps1` audits **what is and isn't tested**. It maps every source file to its
test file, asks the LLM to identify coverage gaps and broken tests, and synthesises the
per-file analyses into a single prioritised gap report ordered by production risk.

### Three-pass design

#### Pass 0 — Static file mapping (no LLM)

Before any LLM call, `testgap_local.ps1` maps every Python source file under `SrcDir` to
its corresponding test file under `TestDir` using the `Find-TestFile` function. This is a
pure filesystem operation — no LLM involved.

`Find-TestFile` tries three candidate naming conventions:

| Candidate | Derivation                                                                                            | Example                                                         |
|-----------|-------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------|
| C1        | `test_` + all sub-path parts joined with `_` + `.py`                                                  | `src/nmon/gpu/nvml_source.py` → `tests/test_gpu_nvml_source.py` |
| C2        | `test_` + stem only                                                                                   | → `tests/test_nvml_source.py`                                   |
| C3        | Strip common suffixes (`_source`, `_base`, `_impl`, `_helper`, `_utils`) from stem, rejoin with parts | `nvml_source` → `nvml` → `tests/test_gpu_nvml.py`               |

The first candidate that exists on disk is used. If none exist, the file is marked `NO TEST FILE`.

Pass 0 prints the full mapping to the console before any LLM work begins, so you can verify
the source→test pairing before committing to the full run:

```
Pass 0: mapping source files to test files...

  src/nmon/collector.py             -> test_collector.py
  src/nmon/storage.py               -> test_storage.py
  src/nmon/gpu/nvml_source.py       -> test_gpu_nvml.py
  src/nmon/tui/app.py               -> [NO TEST FILE]
  ...

  With test file:  12
  Without test:    3
```

#### Pass 1 — Per-file LLM analysis

For each source file, the script sends a prompt to the LLM containing:

- **For files WITH a test file** (`testgap_file_prompt.txt`): source content + test content +
  `conftest.py` content (always included as shared fixture context) + `test_integration.py`
  content as supplementary context for non-integration test files. The prompt asks for six
  sections: Coverage Level, Gap Analysis (with severity), Broken or Misleading Tests, Mock
  and Fixture Quality, Module Invariants Tested, and Missing Edge Cases.

- **For files WITHOUT a test file** (`testgap_notest_prompt.txt`): source content only. The
  prompt asks for a Must-Test priority list and Mock Strategy — what to stub and how.

Cache key: SHA1 of `srcSha + '_' + testSha` (the concatenation of both hashes as a string).
This means the cache entry is invalidated if *either* the source file or the test file
changes. Cache files live in `test_gaps/.testgap_state/cache/<combined_sha1>.txt`.

Max output tokens per file: `TESTGAP_FILE_TOKENS` (default 700).

#### Pass 2 — Synthesis

All per-file analyses are concatenated and sent to the LLM with `testgap_synth_prompt.txt`.
The synthesis output has six sections:

1. **Coverage Summary** — table: file, coverage rating (`GOOD`/`PARTIAL`/`POOR`/`NONE`),
   HIGH/MEDIUM/LOW gap counts

2. **High Priority Gaps** — one block per HIGH gap across all files: gap description, production
   risk if undetected, and a one-sentence description of the specific test to add

3. **Broken or Misleading Tests** — list of every broken test identified: `test_file::test_name`
   and what is wrong

4. **Mock and Fixture Quality Issues** — mock divergences and fixture problems: what real
   behaviour is missing from the mock

5. **Untested Files** — for each file with `NONE` coverage, the top 3 must-test items

6. **Recommended Test Writing Order** — prioritised list of up to 10 items ordered by
   production risk, not alphabetically

Max synthesis tokens: `TESTGAP_SYNTH_TOKENS` (default 1800). Synthesis timeout:
`LLM_TIMEOUT * 3`.

### conftest.py inclusion

`conftest.py` is read once at startup and appended to **every** per-file analysis prompt.
This is important because `conftest.py` defines shared fixtures (`fake_samples_batch`,
`tmp_db_path`, etc.) that tests rely on — without this context the LLM cannot judge whether
a test's fixtures correctly represent the production data shape.

Similarly, `test_integration.py` is appended to every non-integration file's prompt as
supplementary context, since integration tests often exercise paths that unit tests don't.

### Severity model for gaps

| Severity | Meaning in gap analysis                                                               |
|----------|---------------------------------------------------------------------------------------|
| `HIGH`   | Missing test for a path that could cause data loss, crash, or silent incorrect result |
| `MEDIUM` | Missing test for reachable incorrect behaviour (wrong value returned, wrong state)    |
| `LOW`    | Missing test for an edge case unlikely in normal operation                            |

### CLI reference

```powershell
# Analyse all src/ files against tests/
..\LocalLLM_Pipeline\LocalLLMDebug\testgap_local.ps1

# Specify different source and test directories
..\LocalLLM_Pipeline\LocalLLMDebug\testgap_local.ps1 -SrcDir src -TestDir tests

# Delete all output and caches, then re-run
..\LocalLLM_Pipeline\LocalLLMDebug\testgap_local.ps1 -Clean
```

There is no `-Force` or `-TargetDir`. To force re-analysis of a single file, delete its cache
entry from `test_gaps/.testgap_state/cache/`.

### Output structure

```
test_gaps/
  GAP_REPORT.md                       # prioritised combined report (load into Claude Code)
  src/
    nmon/
      collector.py.gap.md             # per-file gap analysis
      storage.py.gap.md
      gpu/
        nvml_source.py.gap.md
  .testgap_state/
    cache/
      <sha1>.txt                      # per-file cached analysis
    last_error.log
```

### When to use

- **Before writing new tests** — read `GAP_REPORT.md` Recommended Test Writing Order to
  prioritise what to write first
- **Code review preparation** — check that any new source code has corresponding test coverage
  that the gap analysis rates GOOD
- **Investigating a production bug** — if a bug slipped through, the Broken or Misleading
  Tests section may show why the test suite didn't catch it
- **Onboarding** — the per-file `.gap.md` files give a concrete description of what each
  module is supposed to do and where its test coverage is thin

---

## 7. interfaces_local — Interface Contract Summary

**Script:** `LocalLLMDebug/interfaces_local.ps1`
**Prompts:** `interfaces_prompt.txt`, `interfaces_synth_prompt.txt`
**Output:** `architecture/INTERFACES.md`

### Purpose

`interfaces_local.ps1` documents the **precise contract of every public class and function**:
what the caller must provide (preconditions), what the function guarantees on return
(postconditions), what exceptions it can raise, where it fails silently, and whether it is
thread-safe. The synthesised output gives Claude Code an exact contract map to consult when
debugging a caller/callee interaction.

### Two-pass design

#### Pass 1 — Per-file contract extraction

Each source file is sent to the LLM with `interfaces_prompt.txt`. The prompt asks for a
contract block per public class and per public function:

```
### `method_name(params) -> ReturnType`
- **Requires:**   preconditions on arguments and object state
- **Guarantees:** what is always true about the return value on success
- **Raises:**     ExceptionType -- specific condition (one per line)
- **Silent failure:** condition where no exception is raised but the result may be wrong
- **Thread safety:** safe | unsafe | requires external lock | internal lock (name it)
```

The prompt specifically instructs the LLM to treat **Silent failure** as the most important
section, and to look for bare `except`, swallowed exceptions, missing field checks, f-string
SQL, and unchecked return values.

Each file's contract extraction is also placed in:
- **Cache:** `architecture/.interfaces_state/cache/<sha1>.txt` (SHA1 of source file)
- **Per-file output:** `architecture/interfaces/<rel>.iface.md`

On cache hit, the per-file `.iface.md` is refreshed from cache if it doesn't already exist.
This means you can load individual module contract files without re-running the full pipeline.

Max output tokens per file: `INTERFACES_EXTRACT_TOKENS` (default 700).

#### Pass 2 — Synthesis

All per-file contracts are concatenated and sent to the LLM with `interfaces_synth_prompt.txt`.
The synthesis produces four sections, ordered by pipeline stage (GPU sources → Collector →
Storage → TUI → Config → Entry point):

1. **Quick Reference table** — one row per public class or top-level function:

   | Module | Class / Function | Thread Safe | Raises | Silent Failures |
   |--------|------------------|-------------|--------|-----------------|

   Thread Safe is `Y`, `N`, or `Partial`. Silent Failures is `Y` or `N`.

2. **Contracts by Module** — each per-file contract verbatim, under a level-3 heading with
   the source path

3. **Cross-Module Obligations** — where one module's postcondition must satisfy another
   module's precondition:
   - **Caller:** `module.method()` — what it must provide
   - **Callee:** `module.method()` — the precondition it relies on
   - **Risk:** what breaks if the obligation is not met

4. **All Silent Failure Modes** — consolidated list of every silent failure across all modules,
   ordered by severity (`[HIGH]`, `[MEDIUM]`, `[LOW]`)

Max synthesis tokens: `INTERFACES_SYNTH_TOKENS` (default 2000). Synthesis timeout:
`LLM_TIMEOUT * 3`.

### Why silent failures receive special attention

Silent failures are the hardest bugs to detect. A function that raises an exception at least
makes the problem visible; a function that returns incorrect data without any signal will
propagate incorrect state silently through the pipeline. The interfaces contract extraction
prompt explicitly calls out:

- `bare except` clauses
- Swallowed exceptions (caught but not re-raised or logged)
- Missing field checks (accessing `.text` on a result that might be `None`)
- F-string SQL construction (SQL injection and silent format errors)
- Unchecked return values from methods that signal failure via return code

The `All Silent Failure Modes` section in `INTERFACES.md` is the single most useful section
for systematic debugging: it lists every place in the codebase where something can go wrong
invisibly.

### CLI reference

```powershell
# Extract contracts for all source files and synthesise
..\LocalLLM_Pipeline\LocalLLMDebug\interfaces_local.ps1

# Restrict to a subdirectory
..\LocalLLM_Pipeline\LocalLLMDebug\interfaces_local.ps1 -TargetDir src/nmon

# Delete all contract docs and caches, then re-run
..\LocalLLM_Pipeline\LocalLLMDebug\interfaces_local.ps1 -Clean
```

### Output structure

```
architecture/
  INTERFACES.md                       # combined contract reference (load into Claude Code)
  interfaces/
    src/
      nmon/
        collector.py.iface.md         # per-file contract
        storage.py.iface.md
        gpu/
          nvml_source.py.iface.md
  .interfaces_state/
    cache/
      <sha1>.txt                      # per-file cached extraction
    last_error.log
```

### When to use

- **Before debugging a caller/callee interaction** — look up the exact preconditions the
  callee expects and verify the caller provides them
- **Investigating a thread-safety bug** — the Quick Reference table's Thread Safe column and
  the Cross-Module Obligations section identify the specific lock requirements
- **After any API change** — regenerate to update the contract map; the diff shows which
  contracts changed and which callers are now violating a precondition
- **Loading into Claude Code** — `INTERFACES.md` is the most compact, dense contract map for
  a debugging session. Combined with `DATA_FLOW.md`, it gives the LLM everything it needs to
  reason about module boundaries without reading all source files

---

## 8. Recommended Debugging Workflow

### Phase 1 — Establish context (run once per codebase state)

```powershell
# 1. Generate architecture docs
python LocalLLMAnalysis/Arch_Analysis_Pipeline.py --skip-lsp

# 2. Generate data flow trace
..\LocalLLM_Pipeline\LocalLLMDebug\dataflow_local.ps1

# 3. Generate interface contracts
..\LocalLLM_Pipeline\LocalLLMDebug\interfaces_local.ps1
```

These three steps produce the background context for all subsequent debugging. They are
incremental (SHA1-cached) so re-running after editing a few files only refreshes the changed
files.

### Phase 2 — Find and triage issues

```powershell
# 4. Run the quick bug scan
..\LocalLLM_Pipeline\LocalLLMDebug\bughunt_local.ps1

# 5. Review the summary
# Read bug_reports/SUMMARY.md — focus on HIGH first
```

At this point you have:
- Architecture docs explaining what each module is supposed to do
- Data flow trace showing where data crosses boundaries
- Interface contracts with all silent failure modes listed
- Bug scan with HIGH/MEDIUM findings ranked by file

### Phase 3 — Targeted gap analysis

```powershell
# 6. Audit test coverage
..\LocalLLM_Pipeline\LocalLLMDebug\testgap_local.ps1

# Read test_gaps/GAP_REPORT.md
# Focus on HIGH Priority Gaps and Broken or Misleading Tests
```

The test gap report shows whether the bug scan findings are detectable by the current test
suite, and which test files are misleading (mock divergence, trivially-passing tests).

### Phase 4 — Iterative automatic fix

```powershell
# 7. Run iterative fix on the highest-priority subsystem
..\LocalLLM_Pipeline\LocalLLMDebug\bughunt_iterative_local.ps1 -TargetDir src/nmon -TestDir tests

# 8. Review the diff for each changed file
# diff src/nmon/collector.py bug_fixes/src/nmon/collector.py

# 9. Apply fixes you are satisfied with
copy bug_fixes\src\nmon\collector.py src\nmon\collector.py

# Or apply everything at once on a dedicated branch:
..\LocalLLM_Pipeline\LocalLLMDebug\bughunt_iterative_local.ps1 -ApplyFixes
```

### Phase 5 — Load context into Claude Code

With all analysis outputs up to date, load the most relevant documents into the Claude Code
session:

```bash
# For a general debugging session
Read architecture/INTERFACES.md
Read architecture/DATA_FLOW.md
Read bug_fixes/SUMMARY.md
Read test_gaps/GAP_REPORT.md

# For a specific file investigation
Read architecture/interfaces/src/nmon/collector.py.iface.md
Read bug_fixes/src/nmon/collector.py.iter_log.md
```

### Focused workflows

**"Something is wrong with the displayed GPU metric"**

1. Read `DATA_FLOW.md` — Handoff Points section to identify which boundary to check
2. Read `INTERFACES.md` — All Silent Failure Modes for the relevant modules
3. Run `bughunt_iterative_local.ps1 -TargetDir src/nmon/gpu -SkipTests`

**"The test suite passes but there is a production bug"**

1. Run `testgap_local.ps1`
2. Read `GAP_REPORT.md` — Broken or Misleading Tests section
3. Run `bughunt_iterative_local.ps1 -SkipBugs -SkipDataflow -SkipContracts` to fix only tests

**"I changed the Collector API"**

1. Re-run `interfaces_local.ps1` to refresh `INTERFACES.md`
2. Read Cross-Module Obligations — find every caller that relied on the old postcondition
3. Run `bughunt_iterative_local.ps1 -SkipTests` to check for new contract violations

**"Starting a new debugging session from scratch"**

Run the pipeline in order:

```powershell
python LocalLLMAnalysis/Arch_Analysis_Pipeline.py --skip-lsp
..\LocalLLM_Pipeline\LocalLLMDebug\dataflow_local.ps1
..\LocalLLM_Pipeline\LocalLLMDebug\interfaces_local.ps1
..\LocalLLM_Pipeline\LocalLLMDebug\bughunt_local.ps1
..\LocalLLM_Pipeline\LocalLLMDebug\testgap_local.ps1
```

Then load `INTERFACES.md`, `DATA_FLOW.md`, `bug_reports/SUMMARY.md`, and
`test_gaps/GAP_REPORT.md` into Claude Code and describe the problem.

---

## 9. Configuration Reference (.env)

All keys are in `LocalLLM_Pipeline/Common/.env`. Keys not present fall back to the default shown.

### Shared settings

| Key                   | Default             | Used by                                                                                                                                                   |
|-----------------------|---------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------|
| `LLM_ENDPOINT`        | (unset)             | All scripts. When set, wins over `LLM_HOST` + `LLM_PORT`.                                                                                                 |
| `LLM_HOST`            | `192.168.1.126`     | All scripts (fallback when `LLM_ENDPOINT` unset)                                                                                                          |
| `LLM_PORT`            | `11434`             | All scripts                                                                                                                                               |
| `LLM_MODEL`           | `qwen3-coder:30b`   | All analysis scripts                                                                                                                                      |
| `LLM_NUM_CTX`         | `32768`             | All scripts via `Invoke-LocalLLM`. When > 0, routes requests through native `/api/chat` with `options.num_ctx`. When 0 or unset, uses legacy OpenAI-compat path. |
| `LLM_MODEL_HIGH_CTX`  | `qwen3-coder:30b`   | Preferred over `LLM_MODEL` by `bughunt_iterative_local.ps1`, `interfaces_local.ps1`, `testgap_local.ps1`. With per-request `num_ctx` the separate model is no longer needed; this key remains for backward compatibility and now typically points at the same tag as `LLM_MODEL`. |
| `LLM_TEMPERATURE`     | `0.1`               | All scripts                                                                                                                                               |
| `LLM_TIMEOUT`         | `120` / `300`       | All scripts (`bughunt_iterative_local.ps1` defaults to 300 for long fix calls)                                                                            |
| `PRESET`              | `''`                | All scripts (selects file patterns)                                                                                                                       |
| `INCLUDE_EXT_REGEX`   | Preset default      | All scripts                                                                                                                                               |
| `EXCLUDE_DIRS_REGEX`  | Preset default      | All scripts                                                                                                                                               |
| `EXTRA_EXCLUDE_REGEX` | `''`                | All scripts                                                                                                                                               |
| `CODEBASE_DESC`       | Preset default      | All scripts (system prompt)                                                                                                                               |
| `DEFAULT_FENCE`       | Preset default      | All scripts (code fence language)                                                                                                                         |
| `MAX_FILE_LINES`      | `800`               | All scripts (source truncation)                                                                                                                           |

### Per-script token budgets

| Key                         | Default | Script                                            |
|-----------------------------|---------|---------------------------------------------------|
| `BUGHUNT_MAX_TOKENS`        | `900`   | `bughunt_local.ps1`                               |
| `BUGHUNT_ANALYSE_TOKENS`    | `900`   | `bughunt_iterative_local.ps1` (per analysis type) |
| `BUGHUNT_FIX_TOKENS`        | `4000`  | `bughunt_iterative_local.ps1` (fix call)          |
| `DATAFLOW_EXTRACT_TOKENS`   | `400`   | `dataflow_local.ps1` (per-file extraction)        |
| `DATAFLOW_SYNTH_TOKENS`     | `1800`  | `dataflow_local.ps1` (synthesis)                  |
| `TESTGAP_FILE_TOKENS`       | `700`   | `testgap_local.ps1` (per-file analysis)           |
| `TESTGAP_SYNTH_TOKENS`      | `1800`  | `testgap_local.ps1` (synthesis)                   |
| `INTERFACES_EXTRACT_TOKENS` | `700`   | `interfaces_local.ps1` (per-file extraction)      |
| `INTERFACES_SYNTH_TOKENS`   | `2000`  | `interfaces_local.ps1` (synthesis)                |

### Convergence guards (bughunt_iterative_local.ps1 only)

| Key                       | Default | Effect                                                                   |
|---------------------------|---------|--------------------------------------------------------------------------|
| `BUGHUNT_BLOAT_RATIO`     | `1.5`   | Reject fix if file grows beyond this ratio vs. original line count       |
| `BUGHUNT_BLOAT_MIN_SLACK` | `15`    | Absolute line floor so tiny files aren't over-constrained (e.g. 17 → 32) |
| `BUGHUNT_DIVERGE_AFTER`   | `2`     | Abort as `DIVERGING` after N consecutive non-improving iterations        |

The effective bloat limit is `max(orig × BUGHUNT_BLOAT_RATIO, orig + BUGHUNT_BLOAT_MIN_SLACK)`
lines. Defaults are tuned against `qwen2.5-coder:32b`, where legitimate fixes hover at 25–35%
growth and hallucinated bulk is 100%+ — `1.5` (50%) sits comfortably between. Tighten to
`1.25` if you're running a weaker model that hallucinates aggressively, or relax to `2.0`
if you're running a stronger reviewer that legitimately restructures more. Set
`BUGHUNT_DIVERGE_AFTER=3` or higher if your model makes slow but real progress across
iterations.

### Subsections block (Arch_Analysis_Pipeline.py only)

```ini
#Subsections begin
src
# any non-comment line becomes a -TargetDir argument
#Subsections end
```

---

## 10. Output File Index

| File                                     | Generated by                  | Purpose                                               |
|------------------------------------------|-------------------------------|-------------------------------------------------------|
| `1. src/`                                | `Arch_Analysis_Pipeline.py`             | Per-file architecture docs + overview + xref + graphs |
| `architecture/DATA_FLOW.md`              | `dataflow_local.ps1`          | Cross-module data flow trace                          |
| `architecture/INTERFACES.md`             | `interfaces_local.ps1`        | Interface contracts + silent failures                 |
| `architecture/interfaces/<rel>.iface.md` | `interfaces_local.ps1`        | Per-file contract                                     |
| `bug_reports/SUMMARY.md`                 | `bughunt_local.ps1`           | Bug scan summary with severity counts                 |
| `bug_reports/<rel>.md`                   | `bughunt_local.ps1`           | Per-file bug report                                   |
| `bug_fixes/SUMMARY.md`                   | `bughunt_iterative_local.ps1` | Source + test fix results tables                      |
| `bug_fixes/<rel>`                        | `bughunt_iterative_local.ps1` | Fixed source file (if changed)                        |
| `bug_fixes/<rel>.iter_log.md`            | `bughunt_iterative_local.ps1` | Per-iteration analysis + fix log                      |
| `test_gaps/GAP_REPORT.md`                | `testgap_local.ps1`           | Prioritised coverage gap report                       |
| `test_gaps/<rel>.gap.md`                 | `testgap_local.ps1`           | Per-file gap analysis                                 |

---

## 11. Reading the Outputs

### Deciding what to load into Claude Code

Not all outputs need to be loaded at once. Use this table to decide:

| Question                                                          | Load                                                                     |
|-------------------------------------------------------------------|--------------------------------------------------------------------------|
| "What does this module do?"                                       | `1. src/<rel>.md`                                                        |
| "Where does data flow through the system?"                        | `architecture/DATA_FLOW.md`                                              |
| "What does this function guarantee / what can go wrong silently?" | `architecture/INTERFACES.md` or `architecture/interfaces/<rel>.iface.md` |
| "What bugs are known?"                                            | `bug_reports/SUMMARY.md` then specific `bug_reports/<rel>.md`            |
| "What did the iterative fixer do to this file?"                   | `bug_fixes/<rel>.iter_log.md`                                            |
| "Is this module well-tested?"                                     | `test_gaps/GAP_REPORT.md`                                                |

### Understanding severity counts in SUMMARY.md files

Both `bug_reports/SUMMARY.md` and `bug_fixes/SUMMARY.md` list severity counts. These counts
are **occurrences of the tag** in LLM output, not deduplicated bugs. A single function with
one race condition may produce one `[HIGH]` tag; three race conditions in one function may
still produce one tag if the LLM describes them together. Use the counts as a triage signal,
not an exact bug count.

### Iteration logs: diagnosing non-CLEAN files

When a file reaches any non-CLEAN status in `bughunt_iterative_local.ps1`, its
`.iter_log.md` is the primary diagnostic. Every iteration block records its own severity,
the best-so-far, and (on non-improvement) the current non-improving streak. The final block
records `Best iteration: N` — that's the version actually written to `bug_fixes/<rel>`.

- **`STUCK` / `[NO CODE BLOCK]`** — the LLM did not produce a code fence. Usually caused by
  the model running out of output tokens before finishing. Try increasing `BUGHUNT_FIX_TOKENS`.

- **`STUCK` / `[NO CHANGE]`** — the LLM reproduced the original file unchanged. The issue
  may require a design change rather than a local fix, or the prompt and model combination
  doesn't understand the required fix. Address manually.

- **`SYNTAX_ERR`** — the LLM produced syntactically invalid Python. The log shows which
  iteration triggered this. The file written is the best previous version. Read the rejected
  code in the iteration log; the LLM may have a good structural fix that only needs a syntax
  tweak.

- **`BLOAT`** — the fix grew the file beyond the bloat limit. Typical signature of a weak
  reviewer that hallucinates large blocks of defensive or duplicated code. Options: swap to a
  stronger model, raise `BUGHUNT_BLOAT_RATIO`, or run `bughunt_local.ps1` (non-modifying) and
  fix manually.

- **`DIVERGING`** — HIGH count failed to improve for `BUGHUNT_DIVERGE_AFTER` consecutive
  iterations. The model is either finding *new* "bugs" each pass or oscillating on the same
  set. The written file is the best iteration (may still be the original). A stronger model
  is the usual remedy; as a shortcut you can also raise `BUGHUNT_DIVERGE_AFTER` to give slow
  models more slack.

- **`MAX_ITER`** — `MaxIterations` reached without converging. With the default of 3 and the
  divergence guard, this is rare — most non-CLEAN runs now terminate as `DIVERGING` first.
  If you see it regularly, raise `-MaxIterations` *and* verify the per-iteration HIGH counts
  are actually decreasing (otherwise the issue is model quality, not budget).

### Cache management

| Cache location                              | Cleared by                | Scope                      |
|---------------------------------------------|---------------------------|----------------------------|
| `bug_reports/.bughunt_state/hashes.tsv`     | `-Clean` or manual delete | Per source file SHA1       |
| `bug_fixes/.bughunt_iter_state/hashes.tsv`  | `-Clean` or `-Force`      | Per source + test SHA1     |
| `architecture/.dataflow_state/extractions/` | `-Clean` or manual delete | Per source file SHA1       |
| `test_gaps/.testgap_state/cache/`           | `-Clean` or manual delete | Per combined src+test SHA1 |
| `architecture/.interfaces_state/cache/`     | `-Clean` or manual delete | Per source file SHA1       |

SHA1 caches are safe to delete partially. Deleting one entry forces re-processing of that
file on the next run without affecting others. The full `-Clean` flag deletes all output and
all caches for that script and starts from zero.
