# bughunt_iterative_local.ps1

## Purpose

`bughunt_iterative_local.ps1` is the most advanced debug script in the LocalLLM_Pipeline toolkit. It performs iterative, multi-pass bug hunting and auto-fixing on every source file in a codebase using a local LLM (via Ollama). For each source file, it runs up to three independent analysis passes -- general bug detection, data flow analysis, and interface contract violation detection -- then feeds the combined findings into a single fix call. The analyse-fix cycle repeats up to `MaxIterations` times per file, stopping early when the file is clean, the LLM gets stuck, the fix diverges, or the fix bloats the file beyond a configurable ratio.

In addition to source files, the script discovers matching test files and runs a separate iterative loop targeting test quality bugs (trivially-passing tests, mock divergence, weak assertions, missing high-risk coverage). Both source and test loops track a "best version seen" and automatically revert to it when the LLM fails to converge, ensuring the output is always the least-buggy version observed across all iterations.

## Prerequisites

- **PowerShell 5.1+** (Windows PowerShell or PowerShell Core)
- **Ollama** running locally with a code-capable model loaded (e.g. `qwen2.5-coder:14b` or `qwen2.5-coder:32b`)
- **Python** on PATH (required only for `.py` files -- used for `py_compile` syntax checking of fixes)
- **`llm_common.ps1`** -- shared module at `Common/llm_common.ps1` (loaded via dot-sourcing)
- **`.env` file** -- at `Common/.env` (or custom path via `-EnvFile`)
- **Prompt files** in the `LocalLLMDebug/` directory:
  - `bughunt_prompt.txt` (required always)
  - `bughunt_fix_prompt.txt` (required always)
  - `bughunt_dataflow_prompt.txt` (required unless `-SkipDataflow`)
  - `bughunt_contracts_prompt.txt` (required unless `-SkipContracts`)
  - `bughunt_tests_prompt.txt` (required unless `-SkipTests`)
- No prior pipeline steps are required -- the script reads source files directly.

## Usage

```powershell
.\LocalLLMDebug\bughunt_iterative_local.ps1 [options]
```

### CLI Options

| Parameter        | Type   | Default       | Description                                                                                                                                                   |
| ---------------- | ------ | ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `-TargetDir`     | string | `"."`         | Subdirectory of the repo to scan for source files (relative to repo root). Use `"."` for the entire repo.                                                     |
| `-TestDir`       | string | `"tests"`     | Directory containing test files (relative to repo root). Used for test file discovery and the test quality loop.                                              |
| `-MaxIterations` | int    | `3`           | Maximum number of analyse-fix iterations per file. Higher values give the LLM more chances to converge but cost more time.                                    |
| `-ApplyFixes`    | switch | off           | When set, writes the best fixed version back to the original source file in addition to staging it in `bug_fixes/`. Without this flag, fixes are staged only. |
| `-SkipBugs`      | switch | off           | Disable the general bug detection analysis pass.                                                                                                              |
| `-SkipDataflow`  | switch | off           | Disable the data flow analysis pass.                                                                                                                          |
| `-SkipContracts` | switch | off           | Disable the interface contract violation analysis pass.                                                                                                       |
| `-SkipTests`     | switch | off           | Disable the test quality analysis loop entirely.                                                                                                              |
| `-Clean`         | switch | off           | Delete the entire `bug_fixes/` output directory and state before running.                                                                                     |
| `-Force`         | switch | off           | Ignore the hash-based skip cache and re-process all files regardless of whether they have changed.                                                            |
| `-EnvFile`       | string | `Common/.env` | Path to the `.env` configuration file.                                                                                                                        |

**Guard:** At least one analysis type must remain active. If all four skip flags are set, the script exits with code 1.

## How It Is Invoked

### Standalone

```powershell
# From the target project's root directory:
.\LocalLLMDebug\bughunt_iterative_local.ps1 -TargetDir src/nmon -TestDir tests
```

### Via ArchPipeline.py (current)

`ArchPipeline.py debug` mode does NOT call `bughunt_iterative_local.ps1` automatically. The six-step debug pipeline (`_pipeline/modes/debug/cli.py`) runs `bughunt_local.ps1` at step 4 for reporting only, then performs its own per-file LLM-driven bug fix at step 5 (`_pipeline/modes/debug/fix_bugs.py`). This iterative script is therefore a **standalone tool** — run it directly from PowerShell when you want the LLM to attempt automatic fixes rather than rely on the pipeline's step-5 approach.

## Input Files

| Input                                     | Description                                                                                                                                                                                                            |
| ----------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Source files matching `INCLUDE_EXT_REGEX` | All files under `TargetDir` that match the configured include pattern and do not match exclude patterns. Directories `architecture/`, `bug_reports/`, `bug_fixes/`, and the test directory are automatically excluded. |
| Test files in `TestDir`                   | Discovered via naming conventions: `test_<path_parts>.py`, `test_<stem>.py`, or `test_<stem_without_suffix>.py`.                                                                                                       |
| `Common/.env`                             | Configuration file for LLM endpoint, model, temperature, token limits, and convergence parameters.                                                                                                                     |
| `bughunt_prompt.txt`                      | Prompt schema for general bug analysis.                                                                                                                                                                                |
| `bughunt_fix_prompt.txt`                  | Prompt schema for the fix call.                                                                                                                                                                                        |
| `bughunt_dataflow_prompt.txt`             | Prompt schema for data flow analysis.                                                                                                                                                                                  |
| `bughunt_contracts_prompt.txt`            | Prompt schema for contract violation analysis.                                                                                                                                                                         |
| `bughunt_tests_prompt.txt`                | Prompt schema for test quality analysis.                                                                                                                                                                               |

## Output Files and Directories

| Output                                         | Description                                                                                                                                         |
| ---------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `bug_fixes/<rel>`                              | Fixed source file (only written if the fix changed the file). Contains the best version seen across all iterations.                                 |
| `bug_fixes/<rel>.iter_log.md`                  | Per-iteration analysis and fix log for the source file. Contains severity counts, combined reports, fix status, and revert decisions per iteration. |
| `bug_fixes/<testRel>`                          | Fixed test file (only written if the test fix changed the file).                                                                                    |
| `bug_fixes/<testRel>.iter_log.md`              | Per-iteration log for the test file.                                                                                                                |
| `bug_fixes/SUMMARY.md`                         | Combined summary table with per-file results, severity totals, and manual review notes.                                                             |
| `bug_fixes/.bughunt_iter_state/hashes.tsv`     | SHA1-based cache. Source entries: `<sha>\t<rel>`. Test entries: `<sha>\ttest:<testRel>`. Used to skip unchanged files on re-runs.                   |
| `bug_fixes/.bughunt_iter_state/last_error.log` | Timestamped log of LLM call failures.                                                                                                               |

## Environment Variables / .env Keys

| Key                                     | Default                 | Description                                                                                                 |
| --------------------------------------- | ----------------------- | ----------------------------------------------------------------------------------------------------------- |
| `PRESET`                                | `""`                    | Named preset for include/exclude patterns and codebase description.                                         |
| `INCLUDE_EXT_REGEX`                     | from preset             | Regex matching file extensions to include.                                                                  |
| `EXCLUDE_DIRS_REGEX`                    | from preset             | Regex matching directory paths to exclude.                                                                  |
| `EXTRA_EXCLUDE_REGEX`                   | `""`                    | Additional exclusion regex.                                                                                 |
| `CODEBASE_DESC`                         | from preset             | Human-readable codebase description injected into LLM system prompts.                                       |
| `DEFAULT_FENCE`                         | from preset             | Default code fence language for LLM prompts (e.g. `python`, `c`).                                           |
| `MAX_FILE_LINES`                        | `800`                   | Maximum lines to send to the LLM per file (longer files are truncated).                                     |
| `LLM_ENDPOINT` or `LLM_HOST`+`LLM_PORT` | —                       | Ollama API endpoint. Resolved by `Get-LLMEndpoint` in `llm_common.ps1`.                                     |
| `LLM_MODEL`                             | blank (→ `LLM_DEFAULT_MODEL` → `qwen3-coder:30b`) | Model name. Resolved by `Get-LLMModel -RoleKey 'LLM_MODEL'`. Per-request `num_ctx` covers the 12k fix-call window. |
| `LLM_DEFAULT_MODEL`                     | `qwen3-coder:30b`       | Universal fallback used when `LLM_MODEL` is blank/unset.                                                    |
| `LLM_TEMPERATURE`                       | `0.1`                   | LLM sampling temperature.                                                                                   |
| `LLM_TIMEOUT`                           | `300`                   | Per-request timeout in seconds. Set high because 4000-token fix calls on a 32B model regularly exceed 120s. |
| `BUGHUNT_ANALYSE_TOKENS`                | `900`                   | Max output tokens for each analysis call.                                                                   |
| `BUGHUNT_FIX_TOKENS`                    | `4000`                  | Max output tokens for each fix call.                                                                        |
| `BUGHUNT_BLOAT_RATIO`                   | `1.5`                   | Maximum allowed file growth ratio vs. original. Fixes exceeding this are rejected.                          |
| `BUGHUNT_BLOAT_MIN_SLACK`               | `15`                    | Absolute minimum line growth allowance so tiny files are not over-constrained.                              |
| `BUGHUNT_DIVERGE_AFTER`                 | `2`                     | Number of consecutive non-improving iterations before aborting and reverting to best.                       |

## Stop Conditions and Exit Codes

### Per-File Stop Conditions

| Status       | Meaning                                                    | Revert Behavior               |
| ------------ | ---------------------------------------------------------- | ----------------------------- |
| `CLEAN`      | No HIGH or MEDIUM findings remain.                         | Keeps current version.        |
| `MAX_ITER`   | Reached `MaxIterations` without becoming clean.            | Reverts to best version seen. |
| `STUCK`      | LLM returned no code block or identical source.            | Reverts to best version seen. |
| `SYNTAX_ERR` | Fixed Python code failed `py_compile` syntax check.        | Reverts to best version seen. |
| `ERROR`      | All analysis types failed (LLM errors) or fix call failed. | Reverts to best version seen. |
| `DIVERGING`  | HIGH count did not improve for N consecutive iterations.   | Reverts to best version seen. |
| `BLOAT`      | Fix grew the file beyond the allowed bloat ratio.          | Reverts to best version seen. |

### Script Exit Codes

| Code   | Meaning                                                                       |
| ------ | ----------------------------------------------------------------------------- |
| `0`    | Success (or nothing to do).                                                   |
| `1`    | All analysis types skipped, target directory not found, or no matching files. |
| `2`    | Required prompt file is missing.                                              |

## Examples

### Example 1: Basic iterative bug hunt with defaults

```powershell
cd C:\Projects\MyApp
.\LocalLLMDebug\bughunt_iterative_local.ps1 -TargetDir src
```

Scans all source files under `src/`, runs all four analysis types (bugs, dataflow, contracts, tests) for up to 3 iterations each. Fixed files are staged in `bug_fixes/` but not written back to source.

### Example 2: Apply fixes with more iterations, skip test analysis

```powershell
.\LocalLLMDebug\bughunt_iterative_local.ps1 `
    -TargetDir src/core `
    -MaxIterations 5 `
    -ApplyFixes `
    -SkipTests
```

Runs up to 5 iterations of bug/dataflow/contract analysis on files under `src/core/`. Skips test quality analysis. Writes the best fixed version back to the original source files.

### Example 3: Clean start, only bug detection, force re-process

```powershell
.\LocalLLMDebug\bughunt_iterative_local.ps1 `
    -TargetDir src/nmon `
    -Clean -Force `
    -SkipDataflow -SkipContracts -SkipTests
```

Wipes previous state, ignores the hash cache, and runs only the general bug detection pass on all files under `src/nmon/`.
