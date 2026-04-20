# Architecture Analysis Toolkit -- Command Reference

Complete command-line reference for every script. The toolkit lives at `..\LocalLLM_Pipeline\LocalLLMAnalysis\` (sibling of your target project); all scripts are run from your target project's root (e.g. `cd C:\Coding\nmon`).

---

## Table of Contents

1. [Pipeline Overview](#1-pipeline-overview)
2. [archgen_local.ps1 -- Pass 1](#2-archgen_localps1----pass-1)
3. [archxref.ps1 -- Cross-Reference Index](#3-archxrefps1----cross-reference-index)
4. [archgraph.ps1 -- Call Graph Diagrams](#4-archgraphps1----call-graph-diagrams)
5. [arch_overview_local.ps1 -- Architecture Overview](#5-arch_overview_localps1----architecture-overview)
6. [archpass2_context.ps1 -- Targeted Pass 2 Context](#6-archpass2_contextps1----targeted-pass-2-context)
7. [archpass2_local.ps1 -- Pass 2](#7-archpass2_localps1----pass-2)
8. [serena_extract.ps1 -- LSP Extraction](#8-serena_extractps1----lsp-extraction)
9. [generate_compile_commands.py -- Compilation Database](#9-generate_compile_commandspy----compilation-database)
10. [llm_common.ps1 -- Shared Module](#10-llm_commonps1----shared-module)
11. [.env Configuration](#11-env-configuration)
12. [Presets](#12-presets)
13. [Common Workflows](#13-common-workflows)

---

## 1. Pipeline Overview

```
Step 0 (free):   ..\LocalLLM_Pipeline\LocalLLMAnalysis\serena_extract.ps1          LSP symbols (optional)
Step 1:          ..\LocalLLM_Pipeline\LocalLLMAnalysis\archgen_local.ps1            Per-file .md docs
Step 2 (free):   ..\LocalLLM_Pipeline\LocalLLMAnalysis\archxref.ps1                Cross-reference index
Step 3 (free):   ..\LocalLLM_Pipeline\LocalLLMAnalysis\archgraph.ps1               Mermaid diagrams
Step 4:          ..\LocalLLM_Pipeline\LocalLLMAnalysis\arch_overview_local.ps1      Architecture overview
Step 4b (free):  ..\LocalLLM_Pipeline\LocalLLMAnalysis\archpass2_context.ps1        Targeted context
Step 5:          ..\LocalLLM_Pipeline\LocalLLMAnalysis\archpass2_local.ps1          Pass 2 enrichment (selective)
```

---

## 2. archgen_local.ps1 -- Pass 1

Per-file architecture docs. Synchronous single-threaded.

### Syntax

```
..\LocalLLM_Pipeline\LocalLLMAnalysis\archgen_local.ps1
    [-TargetDir <string>]  [-Preset <string>]  [-Clean]  [-EnvFile <string>]
```

### Parameters

| Parameter    | Type   | Default  | Description                                          |
|--------------|--------|----------|------------------------------------------------------|
| `-TargetDir` | string | `"."`    | Subdirectory to scan (relative to repo root)         |
| `-Preset`    | string | `""`     | Engine preset. Falls back to `PRESET` in `.env`.     |
| `-Clean`     | switch | off      | Remove docs and state (preserves `.serena_context/`) |
| `-EnvFile`   | string | `".env"` | Path to configuration file                           |

### Examples

```powershell
..\LocalLLM_Pipeline\LocalLLMAnalysis\archgen_local.ps1 -Preset cnc
..\LocalLLM_Pipeline\LocalLLMAnalysis\archgen_local.ps1 -TargetDir REDALERT -Preset cnc
..\LocalLLM_Pipeline\LocalLLMAnalysis\archgen_local.ps1 -TargetDir CnCTDRAMapEditor -Preset cnc
..\LocalLLM_Pipeline\LocalLLMAnalysis\archgen_local.ps1 -Clean
```

### Performance

Throughput depends on model: historically ~0.11 files/sec with `qwen2.5-coder:14b` on RTX 3090. The current default `qwen3-coder:30b` at `LLM_ANALYSIS_NUM_CTX=49152` is slower per call but produces richer output.

---

## 3. archxref.ps1 -- Cross-Reference Index

No LLM calls. Parses Pass 1 docs into cross-reference index.

```
..\LocalLLM_Pipeline\LocalLLMAnalysis\archxref.ps1 [-TargetDir <string>] [-EnvFile <string>]
```

Output: `architecture/xref_index.md`.

---

## 4. archgraph.ps1 -- Call Graph Diagrams

No LLM calls. Mermaid diagrams from Pass 1 docs.

```
..\LocalLLM_Pipeline\LocalLLMAnalysis\archgraph.ps1
    [-TargetDir <string>]  [-MaxCallEdges <int>]  [-MinCallSignificance <int>]
```

| Parameter              | Default | Description                        |
|------------------------|---------|------------------------------------|
| `-MaxCallEdges`        | `150`   | Max edges in call graph            |
| `-MinCallSignificance` | `2`     | Min call count to include a callee |

Output: `architecture/callgraph.md`, `architecture/callgraph.mermaid`, `architecture/subsystems.mermaid`.

---

## 5. arch_overview_local.ps1 -- Architecture Overview

Synthesizes Pass 1 docs into subsystem overview. Auto-chunks for large codebases.

```
..\LocalLLM_Pipeline\LocalLLMAnalysis\arch_overview_local.ps1
    [-TargetDir <string>]  [-Single]  [-Clean]  [-Full]  [-EnvFile <string>]
```

| Parameter    | Default  | Description                                   |
|--------------|----------|-----------------------------------------------|
| `-TargetDir` | `"all"`  | Subdirectory scope                            |
| `-Single`    | off      | Force single-pass mode (small codebases only) |
| `-Full`      | off      | Force full regeneration                       |
| `-Clean`     | off      | Remove previous overview                      |

Output: `architecture/architecture.md` + per-subsystem files in chunked mode.

---

## 6. archpass2_context.ps1 -- Targeted Pass 2 Context

No LLM calls. Extracts relevant context per file for Pass 2. Requires steps 2 and 4.

```
..\LocalLLM_Pipeline\LocalLLMAnalysis\archpass2_context.ps1 [-TargetDir <string>] [-EnvFile <string>]
```

Output: `architecture/.pass2_context/<path>.ctx.txt`.

---

## 7. archpass2_local.ps1 -- Pass 2

Selective re-analysis with architecture context. Requires steps 1, 2, and 4.

```
..\LocalLLM_Pipeline\LocalLLMAnalysis\archpass2_local.ps1
    [-TargetDir <string>]  [-Clean]  [-Only <string>]  [-Top <int>]  [-ScoreOnly]
```

| Parameter    | Default  | Description                            |
|--------------|----------|----------------------------------------|
| `-TargetDir` | `"."`    | Subdirectory scope                     |
| `-Clean`     | off      | Remove Pass 2 output and state         |
| `-Only`      | `""`     | Comma-separated file paths to process  |
| `-Top`       | `0`      | Only process N highest-scoring files   |
| `-ScoreOnly` | off      | Print scores without running           |

### Examples

```powershell
..\LocalLLM_Pipeline\LocalLLMAnalysis\archpass2_local.ps1 -Top 100
..\LocalLLM_Pipeline\LocalLLMAnalysis\archpass2_local.ps1 -Top 50 -ScoreOnly
..\LocalLLM_Pipeline\LocalLLMAnalysis\archpass2_local.ps1 -Only "REDALERT/CONQUER.CPP"
```

---

## 8. serena_extract.ps1 -- LSP Extraction

Optional. Zero LLM calls. Requires `compile_commands.json` + clangd.

```
..\LocalLLM_Pipeline\LocalLLMAnalysis\serena_extract.ps1
    [-TargetDir <string>]  [-Preset <string>]  [-Workers <int>]  [-Jobs <int>]
    [-Force]  [-SkipRefs]  [-Compress]  [-ClangdPath <string>]
```

| Parameter      | Default    | Description                                       |
|----------------|------------|---------------------------------------------------|
| `-TargetDir`   | `"."`      | Subdirectory to scan                              |
| `-Preset`      | `""`       | Engine preset                                     |
| `-Workers`     | `0`        | clangd processes (`0` = auto based on free RAM)   |
| `-Jobs`        | `2`        | Threads per clangd process                        |
| `-Force`       | off        | Re-extract all files                              |
| `-SkipRefs`    | off        | Skip reference queries (faster)                   |
| `-Compress`    | off        | Collapse classes, keep top-10 functions only      |
| `-ClangdPath`  | `"clangd"` | Path to clangd binary                            |

Output: `LocalLLMAnalysis/.serena_context/<path>.serena_context.txt`.

---

## 9. generate_compile_commands.py -- Compilation Database

Generates `compile_commands.json` for clangd. Discovers whichever build artifacts are present under the repo root and uses them, in this order:

1. **Pre-existing `compile_commands.json`** anywhere under the root (e.g. from a prior `cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON` run in `build/`) -- copied verbatim.
2. **Native Visual Studio / Visual C++ project files**, parsed directly:
   - `.vcxproj` (MSBuild, VS 2010+)
   - `.vcproj`  (VS 2002-2008, pre-MSBuild)
   - `.dsp`     (Visual C++ 6.0)
   - `.sln` files are reported for visibility; solutions contribute via the projects they reference.
3. **Delegate** to a higher-level exporter when `CMakeLists.txt`, `meson.build`, `build.ninja`, or a Bazel `WORKSPACE`/`MODULE.bazel` sits at the repo root. If the required tool (`cmake`, `meson`, `ninja`, `bazel`) is not on `PATH`, the script stops and prints the install URL. CMake and Meson both require `ninja` as the backend.

Release-style project configurations are preferred when multiple configs exist. On duplicate source paths, the more modern format wins (`.vcxproj` > `.vcproj` > `.dsp`).

```
python ..\LocalLLM_Pipeline\LocalLLMAnalysis\generate_compile_commands.py [--root <path>] [--output <path>]
```

| Parameter  | Default                      | Description          |
|------------|------------------------------|----------------------|
| `--root`   | `.` (current directory)      | Repository root      |
| `--output` | `<root>/compile_commands.json` | Output file path   |

If the output file already exists, generation is skipped -- delete it to force regeneration.

---

## 10. llm_common.ps1 -- Shared Module

Dot-sourced by all `*_local.ps1` scripts. **Do not run directly.**

| Function              | Description                                              |
|-----------------------|----------------------------------------------------------|
| `Invoke-LocalLLM`    | Calls Ollama `/v1/chat/completions` with retry (3x)     |
| `Get-LLMEndpoint`    | Builds URL from `LLM_HOST` + `LLM_PORT`                 |
| `Read-EnvFile`       | Parses `.env` key=value files                            |
| `Cfg`                | Reads config key with default fallback                   |
| `Get-SHA1`           | SHA1 hash of a file                                      |
| `Get-Preset`         | Engine preset definitions                                |
| `Get-FenceLang`      | Maps file extension to markdown fence language           |
| `Test-TrivialFile`   | Detects generated/trivial files                          |
| `Write-TrivialStub`  | Writes stub doc for trivial files                        |
| `Get-OutputBudget`   | Adaptive output budget based on file size                |
| `Truncate-Source`    | Head+tail truncation for large files                     |
| `Load-CompressedLSP` | Loads LSP context, Symbol Overview section only          |
| `Show-SimpleProgress`| Single-line progress display with rate and ETA           |

---

## 11. .env Configuration

The `.env` file lives in `LocalLLMAnalysis/` alongside the scripts (not at the repo root). Scripts read it via `$PSScriptRoot`.

### LLM Server (all in `LocalLLM_Pipeline/Common/.env`)

| Variable                | Default                         | Description                                                                                   |
|-------------------------|---------------------------------|-----------------------------------------------------------------------------------------------|
| `LLM_ENDPOINT`          | (derived from HOST+PORT)        | Full Ollama URL. When set, wins over `LLM_HOST`+`LLM_PORT`.                                   |
| `LLM_HOST`              | `192.168.1.126`                 | Ollama server IP/hostname (fallback)                                                          |
| `LLM_PORT`              | `11434`                         | Ollama server port                                                                            |
| `LLM_DEFAULT_MODEL`     | `qwen3-coder:30b`               | Universal fallback. Every role-specific key (`LLM_MODEL`, `LLM_AIDER_MODEL`, `LLM_PLANNING_MODEL`) chains to this when blank/unset. |
| `LLM_MODEL`             | blank (→ `LLM_DEFAULT_MODEL`)   | Analysis + Debug scripts. Resolved via `cfg.resolve_model` / `Get-LLMModel`.                  |
| `LLM_NUM_CTX`           | `32768`                         | Per-request context window. When > 0, routes via native `/api/chat` with `options.num_ctx`.   |
| `LLM_ANALYSIS_NUM_CTX`  | `49152`                         | Analysis-specific window. Promoted into `LLM_NUM_CTX` by Analysis scripts on startup.         |
| `LLM_TEMPERATURE`       | `0.1`                           | Sampling temperature                                                                          |
| `LLM_MAX_TOKENS`        | `800`                           | Max output tokens                                                                             |
| `LLM_TIMEOUT`           | `300`                           | Request timeout (seconds)                                                                     |

### Codebase

| Variable            | Default         | Description                   |
|---------------------|-----------------|-------------------------------|
| `PRESET`             | *(empty)*       | Engine preset                                             |
| `CODEBASE_DESC`      | *(from preset)* | Codebase description for LLM                              |
| `MAX_FILE_LINES`     | `800`           | Source truncation limit                                   |
| `SKIP_TRIVIAL`       | `1`             | Skip generated/trivial files                              |
| `MIN_TRIVIAL_LINES`  | `20`            | Trivial line count threshold                              |
| `CHUNK_THRESHOLD`    | `400`           | Overview auto-chunk threshold                             |
| `INCLUDE_EXT_REGEX`  | *(from preset)* | Override for file-extension include regex                 |
| `EXCLUDE_DIRS_REGEX` | *(from preset)* | Override for directory exclude regex                      |

### Subsections

The `.env` file can contain a `#Subsections begin` / `#Subsections end` block listing subdirectories for `Arch_Analysis_Pipeline.py` to process in sequence. Comment lines (starting with `#`) are ignored by the parser and can be used for annotations like file counts.

```env
#Subsections begin
REDALERT
# 530 files at root + 79 in WIN32LIB subdir
TIBERIANDAWN
# 307 files at root + 82 in WIN32LIB subdir
CnCTDRAMapEditor
# 257 C# files -- WinForms map editor
#Subsections end
```

Subsections are processed by `Arch_Analysis_Pipeline.py` via `-TargetDir <name>`. Scans are recursive, so nested dirs (e.g. `REDALERT/WIN32LIB/`) are picked up inside their parent subsection.

---

## 12. Presets

Defined in `llm_common.ps1`.

| Preset                                         | File Extensions                           | Description                                           |
|------------------------------------------------|-------------------------------------------|-------------------------------------------------------|
| `generals`, `cnc`, `sage`                      | `.cpp .h .hpp .c .cc .cxx .inl .inc`     | C&C: Generals/Zero Hour (SAGE) + TD/RA Remastered src |
| `quake`, `quake2`, `quake3`, `doom`, `idtech`  | `.c .cc .cpp .cxx .h .hh .hpp .inl .inc` | id Software / Quake-family |
| `unreal`, `ue4`, `ue5`                         | `.cpp .h .hpp .cc .cxx .inl .cs`         | Unreal Engine              |
| `godot`                                        | `.cpp .h .gd .tscn .tres .cs`            | Godot                      |
| `unity`                                        | `.cs .shader .hlsl .cpp .h`              | Unity                      |
| `source`, `valve`                              | `.cpp .h .c .cc .cxx .inl .vpc`          | Source Engine              |
| `rust`                                         | `.rs .toml`                              | Rust engines               |

---

## 13. Common Workflows

### Full Pipeline

```powershell
..\LocalLLM_Pipeline\LocalLLMAnalysis\archgen_local.ps1 -Preset cnc
..\LocalLLM_Pipeline\LocalLLMAnalysis\archxref.ps1
..\LocalLLM_Pipeline\LocalLLMAnalysis\archgraph.ps1
..\LocalLLM_Pipeline\LocalLLMAnalysis\arch_overview_local.ps1
..\LocalLLM_Pipeline\LocalLLMAnalysis\archpass2_context.ps1
..\LocalLLM_Pipeline\LocalLLMAnalysis\archpass2_local.ps1 -Top 100
```

### Single Subsystem

```powershell
..\LocalLLM_Pipeline\LocalLLMAnalysis\archgen_local.ps1 -TargetDir REDALERT -Preset cnc
..\LocalLLM_Pipeline\LocalLLMAnalysis\archxref.ps1
..\LocalLLM_Pipeline\LocalLLMAnalysis\arch_overview_local.ps1
```

### With LSP Extraction

```powershell
python ..\LocalLLM_Pipeline\LocalLLMAnalysis\generate_compile_commands.py
..\LocalLLM_Pipeline\LocalLLMAnalysis\serena_extract.ps1 -Preset cnc
..\LocalLLM_Pipeline\LocalLLMAnalysis\archgen_local.ps1 -Preset cnc
```

### Resume After Interruption

```powershell
..\LocalLLM_Pipeline\LocalLLMAnalysis\archgen_local.ps1 -Preset cnc    # skips completed files
```

### Clean Start

```powershell
..\LocalLLM_Pipeline\LocalLLMAnalysis\archgen_local.ps1 -Clean
..\LocalLLM_Pipeline\LocalLLMAnalysis\archpass2_local.ps1 -Clean
```
