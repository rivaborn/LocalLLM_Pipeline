# archpass2_local.ps1 -- Context-Aware Second-Pass Analysis

## Purpose

`archpass2_local.ps1` performs a second-pass architectural analysis of source files, enriching the
first-pass documentation with cross-cutting insights. While `archgen_local.ps1` (Pass 1) analyzes
each file in isolation, Pass 2 re-analyzes files with injected context: the architecture overview,
cross-reference index, and (optionally) targeted per-file context built by `archpass2_context.ps1`.

The script scores and ranks candidate files by their architectural significance -- files with more
cross-references and larger codebases rank higher. It supports a `-Top N` filter to process only the
most important files, and a `-ScoreOnly` mode to preview rankings without running the LLM. Like
Pass 1, it is fully incremental via SHA-1 hash tracking.

## Prerequisites

| Requirement                               | Details                                          |
| ----------------------------------------- | ------------------------------------------------ |
| PowerShell 5.1+ or pwsh 7+                | Uses `Set-StrictMode -Version Latest`            |
| `llm_common.ps1`                          | Shared module in `../Common/`                    |
| `../Common/.env`                          | Configuration file                               |
| Ollama running locally                    | LLM endpoint must be reachable                   |
| `architecture/architecture.md`            | Output of `arch_overview_local.ps1`              |
| `architecture/xref_index.md`              | Output of `archxref.ps1`                         |
| Per-file Pass 1 docs                      | Output of `archgen_local.ps1` in `architecture/` |
| (Optional) `architecture/.pass2_context/` | Targeted context from `archpass2_context.ps1`    |
| (Optional) `archpass2_local_prompt.txt`   | Prompt schema in script directory                |

## Usage

```powershell
.\archpass2_local.ps1 [-TargetDir <dir>] [-Clean] [-Only <paths>] [-Top <n>] [-ScoreOnly] [-EnvFile <path>]
```

### CLI Options

| Parameter    | Type   | Default          | Description                                                                          |
| ------------ | ------ | ---------------- | ------------------------------------------------------------------------------------ |
| `-TargetDir` | string | `"."`            | Subdirectory to scan (relative to repo root). `"."` scans the entire repo.           |
| `-Clean`     | switch | off              | Remove all `.pass2.md` files and Pass 2 state before processing.                     |
| `-Only`      | string | `""`             | Comma-separated list of specific relative file paths to process (bypasses scanning). |
| `-Top`       | int    | `0`              | Process only the top N files ranked by significance score. `0` = process all.        |
| `-ScoreOnly` | switch | off              | Print the ranked file list with scores and exit without running the LLM.             |
| `-EnvFile`   | string | `../Common/.env` | Alternative `.env` file.                                                             |

## Scoring Algorithm

Each candidate file receives a score calculated as:

```
score = (incoming_xref_count * 3) + (source_line_count / 100)
```

If the file has Serena LSP context (`.serena_context.txt`), the score is halved (since Pass 1
already had rich context for that file). Files are processed in descending score order.

## How It Is Invoked

**Standalone:**
```powershell
cd C:\Coding\MyProject
.\LocalLLMAnalysis\archpass2_local.ps1 -Top 50
```

**Via ArchPipeline.py (analysis mode):**
Called as the sixth and final analysis step:
```
python Common/ArchPipeline.py analysis
```

## Input Files

| Input                        | Location                                           | Description                                              |
| ---------------------------- | -------------------------------------------------- | -------------------------------------------------------- |
| Source files                 | `<repo>/<TargetDir>/`                              | Original source code (truncated to 300 lines for Pass 2) |
| Pass 1 docs                  | `<repo>/architecture/<rel>.md`                     | First-pass analysis for each file                        |
| Architecture overview        | `<repo>/architecture/architecture.md`              | Global architecture context (truncated to 8000 chars)    |
| Xref index                   | `<repo>/architecture/xref_index.md`                | Cross-reference data (truncated to 4000 chars)           |
| Targeted context             | `<repo>/architecture/.pass2_context/<rel>.ctx.txt` | Preferred over global context when available             |
| `.env`                       | `../Common/.env`                                   | Configuration                                            |
| `archpass2_local_prompt.txt` | Script directory                                   | Optional prompt schema                                   |

## Output Files

| Output        | Location                                          | Description                                   |
| ------------- | ------------------------------------------------- | --------------------------------------------- |
| Pass 2 docs   | `<repo>/architecture/<rel_path>.pass2.md`         | Enhanced analysis with cross-cutting insights |
| Hash database | `<repo>/architecture/.pass2_state/hashes.tsv`     | Incremental skip tracking                     |
| Error log     | `<repo>/architecture/.pass2_state/last_error.log` | Timestamped failures                          |

## Environment Variables / .env Keys

| Key                    | Default             | Description                    |
| ---------------------- | ------------------- | ------------------------------ |
| `PRESET`               | `""`                | Named preset                   |
| `INCLUDE_EXT_REGEX`    | Preset-dependent    | File extension include pattern |
| `EXCLUDE_DIRS_REGEX`   | Preset-dependent    | Directory exclude pattern      |
| `EXTRA_EXCLUDE_REGEX`  | `""`                | Additional exclude pattern     |
| `CODEBASE_DESC`        | Preset-dependent    | Codebase description           |
| `DEFAULT_FENCE`        | Preset-dependent    | Code fence language            |
| `LLM_MODEL`            | `qwen2.5-coder:14b` | Ollama model                   |
| `LLM_TEMPERATURE`      | `0.1`               | Sampling temperature           |
| `LLM_TIMEOUT`          | `120`               | HTTP timeout in seconds        |
| `LLM_ANALYSIS_NUM_CTX` | (none)              | Overrides `LLM_NUM_CTX` if set |

## Exit Codes

| Code | Meaning                                                                                    |
| ---- | ------------------------------------------------------------------------------------------ |
| `0`  | Success (or nothing to do / score-only mode)                                               |
| `1`  | Missing `architecture.md` or `xref_index.md`, target directory not found, or no candidates |

## Examples

**Example 1: Process the top 50 most significant files**
```powershell
cd C:\Coding\Generals
.\LocalLLMAnalysis\archpass2_local.ps1 -Top 50
```

**Example 2: Preview file rankings without running the LLM**
```powershell
.\archpass2_local.ps1 -ScoreOnly -Top 20
```
Prints the top 20 files with their score, line count, and incoming reference count.

**Example 3: Re-analyze specific files**
```powershell
.\archpass2_local.ps1 -Only "Generals/Code/GameEngine/Source/Engine.cpp,Generals/Code/GameEngine/Source/Renderer.cpp"
```
Bypasses scanning and processes only the two listed files.
