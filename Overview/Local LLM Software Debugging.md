# Local LLM Software Debugging Pipeline

The `LocalLLMDebug/` directory contains a multi-tool debugging pipeline that uses a local Ollama LLM to analyse a codebase from several angles: finding bugs, tracing data flow, auditing test coverage, mapping interface contracts, and iteratively fixing issues. Each tool produces a focused report. Used together they give a complete picture of where a codebase is broken and why.

All scripts run from the repository root and read `LocalLLMDebug/.env` for LLM server configuration.

---

## How the Pipeline Works

The pipeline is not a single linear sequence -- it is a collection of five independent tools, each targeting a different dimension of code quality. They can be run in any order, but there is a recommended progression that builds context before attempting fixes.

```
Phase 1 — Understand                    Phase 2 — Find           Phase 3 — Audit        Phase 4 — Fix
                                         
dataflow_local       ──┐                bughunt_local            testgap_local           bughunt_iterative_local
  (data flow trace)    │                  (bug scan)               (test gaps)             (analyse + auto-fix)
                       ├─→ context                                  
interfaces_local     ──┘                    
  (interface contracts)                     
```

**Recommended order:** dataflow -> interfaces -> bughunt -> testgap -> bughunt_iterative.

All tools are SHA1-cached -- re-runs only re-process files that have changed since the last run. Use `-Clean` to wipe outputs and caches; `-Force` (where supported) ignores the cache without deleting outputs.

---

## The Tools

### dataflow_local -- Data Flow Trace

**Script:** `dataflow_local.ps1`
**Prompts:** `dataflow_extract_prompt.txt`, `dataflow_synth_prompt.txt`
**Output:** `architecture/DATA_FLOW.md`

Maps how data moves through the codebase. Answers the question: "When a value is wrong on screen, at which stage did the error enter?"

Works in two passes:

1. **Per-file extraction** -- Each source file is sent to the LLM, which extracts a compact description of the file's pipeline interface: what types it defines, what data it produces, what it consumes, threading notes, and error surface. Results are cached by SHA1.

2. **Synthesis** -- All per-file extractions are concatenated and sent to the LLM in a single call. The synthesis produces a structured document with five sections: pipeline overview (ASCII diagram), shared data types, stage-by-stage flow, thread boundaries, and handoff points.

The handoff points section is the primary debugging guide -- for each module boundary it lists the exact method call, the type in transit, what to inspect, and the failure mode if that handoff is broken.

```powershell
.\LocalLLMDebug\dataflow_local.ps1
.\LocalLLMDebug\dataflow_local.ps1 -TargetDir src/nmon
.\LocalLLMDebug\dataflow_local.ps1 -Clean
```

---

### interfaces_local -- Interface Contract Summary

**Script:** `interfaces_local.ps1`
**Prompts:** `interfaces_prompt.txt`, `interfaces_synth_prompt.txt`
**Output:** `architecture/INTERFACES.md` + per-file contracts in `architecture/interfaces/`

Documents the precise contract of every public class and function: what the caller must provide (preconditions), what the function guarantees on return (postconditions), what exceptions it can raise, where it fails silently, and whether it is thread-safe.

Works in two passes:

1. **Per-file extraction** -- Each source file is sent to the LLM, which produces a contract block per public class and function with Requires, Guarantees, Raises, Silent failure, and Thread safety fields.

2. **Synthesis** -- All per-file contracts are combined into a single reference document with four sections: a quick-reference table, contracts by module, cross-module obligations (where one module's postcondition must satisfy another's precondition), and a consolidated list of all silent failure modes ordered by severity.

Silent failures receive special attention because they are the hardest bugs to detect -- a function that returns incorrect data without any signal will propagate wrong state through the pipeline invisibly. The prompt explicitly targets bare `except` clauses, swallowed exceptions, missing field checks, f-string SQL, and unchecked return values.

```powershell
.\LocalLLMDebug\interfaces_local.ps1
.\LocalLLMDebug\interfaces_local.ps1 -TargetDir src/nmon
.\LocalLLMDebug\interfaces_local.ps1 -Clean
```

---

### bughunt_local -- Quick Bug Scan

**Script:** `bughunt_local.ps1`
**Prompt:** `bughunt_prompt.txt`
**Output:** `bug_reports/` + `bug_reports/SUMMARY.md`

Single-pass, non-modifying bug scanner. Sends every source file to the LLM and asks it to identify bugs, then writes a report per file and a combined summary. Nothing is modified.

Each finding is tagged with a severity:

| Tag        | Meaning                                                  |
|------------|----------------------------------------------------------|
| `[HIGH]`   | Data loss, crash, or silent failure in a critical path   |
| `[MEDIUM]` | Incorrect behaviour under a reachable condition          |
| `[LOW]`    | Edge case unlikely in normal operation                   |
| `[INFO]`   | Defensiveness gap -- missing validation, unclear contract |

The summary counts `[HIGH]` and `[MEDIUM]` per file so you can triage by severity.

```powershell
.\LocalLLMDebug\bughunt_local.ps1
.\LocalLLMDebug\bughunt_local.ps1 -TargetDir src/nmon/gpu
.\LocalLLMDebug\bughunt_local.ps1 -Clean
.\LocalLLMDebug\bughunt_local.ps1 -Force
```

---

### testgap_local -- Test Gap Analysis

**Script:** `testgap_local.ps1`
**Prompts:** `testgap_file_prompt.txt`, `testgap_notest_prompt.txt`, `testgap_synth_prompt.txt`
**Output:** `test_gaps/GAP_REPORT.md` + per-file analyses in `test_gaps/`

Audits what is and isn't tested. Maps every source file to its test file, asks the LLM to identify coverage gaps and broken tests, and synthesises everything into a prioritised gap report.

Works in three passes:

1. **Static file mapping (no LLM)** -- Maps each source file to its test file using naming conventions (`test_` prefix, path-based joining, suffix stripping). Files with no match are flagged immediately.

2. **Per-file analysis** -- For files with a test, both source and test content (plus `conftest.py` for shared fixtures) are sent to the LLM. For files without a test, only the source is sent with a prompt asking for a must-test priority list.

3. **Synthesis** -- All per-file analyses are combined into a gap report with six sections: coverage summary table, high priority gaps, broken or misleading tests, mock and fixture quality issues, untested files, and a recommended test writing order ranked by production risk.

The cache key combines the SHA1 of both the source and test file, so the cache is invalidated if either file changes.

```powershell
.\LocalLLMDebug\testgap_local.ps1
.\LocalLLMDebug\testgap_local.ps1 -SrcDir src -TestDir tests
.\LocalLLMDebug\testgap_local.ps1 -Clean
```

---

### bughunt_iterative_local -- Iterative Analyse and Fix

**Script:** `bughunt_iterative_local.ps1`
**Prompts:** `bughunt_prompt.txt`, `bughunt_dataflow_prompt.txt`, `bughunt_contracts_prompt.txt`, `bughunt_tests_prompt.txt`, `bughunt_fix_prompt.txt`
**Output:** `bug_fixes/` + per-file iteration logs + `bug_fixes/SUMMARY.md`

The most powerful tool in the pipeline. For each source file it iteratively analyses and fixes until no HIGH or MEDIUM issues remain, a stop condition is hit, or the iteration limit is reached. Fixed files are staged in `bug_fixes/` (optionally written back to source with `-ApplyFixes`).

#### Four analysis dimensions

Each iteration runs up to four independent LLM analysis calls against the current working copy of a file:

| Dimension    | Prompt                          | Focus                                                           | Disable with     |
|--------------|---------------------------------|-----------------------------------------------------------------|------------------|
| Bugs         | `bughunt_prompt.txt`            | Crashes, data loss, logic errors, resource leaks                | `-SkipBugs`      |
| Data flow    | `bughunt_dataflow_prompt.txt`   | Type mismatches at boundaries, missing validation, stale data   | `-SkipDataflow`  |
| Contracts    | `bughunt_contracts_prompt.txt`  | Broken preconditions, resource leaks, invariant violations      | `-SkipContracts` |
| Tests        | `bughunt_tests_prompt.txt`      | Trivially-passing tests, mock divergence, weak assertions       | `-SkipTests`     |

The first three target source files. The fourth targets test files in a separate loop.

#### Iteration mechanics

For each file, the script runs a loop of up to `MaxIterations` (default 3) cycles:

1. Run all enabled analysis types as independent LLM calls
2. Combine the reports and count `[HIGH]` / `[MEDIUM]` findings
3. Track the best version seen (lowest HIGH, tie-broken by lowest MEDIUM)
4. If clean (no HIGH or MEDIUM) -- stop
5. If diverging (HIGH not improving for N consecutive iterations) -- stop and revert to best
6. If last iteration -- stop and write best version
7. Send a fix call with the combined report and current file content
8. Validate the fix (extract code block, check for no-op, bloat guard, syntax check)
9. Accept the fix and loop

#### Convergence guards

Local LLMs tend to diverge on iterative review -- finding fresh "bugs" each pass, rewriting code aggressively, or ballooning file size. The script has built-in guards:

| Guard      | What it does                                                                    | Env key                   | Default |
|------------|---------------------------------------------------------------------------------|---------------------------|---------|
| Best-track | Reverts to the best version seen on any non-CLEAN exit                          | --                        | --      |
| Diverging  | Aborts after N consecutive iterations without improvement                       | `BUGHUNT_DIVERGE_AFTER`   | `2`     |
| Bloat      | Rejects fixes that grow the file beyond a ratio of the original                 | `BUGHUNT_BLOAT_RATIO`     | `1.5`   |
| Syntax     | Rejects fixes that fail `python -m py_compile` (Python files only)              | --                        | --      |

These guards mean a bad model run can never leave a file worse than it started.

#### Stop conditions

| Status       | Meaning                                            | What gets written  |
|--------------|----------------------------------------------------|--------------------|
| `CLEAN`      | No HIGH or MEDIUM findings                         | Final iteration    |
| `MAX_ITER`   | Reached iteration limit, bugs remain               | Best iteration     |
| `DIVERGING`  | HIGH stopped improving                             | Best iteration     |
| `BLOAT`      | Fix grew the file too much                         | Best iteration     |
| `STUCK`      | LLM returned no code block or identical code       | Best iteration     |
| `SYNTAX_ERR` | Fixed code failed compilation                      | Best iteration     |
| `ERROR`      | All analysis types failed (network, timeout, etc.) | Best iteration     |

```powershell
.\LocalLLMDebug\bughunt_iterative_local.ps1
.\LocalLLMDebug\bughunt_iterative_local.ps1 -TargetDir src/nmon -SkipTests
.\LocalLLMDebug\bughunt_iterative_local.ps1 -ApplyFixes
.\LocalLLMDebug\bughunt_iterative_local.ps1 -MaxIterations 6
.\LocalLLMDebug\bughunt_iterative_local.ps1 -Clean
```

---

## Shared Infrastructure

**Script:** `llm_common.ps1`

Dot-sourced by every `*_local.ps1` script. Provides the LLM client (`Invoke-LocalLLM` with retry logic), `.env` parsing, SHA1 hashing, preset definitions, file-extension filtering, source truncation, and progress display. This is a copy of the same module used by the Architecture Analysis pipeline.

---

## Configuration

All configuration lives in `LocalLLMDebug/.env`. Key settings:

| Setting               | Purpose                                                                |
|-----------------------|------------------------------------------------------------------------|
| `LLM_HOST` / `PORT`   | Ollama server address                                                  |
| `LLM_MODEL`           | Model for most scripts (e.g. `qwen2.5-coder:32b-8k`)                  |
| `LLM_MODEL_HIGH_CTX`  | Model for the three heavy scripts that need more context (e.g. `32b-12k`) |
| `LLM_TIMEOUT`         | Per-request timeout in seconds                                         |
| `PRESET`              | File pattern preset (e.g. `python`, `cnc`)                             |
| `INCLUDE_EXT_REGEX`   | Override file extensions to include                                    |
| `EXCLUDE_DIRS_REGEX`  | Override directories to exclude                                        |
| `MAX_FILE_LINES`      | Source truncation limit (default 800)                                  |

Three scripts (`bughunt_iterative_local.ps1`, `interfaces_local.ps1`, `testgap_local.ps1`) automatically prefer `LLM_MODEL_HIGH_CTX` when set, because their synthesis passes read 10k+ tokens of input. All other scripts use `LLM_MODEL`. If `LLM_MODEL_HIGH_CTX` is unset, all scripts fall back to `LLM_MODEL`.

---

## Model Selection

The default `num_ctx` for `qwen2.5-coder:32b` is 32768, which adds ~8 GB of KV cache -- enough to overflow a 24 GB GPU and force partial CPU offload (~4x slower, causing timeouts). Create reduced-context variants:

| Variant                  | KV cache | Total VRAM | Use for                              |
|--------------------------|----------|------------|--------------------------------------|
| `qwen2.5-coder:32b-8k`  | ~2 GB    | ~23 GB     | Analysis-only scripts                |
| `qwen2.5-coder:32b-12k` | ~3 GB    | ~24 GB     | Fix calls and synthesis passes       |
| `qwen2.5-coder:14b`     | default  | ~9 GB      | Smaller GPUs, no custom variant needed |

---

## Output Structure

```
bug_reports/
  SUMMARY.md                          Severity counts per file
  src/nmon/collector.py.md            Per-file bug report

bug_fixes/
  SUMMARY.md                          Stop status per file
  src/nmon/collector.py               Fixed source file
  src/nmon/collector.py.iter_log.md   Per-iteration analysis and fix log

architecture/
  DATA_FLOW.md                        Cross-module data flow trace
  INTERFACES.md                       Combined interface contract reference
  interfaces/
    src/nmon/collector.py.iface.md    Per-file contract

test_gaps/
  GAP_REPORT.md                       Prioritised gap report
  src/nmon/collector.py.gap.md        Per-file gap analysis
```

---

## Recommended Workflows

**Full cold start**
```powershell
.\LocalLLMDebug\dataflow_local.ps1
.\LocalLLMDebug\interfaces_local.ps1
.\LocalLLMDebug\bughunt_local.ps1
.\LocalLLMDebug\testgap_local.ps1
```

**Wrong value on screen**
```powershell
.\LocalLLMDebug\bughunt_iterative_local.ps1 -TargetDir src/nmon/gpu -SkipTests
```
Then read `DATA_FLOW.md` (Handoff Points) and `INTERFACES.md` (Silent Failure Modes).

**Tests pass but production is broken**
```powershell
.\LocalLLMDebug\testgap_local.ps1
.\LocalLLMDebug\bughunt_iterative_local.ps1 -SkipBugs -SkipDataflow -SkipContracts
```

**Auto-fix a subsystem**
```powershell
.\LocalLLMDebug\bughunt_iterative_local.ps1 -TargetDir src/nmon -ApplyFixes
```
