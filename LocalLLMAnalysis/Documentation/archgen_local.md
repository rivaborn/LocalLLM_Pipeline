# archgen_local.ps1 -- Per-File Architecture Doc Generator

## Purpose

`archgen_local.ps1` is the first and most foundational step in the analysis pipeline. It scans a
codebase for source files matching configurable extension/directory patterns, reads each file, and
sends it (along with an optional LSP symbol context from Serena) to a local Ollama LLM. The LLM
produces a structured Markdown architecture document for every source file, describing its purpose,
key functions, data structures, global state, dependencies, and control flow.

The script is **incremental**: it maintains a SHA-1 hash database so unchanged files are
automatically skipped on subsequent runs. Trivial or generated files (below a configurable line
threshold or matching known trivial patterns) receive a lightweight stub document instead of a full
LLM call, saving time and GPU resources.

## Prerequisites

| Requirement | Details |
|---|---|
| PowerShell 5.1+ or pwsh 7+ | Script uses `Set-StrictMode -Version Latest` |
| `llm_common.ps1` | Shared module in `../Common/`; provides `Invoke-LocalLLM`, `Get-Preset`, `Cfg`, hash helpers, etc. |
| `../Common/.env` | Configuration file with LLM endpoint, model, preset, and project settings |
| Ollama running locally | Default endpoint from `.env` (typically `http://localhost:11434`) |
| `archgen_local_prompt.txt` | Prompt schema file in the same directory as the script |
| (Optional) `.serena_context/` | Compressed LSP symbol files from `serena_extract.ps1` for richer context |

## Usage

```powershell
.\archgen_local.ps1 [-TargetDir <path>] [-Preset <name>] [-Clean] [-NoHeaders] [-EnvFile <path>] [-Test]
```

### CLI Options

| Parameter | Type | Default | Description |
|---|---|---|---|
| `-TargetDir` | string | `"."` | Subdirectory (relative to repo root) to scan. `"."` scans the entire repo. |
| `-Preset` | string | `""` | Named preset (e.g. `generals`, `unreal`, `quake`). Overrides `PRESET` in `.env`. Controls include/exclude patterns, codebase description, and fence language. |
| `-Clean` | switch | off | Removes all generated docs and state (preserves `.serena_context`, `.dir_context`, `.dir_headers`), then regenerates. |
| `-NoHeaders` | switch | off | Reserved flag (present in param block but not used in main logic). |
| `-EnvFile` | string | `../Common/.env` | Path to an alternative `.env` configuration file. |
| `-Test` | switch | off | Reserved flag for test harness integration. |

## How It Is Invoked

**Standalone:**
```powershell
cd C:\Coding\MyProject
.\LocalLLMAnalysis\archgen_local.ps1 -Preset generals
```

**Via ArchPipeline.py (analysis mode):**
The unified pipeline orchestrator calls this as the first analysis step:
```
python Common/ArchPipeline.py analysis
```
which runs `archgen_local.ps1 -Preset generals` via PowerShell subprocess.

## Input Files

| Input | Location | Description |
|---|---|---|
| Source files | `<repo_root>/<TargetDir>/` | All files matching `INCLUDE_EXT_REGEX` and not matching `EXCLUDE_DIRS_REGEX` / `EXTRA_EXCLUDE_REGEX` |
| `.env` | `../Common/.env` | Configuration (LLM endpoint, model, temperature, max tokens, timeout, preset, etc.) |
| `archgen_local_prompt.txt` | Same directory as script | The output schema/instructions sent to the LLM |
| `.serena_context/<rel>.serena_context.txt` | Same directory as script | Optional compressed LSP symbol data per file |

## Output Files

| Output | Location | Description |
|---|---|---|
| Per-file docs | `<repo_root>/architecture/<rel_path>.md` | One Markdown doc per source file |
| Hash database | `<repo_root>/architecture/.archgen_state/hashes.tsv` | Tab-separated `SHA1\trelpath` for incremental skip |
| Error log | `<repo_root>/architecture/.archgen_state/last_error.log` | Timestamped failures |
| Trivial stubs | `<repo_root>/architecture/<rel_path>.md` | Lightweight stub for trivial/generated files |

## Environment Variables / .env Keys

| Key | Default | Description |
|---|---|---|
| `PRESET` | `""` | Named preset (overridden by `-Preset` CLI flag) |
| `INCLUDE_EXT_REGEX` | Preset-dependent | Regex for file extensions to include |
| `EXCLUDE_DIRS_REGEX` | Preset-dependent | Regex for directories/paths to exclude |
| `EXTRA_EXCLUDE_REGEX` | `""` | Additional exclude regex |
| `CODEBASE_DESC` | Preset-dependent | Human-readable codebase description for LLM context |
| `DEFAULT_FENCE` | Preset-dependent | Default code fence language (e.g. `cpp`) |
| `MAX_FILE_LINES` | `800` | Truncate source files beyond this line count before sending to LLM |
| `SKIP_TRIVIAL` | `1` | Whether to skip trivial files (`1` = yes) |
| `MIN_TRIVIAL_LINES` | `20` | Files below this line count are considered trivial |
| `LLM_MODEL` | `qwen2.5-coder:14b` | Ollama model name |
| `LLM_TEMPERATURE` | `0.1` | LLM sampling temperature |
| `LLM_MAX_TOKENS` | `800` | Maximum output tokens |
| `LLM_TIMEOUT` | `120` | HTTP request timeout in seconds |
| `LLM_ANALYSIS_NUM_CTX` | (none) | If set, overrides `LLM_NUM_CTX` for analysis scripts |
| `LLM_ENDPOINT` | (from `Get-LLMEndpoint`) | Ollama API URL |

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Success (all files processed or nothing to do) |
| `1` | No matching source files found, or target directory not found |
| `2` | Missing prompt file (`archgen_local_prompt.txt`) |

## Examples

**Example 1: Generate docs for an entire project using a preset**
```powershell
cd C:\Coding\Generals
.\LocalLLMAnalysis\archgen_local.ps1 -Preset generals
```
This scans all `.cpp`, `.h`, `.hpp`, `.c`, `.cc`, `.cxx`, `.inl`, `.inc` files (excluding
`Debug/`, `Release/`, `.git/`, etc.), generates one Markdown doc per file in
`C:\Coding\Generals\architecture\`, and records hashes for incremental runs.

**Example 2: Target a specific subdirectory and clean old state**
```powershell
.\archgen_local.ps1 -TargetDir Generals/Code/GameEngine -Preset generals -Clean
```
Removes all previous docs (except `.serena_context`), then regenerates docs only for files
under `Generals/Code/GameEngine/`.

**Example 3: Use a custom .env and model**
```powershell
.\archgen_local.ps1 -EnvFile C:\Coding\custom.env
```
Reads LLM endpoint, model, and all configuration from `custom.env` instead of the default.
