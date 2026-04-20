# bughunt_local.ps1

## Purpose

`bughunt_local.ps1` is a single-pass bug hunting script that runs an LLM-powered analysis on every source file in a codebase. Unlike the iterative variant, this script does not attempt to fix bugs -- it only reports them. Each file is sent to a local LLM (via Ollama) with a structured prompt that asks the model to identify real bugs (crashes, data loss, logic errors, security issues) and classify them by severity (HIGH, MEDIUM, LOW). The output is a collection of per-file Markdown bug reports plus a summary document.

The script uses SHA1-based caching so that only changed files are re-analysed on subsequent runs. If an `xref_index.md` file exists from a prior Analysis pipeline run, it is injected into every prompt to give the LLM cross-file context for spotting integration bugs that a single-file view would miss.

## Prerequisites

- **PowerShell 5.1+** (Windows PowerShell or PowerShell Core)
- **Ollama** running locally with a code-capable model loaded
- **`llm_common.ps1`** -- shared module at `Common/llm_common.ps1`
- **`.env` file** -- at `Common/.env` (or custom path via `-EnvFile`)
- **Prompt file:** `LocalLLMDebug/bughunt_prompt.txt` (required)
- No prior pipeline steps are required, though the script optionally benefits from a prior Analysis pipeline run (for `xref_index.md`).

## Usage

```powershell
.\LocalLLMDebug\bughunt_local.ps1 [options]
```

### CLI Options

| Parameter    | Type   | Default       | Description                                                                                   |
| ------------ | ------ | ------------- | --------------------------------------------------------------------------------------------- |
| `-TargetDir` | string | `"."`         | Subdirectory of the repo to scan (relative to repo root). Use `"."` for the entire repo.      |
| `-Clean`     | switch | off           | Delete the entire `bug_reports/` directory and state before running.                          |
| `-Force`     | switch | off           | Ignore the SHA1-based cache and re-analyse all files regardless of whether they have changed. |
| `-EnvFile`   | string | `Common/.env` | Path to the `.env` configuration file.                                                        |

## How It Is Invoked

### Standalone

```powershell
cd C:\Projects\MyApp
.\LocalLLMDebug\bughunt_local.ps1 -TargetDir src/nmon
```

### Via ArchPipeline.py (current)

`ArchPipeline.py debug` mode calls `bughunt_local.ps1` as **step 4** of its 6-step sequence. The orchestrator lives at `Common/_pipeline/modes/debug/cli.py`; it invokes this script as a subprocess via `subprocess_runner.powershell_cmd()`, forwarding `--target-dir` → `-TargetDir` and resolving `-EnvFile` from `Common/.env` automatically.

```
Step 1: dataflow_local.ps1      (data-flow analysis)
Step 2: interfaces_local.ps1    (interface extraction)
Step 3: testgap_local.ps1       (test-gap analysis)
Step 4: bughunt_local.ps1       <-- this script
Step 5: fix_bugs.py (inline)    (per-file LLM-driven bug fixing)
Step 6: Archive                 (writes Implemented Plans/Bug Fix Changes N.md)
```

The legacy `Arch_Debug_Pipeline.ps1` has been retired; it still exists in the `legacy/` directory for reference only.

## Input Files

| Input                                     | Description                                                                                                                                                                                           |
| ----------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Source files matching `INCLUDE_EXT_REGEX` | All files under `TargetDir` that match the inclusion regex and do not match exclusion regexes. Directories `architecture/` and `bug_reports/` are excluded.                                           |
| `Common/.env`                             | Configuration for LLM endpoint, model, temperature, and token limits.                                                                                                                                 |
| `bughunt_prompt.txt`                      | Structured prompt schema sent to the LLM with each file.                                                                                                                                              |
| `architecture/xref_index.md` (optional)   | Cross-reference index from a prior Analysis pipeline run. When present, injected into every prompt for integration-bug context. Resolved via `Resolve-ArchFile` which also checks `ARCHITECTURE_DIR`. |

## Output Files and Directories

| Output                                      | Description                                                                                                      |
| ------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `bug_reports/<rel>.md`                      | Per-file bug report in Markdown. Contains severity-tagged findings ([HIGH], [MEDIUM], [LOW]) or a CLEAN verdict. |
| `bug_reports/SUMMARY.md`                    | Aggregated summary listing files with HIGH/MEDIUM findings and a totals table.                                   |
| `bug_reports/.bughunt_state/hashes.tsv`     | SHA1 cache: `<sha>\t<rel>` per line. Used to skip unchanged files on re-runs.                                    |
| `bug_reports/.bughunt_state/last_error.log` | Timestamped log of LLM call failures.                                                                            |

## Environment Variables / .env Keys

| Key                                     | Default             | Description                                                                                                  |
| --------------------------------------- | ------------------- | ------------------------------------------------------------------------------------------------------------ |
| `PRESET`                                | `""`                | Named preset for include/exclude patterns and codebase description.                                          |
| `INCLUDE_EXT_REGEX`                     | from preset         | Regex matching file extensions to include.                                                                   |
| `EXCLUDE_DIRS_REGEX`                    | from preset         | Regex matching directory paths to exclude.                                                                   |
| `EXTRA_EXCLUDE_REGEX`                   | `""`                | Additional exclusion regex.                                                                                  |
| `CODEBASE_DESC`                         | from preset         | Human-readable codebase description for the LLM system prompt.                                               |
| `DEFAULT_FENCE`                         | from preset         | Code fence language identifier (e.g. `python`).                                                              |
| `MAX_FILE_LINES`                        | `800`               | Files longer than this are truncated before sending to the LLM.                                              |
| `LLM_ENDPOINT` or `LLM_HOST`+`LLM_PORT` | —                   | Ollama API endpoint.                                                                                         |
| `LLM_MODEL`                             | `qwen2.5-coder:14b` | Model name to use for analysis.                                                                              |
| `LLM_TEMPERATURE`                       | `0.1`               | LLM sampling temperature.                                                                                    |
| `LLM_TIMEOUT`                           | `120`               | Per-request timeout in seconds.                                                                              |
| `BUGHUNT_MAX_TOKENS`                    | `900`               | Max output tokens per analysis call. Set higher than architecture docs because bug reports need more detail. |
| `ARCHITECTURE_DIR`                      | —                   | Optional override for the directory containing Analysis pipeline output (used to find `xref_index.md`).      |

## Exit Codes

| Code   | Meaning                                                                            |
| ------ | ---------------------------------------------------------------------------------- |
| `0`    | Success. All files analysed (or nothing to do because all reports are up to date). |
| `1`    | Target directory not found, or no matching source files found.                     |
| `2`    | Required prompt file (`bughunt_prompt.txt`) is missing.                            |

The script does not exit non-zero on individual LLM failures. It logs them to `last_error.log` and continues processing remaining files. The final console output reports the number of failures.

## Examples

### Example 1: Scan the entire repo

```powershell
cd C:\Projects\MyApp
.\LocalLLMDebug\bughunt_local.ps1
```

Analyses all files matching the configured include pattern across the repo root. Reports go to `bug_reports/`.

### Example 2: Scan a subdirectory with a clean start

```powershell
.\LocalLLMDebug\bughunt_local.ps1 -TargetDir src/nmon/gpu -Clean
```

Wipes any previous `bug_reports/` directory and re-analyses all files under `src/nmon/gpu/` from scratch.

### Example 3: Force re-analysis of all files

```powershell
.\LocalLLMDebug\bughunt_local.ps1 -Force
```

Ignores the SHA1 cache and re-analyses every file, even those that have not changed since the last run. Useful after changing the LLM model or prompt.

### Example 4: Use a custom .env file

```powershell
.\LocalLLMDebug\bughunt_local.ps1 -TargetDir src -EnvFile C:\configs\my_project.env
```

Uses a project-specific configuration file instead of the default `Common/.env`.
