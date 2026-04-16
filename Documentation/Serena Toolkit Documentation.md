# Architecture Analysis Toolkit -- Technical Reference

Complete technical reference for the architecture documentation pipeline using a local Ollama LLM server with optional clangd LSP. The toolkit lives at `..\LocalLLM_Pipeline\LocalLLMAnalysis\` (sibling of your target project); scripts are run from your target project's root.

---

## Table of Contents

1. [Project Goals](#1-project-goals)
2. [Environment](#2-environment)
3. [Pipeline Stages](#3-pipeline-stages)
4. [Local LLM Integration](#4-local-llm-integration)
5. [clangd / LSP Setup](#5-clangd--lsp-setup)
6. [LSP Extraction Design](#6-lsp-extraction-design)
7. [Generating compile_commands.json](#7-generating-compile_commandsjson)
8. [Context Optimization Strategy](#8-context-optimization-strategy)
9. [Configuration Files](#9-configuration-files)
10. [Lessons Learned](#10-lessons-learned)
11. [Quick Reference Commands](#11-quick-reference-commands)

---

## 1. Project Goals

Multi-pass architecture documentation pipeline for C++ game engine codebases, combining:

- **Local LLM** (Ollama) for per-file and subsystem-level analysis
- **clangd** (optional) for ground-truth symbol definitions and cross-file references
- **Text processing** for cross-references, call graphs, and targeted context

Designed for consumer GPU hardware (24 GB+ VRAM) with coder-tuned models like `qwen3-coder:30b` at a per-request `num_ctx` of 49152 tokens (configurable via `LLM_ANALYSIS_NUM_CTX`).

---

## 2. Environment

| Item             | Detail                                                                                |
|------------------|---------------------------------------------------------------------------------------|
| OS               | Windows 11                                                                            |
| GPU              | RTX 3090 or better (24 GB VRAM; Analysis at `num_ctx=49152` uses ~6-8 GB KV cache)    |
| LLM Server       | Ollama (`LLM_ENDPOINT`, default `http://192.168.1.126:11434`)                         |
| Model            | `qwen3-coder:30b` (configurable via `LLM_MODEL`)                                      |
| Context window   | 49152 tokens per Analysis request (via `LLM_ANALYSIS_NUM_CTX`, promoted into `LLM_NUM_CTX` at script startup) |
| Target codebase  | Any language with a preset (Python, C/C++, C#, Rust, etc.)                            |
| Toolkit location | `LocalLLM_Pipeline/LocalLLMAnalysis/` subdirectory                                         |
| Config           | `LocalLLM_Pipeline/Common/.env` (shared with Debug and Coding pipelines)                   |
| Output           | `architecture/` at repo root (renamed to `N. <subsection>` after completion)          |
| clangd           | VS Community Edition: `C:\Program Files\Microsoft Visual Studio\18\Community\VC\Tools\Llvm\x64\bin\clangd.exe` |

---

## 3. Pipeline Stages

| Stage        | Script                                       | LLM Calls  | Description                            |
|--------------|----------------------------------------------|------------|----------------------------------------|
| LSP Extract  | `..\LocalLLM_Pipeline\LocalLLMAnalysis\serena_extract.ps1`          | 0 (free)   | Symbol data via clangd (optional)      |
| Pass 1       | `..\LocalLLM_Pipeline\LocalLLMAnalysis\archgen_local.ps1`           | 1 per file | Per-file architecture docs             |
| Cross-ref    | `..\LocalLLM_Pipeline\LocalLLMAnalysis\archxref.ps1`                | 0 (free)   | Function mappings, call graph edges    |
| Graphs       | `..\LocalLLM_Pipeline\LocalLLMAnalysis\archgraph.ps1`               | 0 (free)   | Mermaid call graph + subsystem diagrams|
| Overview     | `..\LocalLLM_Pipeline\LocalLLMAnalysis\arch_overview_local.ps1`     | N+1        | Subsystem architecture overview        |
| P2 Context   | `..\LocalLLM_Pipeline\LocalLLMAnalysis\archpass2_context.ps1`       | 0 (free)   | Targeted context per file              |
| Pass 2       | `..\LocalLLM_Pipeline\LocalLLMAnalysis\archpass2_local.ps1`         | 1 per file | Selective re-analysis                  |

All LLM scripts run synchronously (single-threaded, GPU bound).

---

## 4. Local LLM Integration

### API

`Invoke-LocalLLM` in `llm_common.ps1` calls Ollama's OpenAI-compatible endpoint at `POST {endpoint}/v1/chat/completions`. Retry logic (3 attempts, 5s delay), empty response validation, UTF-8 encoding.

`Get-LLMEndpoint` builds the URL from `LLM_HOST` + `LLM_PORT` in `.env`.

### Architecture

- All `*_local.ps1` scripts dot-source `llm_common.ps1` from `$PSScriptRoot`
- Prompt files loaded from `$PSScriptRoot` (same directory as scripts)
- `.env` read from `$PSScriptRoot` (`LocalLLMAnalysis/`, same directory as the scripts)
- No worker scripts -- processing inlined in main loop

### Prompt Design

- Short system prompts (~80 tokens)
- Bullet-point output schemas (not tables)
- Explicit token limits in prompt text
- One file per request

---

## 5. clangd / LSP Setup

Optional. Enriches Pass 1 with accurate symbol info.

### Requirements

1. `compile_commands.json` at repo root
2. clangd installed and in PATH (VS Community Edition or `winget install LLVM.LLVM`)

No separate index-building step needed -- `serena_extract.ps1` spawns clangd which builds the index on-the-fly (first run is slower). Index caches at `.cache/clangd/index/`.

### clangd Configuration (.clangd at repo root)

```yaml
Index:
  Background: Build
  StandardLibrary: No

CompileFlags:
  Remove: [-W*, -fdiagnostics*]

Diagnostics:
  Suppress: ["*"]
  ClangTidy: false
```

### PATH Setup

Add clangd to PATH if needed:
```powershell
$env:PATH += ";C:\Program Files\Microsoft Visual Studio\18\Community\VC\Tools\Llvm\x64\bin"
```

The clangd index builds automatically during the first `serena_extract.ps1` run and caches at `.cache/clangd/index/`.

---

## 6. LSP Extraction Design

### serena_extract.py

Python script that spawns multiple clangd processes via LSP JSON-RPC:
- Workers pull files from shared queue (adaptive parallel)
- RAM monitored every ~60 seconds for auto-scaling
- Per file: `didOpen` -> `documentSymbol` -> `references` (optional) -> `didClose`
- LSP-trimmed source for large files (>800 lines) using symbol ranges
- Crash recovery: detects broken pipe, restarts clangd, retries once
- Incremental: empty files recorded in hash DB, skipped on rerun
- PCH cleanup: auto-removes session `preamble-*.pch` files on exit

### How archgen_local.ps1 Uses LSP Context

When `LocalLLMAnalysis/.serena_context/` exists:
1. Auto-detected at startup
2. Per file: `Load-CompressedLSP` extracts only Symbol Overview section
3. Injected as `LSP SYMBOL CONTEXT` in the prompt (~500 tokens vs ~2000 full)

---

## 7. Generating compile_commands.json

```powershell
python ..\LocalLLM_Pipeline\LocalLLMAnalysis\generate_compile_commands.py
```

The script discovers whichever build artifacts are present, in this order:

1. **Pre-existing `compile_commands.json`** anywhere under the root (e.g. `build/compile_commands.json` from a prior CMake run) -- copied verbatim.
2. **Native VS/VC++ project files** parsed directly: `.vcxproj` (MSBuild), `.vcproj` (VS 2002-2008), `.dsp` (VC6). Per project, the Release-style configuration is preferred; `AdditionalIncludeDirectories`, `PreprocessorDefinitions`, and the source list are pulled from each project. `.sln` files are reported for visibility.
3. **Delegate** to `cmake` / `meson` / `ninja` / `bazel` when their config files sit at the repo root. If the required tool is not on `PATH`, the script stops and prints the install URL. CMake and Meson additionally require `ninja` as the backend.

On duplicate source paths the newer format wins: `.vcxproj` > `.vcproj` > `.dsp`.

For the C&C Remastered Collection source release, the script finds two `.vcxproj` files -- `REDALERT/RedAlert.vcxproj` (293 TUs) and `TIBERIANDAWN/TiberianDawn.vcxproj` (193 TUs) -- for a total of 486 entries. Each project contributes its own `-I REDALERT` (or `-I TIBERIANDAWN`) plus `-I .../WIN32LIB` and per-project preprocessor defines (e.g. `REDALERT_EXPORTS`, `TIBERIANDAWN_EXPORTS`, `ENGLISH` for RA). The `CnCTDRAMapEditor/` tree is C# and is skipped (clangd is C++-only).

---

## 8. Context Optimization Strategy

### What's In

| Feature                  | Why                                                |
|--------------------------|----------------------------------------------------|
| Source (truncated)       | Core input for analysis                            |
| Compressed LSP symbols   | Accurate type/function info at low token cost     |
| Adaptive output budget   | Prevents waste on small files                     |
| Trivial file skipping    | Saves LLM calls entirely                          |
| Targeted Pass 2 context  | 30-80 lines of relevant context vs 500+ generic  |
| SHA1 incremental skip    | Zero cost for unchanged files                     |

### What Was Removed

| Feature              | Why                            |
|----------------------|--------------------------------|
| Header bundling      | Too many tokens for 32K        |
| Directory context    | Not worth the token cost       |
| Multi-file batching  | Local model handles one better |

### Token Budget Per Request

| Component              | Tokens         |
|------------------------|----------------|
| System prompt          | ~80            |
| Output schema          | ~200           |
| Source (800 lines)     | ~4000-6000     |
| LSP context            | ~500           |
| **Total input**        | **~5000-7000** |
| **Output**             | **~300-800**   |

---

## 9. Configuration Files

### `LocalLLM_Pipeline/Common/.env` (shared with Debug and Coding pipelines)

```env
LLM_ENDPOINT=http://192.168.1.126:11434
LLM_MODEL=qwen3-coder:30b
LLM_NUM_CTX=32768
LLM_ANALYSIS_NUM_CTX=49152
LLM_TEMPERATURE=0.1
LLM_MAX_TOKENS=800
LLM_TIMEOUT=300

PRESET=python
CODEBASE_DESC="nmon -- Python terminal GPU monitor"
MAX_FILE_LINES=800
SKIP_TRIVIAL=1

# Preset overrides (if the preset's default regexes don't match your project)
INCLUDE_EXT_REGEX=\.(py|toml)$
EXCLUDE_DIRS_REGEX=[/\\](\.git|architecture|__pycache__|\.venv|venv|dist|build)([/\\]|$)

#Subsections begin
REDALERT
# 530 files at root + 79 in WIN32LIB subdir
TIBERIANDAWN
# 307 files at root + 82 in WIN32LIB subdir
CnCTDRAMapEditor
# 257 C# files
#Subsections end
```

The `#Subsections begin` / `#Subsections end` block lists subdirectories for `Arch_Analysis_Pipeline.py`. Comment lines (e.g. `# 530 files`) are ignored by the parser.

### .clangd (at repo root, optional)

```yaml
Index:
  Background: Build
  StandardLibrary: No

CompileFlags:
  Remove: [-W*, -fdiagnostics*]

Diagnostics:
  Suppress: ["*"]
  ClangTidy: false
```

---

## 10. Lessons Learned

### Local LLM

1. **14B models produce good per-file docs with aggressive context management.** Cap source at 800 lines, simplified prompts, compressed LSP. Measured ~0.11 files/sec on an RTX 3090 with `qwen2.5-coder:14b`. The current default `qwen3-coder:30b` at `num_ctx=49152` is slower per call but handles larger context injections without truncation.

2. **Single-threaded is correct for local GPU inference.** One RTX 3090 = one inference at a time. Removing parallelism simplifies code with no throughput penalty.

3. **Bullet points > tables in prompts for smaller models.** More reliable output format.

4. **Post-processing catches preamble chatter.** Strip everything before the first `#` heading.

5. **Split `LLM_HOST`/`LLM_PORT` for flexible deployment.** Easy switching between local, LAN, remote.

6. **Shared module eliminates massive duplication.** `llm_common.ps1` is single source of truth.

### clangd / LSP

7. **clangd works without perfect compilation info.** A generated `compile_commands.json` with guessed include paths is enough for symbol extraction on most files.

8. **Legacy VC++ project files (`.dsp`, `.vcproj`) still contain usable include paths.** For older engine drops, parsing the project file directly avoids the need for a full CMake build tree; `generate_compile_commands.py` supports `.dsp`, `.vcproj`, and `.vcxproj` with the same code path.

9. **clangd parse time is dominated by include chains.** File size is irrelevant -- the bottleneck is header resolution.

10. **Parallel clangd instances scale linearly.** Each loads the same read-only index. Shared queue ensures even distribution.

11. **clangd crashes are silent and cascading.** Crash detection + auto-restart + single retry prevents queue-wide failures.

12. **`--pch-storage=disk` can consume 50+ GB.** Auto-cleanup at exit prevents unbounded disk usage.

### Pipeline Design

13. **Adaptive output budget prevents waste on small files.** 30-line header doesn't need 800 tokens.

14. **Targeted Pass 2 context saves ~70% of input tokens.** 30-80 lines relevant context vs 500+ generic.

15. **Chunked overview with summary extraction fits local context.** Headings + purpose only, threshold 400 lines.

---

## 11. Quick Reference Commands

### Full Pipeline

```powershell
..\LocalLLM_Pipeline\LocalLLMAnalysis\archgen_local.ps1 -Preset cnc
..\LocalLLM_Pipeline\LocalLLMAnalysis\archxref.ps1
..\LocalLLM_Pipeline\LocalLLMAnalysis\archgraph.ps1
..\LocalLLM_Pipeline\LocalLLMAnalysis\arch_overview_local.ps1
..\LocalLLM_Pipeline\LocalLLMAnalysis\archpass2_context.ps1
..\LocalLLM_Pipeline\LocalLLMAnalysis\archpass2_local.ps1 -Top 100
```

### With LSP

```powershell
python ..\LocalLLM_Pipeline\LocalLLMAnalysis\generate_compile_commands.py
..\LocalLLM_Pipeline\LocalLLMAnalysis\serena_extract.ps1 -Preset cnc
..\LocalLLM_Pipeline\LocalLLMAnalysis\archgen_local.ps1 -Preset cnc
```

### Single Subsystem

```powershell
..\LocalLLM_Pipeline\LocalLLMAnalysis\archgen_local.ps1 -TargetDir REDALERT -Preset cnc
```

### Clean Start

```powershell
..\LocalLLM_Pipeline\LocalLLMAnalysis\archgen_local.ps1 -Clean
..\LocalLLM_Pipeline\LocalLLMAnalysis\archpass2_local.ps1 -Clean
```
