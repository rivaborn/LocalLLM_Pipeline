# interfaces_local.ps1

## Purpose

`interfaces_local.ps1` extracts and synthesises interface contract documentation for every public class and function in a codebase using a local LLM (via Ollama). It operates as a two-pass pipeline. In Pass 1, each source file is individually analysed to extract precise contracts -- preconditions, postconditions, exceptions raised, silent failure modes, thread safety notes, and resource lifecycle information. In Pass 2, all per-module contracts are combined into a single synthesis call that produces a consolidated reference document with a quick-reference table, cross-module obligations, and a silent-failure inventory.

The output serves as a machine-readable and human-readable contract reference that developers and AI coding agents can use to understand module boundaries, spot violations at call sites, and identify hidden failure modes that are not surfaced by the source code alone.

## Prerequisites

- **PowerShell 5.1+** (Windows PowerShell or PowerShell Core)
- **Ollama** running locally with a code-capable model loaded
- **`llm_common.ps1`** -- shared module at `Common/llm_common.ps1`
- **`.env` file** -- at `Common/.env` (or custom path via `-EnvFile`)
- **Prompt files** in the `LocalLLMDebug/` directory:
  - `interfaces_prompt.txt` (per-file contract extraction schema)
  - `interfaces_synth_prompt.txt` (synthesis schema)
- No prior pipeline steps are strictly required. However, if a Serena LSP context directory has been configured (`SERENA_CONTEXT_DIR`), the script uses authoritative LSP type information to ground extracted contracts in exact type names rather than inferring from source text.

## Usage

```powershell
.\LocalLLMDebug\interfaces_local.ps1 [options]
```

### CLI Options

| Parameter    | Type   | Default       | Description                                                                                      |
| ------------ | ------ | ------------- | ------------------------------------------------------------------------------------------------ |
| `-TargetDir` | string | `"."`         | Subdirectory of the repo to scan (relative to repo root). Use `"."` for the entire repo.         |
| `-Clean`     | switch | off           | Delete per-file interface docs, the extraction cache, and the synthesised output before running. |
| `-EnvFile`   | string | `Common/.env` | Path to the `.env` configuration file.                                                           |

## How It Is Invoked

### Standalone

```powershell
cd C:\Projects\MyApp
.\LocalLLMDebug\interfaces_local.ps1 -TargetDir src/nmon
```

### Via ArchPipeline.py (current)

`ArchPipeline.py debug` mode calls `interfaces_local.ps1` as **step 2** of its 6-step sequence. The orchestrator at `Common/_pipeline/modes/debug/cli.py` invokes it as a subprocess via `subprocess_runner.powershell_cmd()`, forwarding `--target-dir` → `-TargetDir` and resolving `-EnvFile` from `Common/.env` automatically.

```
Step 1: dataflow_local.ps1
Step 2: interfaces_local.ps1    <-- this script
Step 3: testgap_local.ps1
Step 4: bughunt_local.ps1
Step 5: fix_bugs.py (inline)
Step 6: Archive
```

The legacy `Arch_Debug_Pipeline.ps1` has been retired; it remains in `legacy/` for reference only.

## Input Files

| Input                                     | Description                                                                                                                                                                                                                                                                                    |
| ----------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Source files matching `INCLUDE_EXT_REGEX` | All files under `TargetDir` that match the inclusion regex and do not match exclusion regexes. Directories `architecture/`, `bug_reports/`, `bug_fixes/`, and `test_gaps/` are excluded.                                                                                                       |
| `Common/.env`                             | Configuration for LLM endpoint, model, temperature, and token limits.                                                                                                                                                                                                                          |
| `interfaces_prompt.txt`                   | Structured prompt for Pass 1 (per-file contract extraction).                                                                                                                                                                                                                                   |
| `interfaces_synth_prompt.txt`             | Structured prompt for Pass 2 (synthesis into a combined reference).                                                                                                                                                                                                                            |
| Serena LSP context (optional)             | If `SERENA_CONTEXT_DIR` is configured and exists, each extraction prompt is prefixed with the file's LSP Symbol Overview section (via `Load-CompressedLSP`). This provides authoritative type and signature information from clangd or another LSP server, grounding contracts in exact types. |

## Output Files and Directories

| Output                                           | Description                                                                                                                                                      |
| ------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `architecture/INTERFACES.md`                     | The final synthesised interface contract reference. Includes an HTML comment header with generation metadata (date, codebase, file count, model, per-file path). |
| `architecture/interfaces/<rel>.iface.md`         | Per-file contract extraction in Markdown. One file per source module.                                                                                            |
| `architecture/.interfaces_state/cache/<sha>.txt` | Cached extraction results keyed by SHA1 of the source file. Only changed files are re-extracted on subsequent runs.                                              |
| `architecture/.interfaces_state/last_error.log`  | Timestamped log of extraction and synthesis failures.                                                                                                            |

### Fallback Behavior

If the synthesis call fails, the script writes a fallback document containing all raw per-file contracts concatenated together, prefixed with the error message. The script exits with code 1 in this case, but partial results are preserved.

## Environment Variables / .env Keys

| Key                                     | Default                 | Description                                                                                                                                                         |
| --------------------------------------- | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `PRESET`                                | `""`                    | Named preset for include/exclude patterns and codebase description.                                                                                                 |
| `INCLUDE_EXT_REGEX`                     | from preset             | Regex matching file extensions to include.                                                                                                                          |
| `EXCLUDE_DIRS_REGEX`                    | from preset             | Regex matching directory paths to exclude.                                                                                                                          |
| `EXTRA_EXCLUDE_REGEX`                   | `""`                    | Additional exclusion regex.                                                                                                                                         |
| `CODEBASE_DESC`                         | from preset             | Human-readable codebase description for LLM system prompts.                                                                                                         |
| `DEFAULT_FENCE`                         | from preset             | Code fence language identifier.                                                                                                                                     |
| `MAX_FILE_LINES`                        | `800`                   | Files longer than this are truncated before sending to the LLM.                                                                                                     |
| `LLM_ENDPOINT` or `LLM_HOST`+`LLM_PORT` | —                       | Ollama API endpoint.                                                                                                                                                |
| `LLM_MODEL`                             | blank (→ `LLM_DEFAULT_MODEL` → `qwen3-coder:30b`) | Model name. Resolved via `Get-LLMModel -RoleKey 'LLM_MODEL'`. Per-request `num_ctx` covers the ~10k synth-pass window.         |
| `LLM_DEFAULT_MODEL`                     | `qwen3-coder:30b`       | Universal fallback used when `LLM_MODEL` is blank/unset.                                                                                                            |
| `LLM_TEMPERATURE`                       | `0.1`                   | LLM sampling temperature.                                                                                                                                           |
| `LLM_TIMEOUT`                           | `120`                   | Base per-request timeout in seconds. The synthesis call uses `3x` this value.                                                                                       |
| `INTERFACES_EXTRACT_TOKENS`             | `700`                   | Max output tokens per extraction call (Pass 1).                                                                                                                     |
| `INTERFACES_SYNTH_TOKENS`               | `2000`                  | Max output tokens for the synthesis call (Pass 2).                                                                                                                  |
| `SERENA_CONTEXT_DIR`                    | —                       | Optional path to a directory containing Serena/clangd LSP context files. When set, LSP symbol data is injected into extraction prompts for type-accurate contracts. |

## Exit Codes

| Code   | Meaning                                                                                                                |
| ------ | ---------------------------------------------------------------------------------------------------------------------- |
| `0`    | Success. Interface contract reference and per-file docs written.                                                       |
| `1`    | Target directory not found, no matching files found, no extractions succeeded, or synthesis failed (fallback written). |
| `2`    | Required prompt file is missing.                                                                                       |

Individual extraction failures do not cause the script to exit. The synthesis proceeds with whatever extractions succeeded.

## Examples

### Example 1: Extract contracts for the entire repo

```powershell
cd C:\Projects\MyApp
.\LocalLLMDebug\interfaces_local.ps1
```

Processes all matching source files, writes per-file contracts to `architecture/interfaces/`, and produces the synthesised `architecture/INTERFACES.md`.

### Example 2: Target a subdirectory with a clean start

```powershell
.\LocalLLMDebug\interfaces_local.ps1 -TargetDir src/nmon -Clean
```

Wipes the cache and all interface docs, then re-extracts contracts for files under `src/nmon/`.

### Example 3: Use with Serena LSP context

If you have previously run Serena to extract LSP symbols and configured `SERENA_CONTEXT_DIR` in your `.env` file, the script automatically enriches each extraction prompt with authoritative type information:

```powershell
# In .env:
# SERENA_CONTEXT_DIR=.serena_context

.\LocalLLMDebug\interfaces_local.ps1 -TargetDir src
```

The console will print `[integration] serena context dir: ...` confirming that LSP data is being used.

## Notes

- The script supports Ctrl+Q cancellation between files.
- Extraction results are cached by SHA1 of source file content. Changing prompts or models without changing source requires `-Clean` to force re-extraction.
- The synthesis pass timeout is automatically set to 3x the base `LLM_TIMEOUT`.
- Per-file contracts are written to both the cache (for re-run efficiency) and to `architecture/interfaces/<rel>.iface.md` (for direct reading).
- The system prompt identifies itself as a "senior Python engineer" -- this is appropriate for Python codebases but the script works for any language matched by `INCLUDE_EXT_REGEX`.
- The final output suggests loading in Claude Code: `Read architecture/INTERFACES.md`.
