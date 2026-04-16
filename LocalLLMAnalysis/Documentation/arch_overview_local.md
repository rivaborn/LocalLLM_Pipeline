# arch_overview_local.ps1 -- Architecture Overview Generator

## Purpose

`arch_overview_local.ps1` synthesizes the per-file Markdown documents produced by `archgen_local.ps1`
into a high-level architecture overview. It reads the `# heading` and `## Purpose` sections from
each per-file doc, groups them by subsystem directory, and asks the local LLM to produce a unified
architecture document covering subsystem responsibilities, key files, and cross-subsystem
dependencies.

The script supports two modes: **single-pass** (when the total summary data fits within the LLM
context window) and **chunked** (default for larger codebases). In chunked mode it auto-discovers
subsystem directories, generates a per-subsystem overview for each chunk, then runs a second
synthesis pass to merge them into a final `architecture.md`. If synthesis fails, it falls back to
concatenating the subsystem overviews.

## Prerequisites

| Requirement | Details |
|---|---|
| PowerShell 5.1+ or pwsh 7+ | Uses `Set-StrictMode -Version Latest` |
| `llm_common.ps1` | Shared module in `../Common/` |
| `../Common/.env` | Configuration file |
| Ollama running locally | LLM endpoint must be reachable |
| Per-file docs | Output of `archgen_local.ps1` must exist in `<repo>/architecture/` |
| (Optional) `arch_overview_local_prompt.txt` | Prompt schema in script directory; used as the base prompt if present |

## Usage

```powershell
.\arch_overview_local.ps1 [-TargetDir <dir>] [-Single] [-Clean] [-Full] [-EnvFile <path>]
```

### CLI Options

| Parameter | Type | Default | Description |
|---|---|---|---|
| `-TargetDir` | string | `"all"` | Subsystem directory to summarize. `"all"` or `"."` processes the entire `architecture/` tree. A specific path (e.g. `Engine/Source/Runtime`) limits scope. |
| `-Single` | switch | off | Force single-pass mode even if summary data exceeds the chunk threshold. |
| `-Clean` | switch | off | Remove all `*architecture.md` and `*diagram_data.md` files before generating. |
| `-Full` | switch | off | Reserved flag (present in param block). |
| `-EnvFile` | string | `../Common/.env` | Alternative `.env` configuration file. |

## How It Is Invoked

**Standalone:**
```powershell
cd C:\Coding\MyProject
.\LocalLLMAnalysis\arch_overview_local.ps1
```

**Via ArchPipeline.py (analysis mode):**
Called as the fourth analysis step (after `archgen_local.ps1`, `archxref.ps1`, `archgraph.ps1`):
```
python Common/ArchPipeline.py analysis
```

## Input Files

| Input | Location | Description |
|---|---|---|
| Per-file docs | `<repo>/architecture/**/*.md` | Markdown docs from `archgen_local.ps1` (excludes state dirs, pass2 docs, meta files) |
| `.env` | `../Common/.env` | Configuration |
| `arch_overview_local_prompt.txt` | Script directory | Optional prompt schema |

## Output Files

| Output | Location | Description |
|---|---|---|
| Architecture overview | `<repo>/architecture/architecture.md` | Final synthesized overview (or `<prefix> architecture.md` when `-TargetDir` is set) |
| Per-subsystem overviews | `<repo>/architecture/<subsystem>_architecture.md` | Individual subsystem overviews (chunked mode only) |
| Error log | `<repo>/architecture/.overview_state/last_error.log` | Timestamped error entries |

## Environment Variables / .env Keys

| Key | Default | Description |
|---|---|---|
| `CODEBASE_DESC` | `"game engine / game codebase"` | Codebase description for LLM context |
| `CHUNK_THRESHOLD` | `400` | Summary line count above which chunked mode is used |
| `LLM_MODEL` | `qwen2.5-coder:14b` | Ollama model |
| `LLM_TEMPERATURE` | `0.1` | Sampling temperature |
| `LLM_TIMEOUT` | `120` | HTTP timeout in seconds |
| `LLM_ANALYSIS_NUM_CTX` | (none) | Overrides `LLM_NUM_CTX` if set |
| `LLM_ENDPOINT` | (from helper) | Ollama API URL |

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | No per-file docs found (run `archgen_local.ps1` first), or LLM synthesis failed in single-pass mode |

## Examples

**Example 1: Generate overview for the full codebase**
```powershell
cd C:\Coding\Generals
.\LocalLLMAnalysis\arch_overview_local.ps1
```
Discovers subsystems, generates per-subsystem overviews via chunked LLM calls, then synthesizes a
final `architecture/architecture.md`.

**Example 2: Force single-pass for a small subsystem**
```powershell
.\arch_overview_local.ps1 -TargetDir Engine/Source/Runtime/Core -Single
```
Summarizes only the `Core` subsystem in a single LLM call.

**Example 3: Clean and regenerate**
```powershell
.\arch_overview_local.ps1 -Clean
```
Removes all existing overview files, then generates fresh ones.
