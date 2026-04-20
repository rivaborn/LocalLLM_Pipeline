# dataflow_local.ps1

## Purpose

`dataflow_local.ps1` generates a debugging-focused data flow trace document for a codebase using a local LLM (via Ollama). It operates as a two-pass pipeline. In Pass 1, each source file is individually sent to the LLM, which extracts the file's "pipeline interface" -- the types it defines, what data it produces and consumes, threading notes, and its error surface. In Pass 2, all per-file extractions are combined into a single synthesis prompt, and the LLM produces an end-to-end data flow trace document with handoff points between modules.

The resulting `DATA_FLOW.md` document is designed to be consumed by developers or AI coding agents when debugging cross-module issues, as it maps exactly how data moves through the system, where type transformations happen, and where error propagation can break.

## Prerequisites

- **PowerShell 5.1+** (Windows PowerShell or PowerShell Core)
- **Ollama** running locally with a code-capable model loaded
- **`llm_common.ps1`** -- shared module at `Common/llm_common.ps1`
- **`.env` file** -- at `Common/.env` (or custom path via `-EnvFile`)
- **Prompt files** in the `LocalLLMDebug/` directory:
  - `dataflow_extract_prompt.txt` (per-file extraction schema)
  - `dataflow_synth_prompt.txt` (synthesis schema)
- No prior pipeline steps are strictly required. However, if an Analysis pipeline run has produced `architecture.md`, the synthesis pass will use it as a subsystem scaffold for a more coherent output.

## Usage

```powershell
.\LocalLLMDebug\dataflow_local.ps1 [options]
```

### CLI Options

| Parameter    | Type   | Default       | Description                                                                              |
| ------------ | ------ | ------------- | ---------------------------------------------------------------------------------------- |
| `-TargetDir` | string | `"."`         | Subdirectory of the repo to scan (relative to repo root). Use `"."` for the entire repo. |
| `-Clean`     | switch | off           | Delete the extraction cache, intermediate state, and output file before running.         |
| `-EnvFile`   | string | `Common/.env` | Path to the `.env` configuration file.                                                   |

## How It Is Invoked

### Standalone

```powershell
cd C:\Projects\MyApp
.\LocalLLMDebug\dataflow_local.ps1 -TargetDir src/nmon
```

### Via ArchPipeline.py (current)

`ArchPipeline.py debug` mode calls `dataflow_local.ps1` as **step 1** of its 6-step sequence. The orchestrator at `Common/_pipeline/modes/debug/cli.py` invokes it as a subprocess via `subprocess_runner.powershell_cmd()`, forwarding `--target-dir` → `-TargetDir` and resolving `-EnvFile` from `Common/.env` automatically.

```
Step 1: dataflow_local.ps1      <-- this script
Step 2: interfaces_local.ps1
Step 3: testgap_local.ps1
Step 4: bughunt_local.ps1
Step 5: fix_bugs.py (inline)
Step 6: Archive
```

The legacy `Arch_Debug_Pipeline.ps1` has been retired; it remains in `legacy/` for reference only.

## Input Files

| Input                                     | Description                                                                                                                                                                                                                                  |
| ----------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Source files matching `INCLUDE_EXT_REGEX` | All files under `TargetDir` that match the inclusion regex and do not match exclusion regexes. Directories `architecture/`, `bug_reports/`, and `bug_fixes/` are excluded.                                                                   |
| `Common/.env`                             | Configuration for LLM endpoint, model, temperature, and token limits.                                                                                                                                                                        |
| `dataflow_extract_prompt.txt`             | Structured prompt for Pass 1 (per-file interface extraction).                                                                                                                                                                                |
| `dataflow_synth_prompt.txt`               | Structured prompt for Pass 2 (synthesis into a single document).                                                                                                                                                                             |
| `architecture/architecture.md` (optional) | If present from a prior Analysis pipeline run, injected into the synthesis prompt as a subsystem scaffold. The LLM anchors the data flow trace to known subsystem boundaries rather than re-inventing them. Resolved via `Resolve-ArchFile`. |

## Output Files and Directories

| Output                                               | Description                                                                                                                                   |
| ---------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `architecture/DATA_FLOW.md`                          | The final synthesised data flow trace document. Includes an HTML comment header with generation metadata (date, codebase, file count, model). |
| `architecture/.dataflow_state/extractions/<sha>.txt` | Cached per-file extraction results, keyed by SHA1 of the source file. Re-running after editing one file only re-extracts that file.           |
| `architecture/.dataflow_state/last_error.log`        | Timestamped log of extraction and synthesis failures.                                                                                         |

### Fallback Behavior

If the synthesis call fails, the script writes a fallback document containing all raw per-file extractions concatenated together, prefixed with the error message. This ensures partial results are never lost. The script exits with code 1 in this case.

## Environment Variables / .env Keys

| Key                                     | Default             | Description                                                                                            |
| --------------------------------------- | ------------------- | ------------------------------------------------------------------------------------------------------ |
| `PRESET`                                | `""`                | Named preset for include/exclude patterns and codebase description.                                    |
| `INCLUDE_EXT_REGEX`                     | from preset         | Regex matching file extensions to include.                                                             |
| `EXCLUDE_DIRS_REGEX`                    | from preset         | Regex matching directory paths to exclude.                                                             |
| `EXTRA_EXCLUDE_REGEX`                   | `""`                | Additional exclusion regex.                                                                            |
| `CODEBASE_DESC`                         | from preset         | Human-readable codebase description for LLM system prompts.                                            |
| `DEFAULT_FENCE`                         | from preset         | Code fence language identifier.                                                                        |
| `MAX_FILE_LINES`                        | `800`               | Files longer than this are truncated before sending to the LLM.                                        |
| `LLM_ENDPOINT` or `LLM_HOST`+`LLM_PORT` | —                   | Ollama API endpoint.                                                                                   |
| `LLM_MODEL`                             | `qwen2.5-coder:14b` | Model name for both extraction and synthesis calls.                                                    |
| `LLM_TEMPERATURE`                       | `0.1`               | LLM sampling temperature.                                                                              |
| `LLM_TIMEOUT`                           | `120`               | Base per-request timeout in seconds. The synthesis call uses `3x` this value.                          |
| `DATAFLOW_EXTRACT_TOKENS`               | `400`               | Max output tokens per extraction call (Pass 1). Kept low since extractions are compact summaries.      |
| `DATAFLOW_SYNTH_TOKENS`                 | `1800`              | Max output tokens for the synthesis call (Pass 2). Higher to allow for a comprehensive trace document. |

## Exit Codes

| Code   | Meaning                                                                                                                |
| ------ | ---------------------------------------------------------------------------------------------------------------------- |
| `0`    | Success. Data flow document written.                                                                                   |
| `1`    | Target directory not found, no matching files found, no extractions succeeded, or synthesis failed (fallback written). |
| `2`    | Required prompt file is missing.                                                                                       |

Individual extraction failures do not cause the script to exit. The synthesis proceeds with whatever extractions succeeded. A warning is printed indicating incomplete coverage.

## Examples

### Example 1: Generate data flow for the entire repo

```powershell
cd C:\Projects\MyApp
.\LocalLLMDebug\dataflow_local.ps1
```

Extracts pipeline interfaces from all matching source files and synthesises them into `architecture/DATA_FLOW.md`.

### Example 2: Target a specific subdirectory with a clean start

```powershell
.\LocalLLMDebug\dataflow_local.ps1 -TargetDir src/nmon -Clean
```

Wipes the extraction cache and output, then re-processes all files under `src/nmon/`. Useful when you have changed the prompt files or switched LLM models.

### Example 3: Use a custom configuration

```powershell
.\LocalLLMDebug\dataflow_local.ps1 -TargetDir src -EnvFile C:\configs\project.env
```

Uses a project-specific `.env` file for LLM settings while scanning the `src/` directory.

## Notes

- The script supports Ctrl+Q cancellation between files (checked via `Test-CancelKey` from `llm_common.ps1`).
- Extraction results are cached by SHA1 of the source file content. If you change prompt files but not source files, use `-Clean` to force re-extraction.
- The synthesis pass timeout is automatically set to 3x the base `LLM_TIMEOUT` since it processes all extractions at once.
- The final document includes a suggested command for loading it in Claude Code: `Read architecture/DATA_FLOW.md`.
