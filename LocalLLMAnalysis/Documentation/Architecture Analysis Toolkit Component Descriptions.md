# Architecture Analysis Toolkit -- Component Descriptions

## Project Overview

Architecture documentation toolkit for codebases of any language with a preset (Python, C/C++, C#, Rust, etc.). Generates per-file and subsystem-level architecture docs using a local Ollama LLM server, with optional LSP-powered semantic analysis via clangd.

**LLM Server:** Ollama at `LLM_HOST:LLM_PORT` (default `192.168.1.126:11434`), shared with the Debug and Coding pipelines.
**Model:** `qwen3-coder:30b` at per-request `num_ctx=49152` (configurable via `LLM_MODEL` + `LLM_ANALYSIS_NUM_CTX` in `LocalLLM_Pipeline/Common/.env`).

## Directory Layout

```
repo root/
  compile_commands.json         Generated compilation database (for clangd, optional)
  architecture/                 Generated output (docs, xref, diagrams). Renamed to "N. <subsection>"
                                after each subsection completes.
  LocalLLM_Pipeline/
    Common/
      .env                      Shared config for Analysis, Debug, and Coding pipelines
      llm_common.ps1            Shared helper module (Invoke-LocalLLM, Read-EnvFile, ...)
    LocalLLMAnalysis/           Toolkit scripts, prompts, and documentation
    LocalLLMDebug/              Debug pipeline (reads Analysis outputs via ARCHITECTURE_DIR)
    LocalLLMCoding/             Claude-assisted planning pipeline
```

Scripts are run from your target project's root (e.g. `cd C:\Coding\nmon`), where the toolkit is a sibling directory: `python ..\LocalLLM_Pipeline\LocalLLMAnalysis\Arch_Analysis_Pipeline.py`.
The `.env` file and `llm_common.ps1` live in `..\LocalLLM_Pipeline\Common\` and are shared with every other pipeline. Output goes to `architecture/` at the target project's root.

## Pipeline Order

```
0 (free)   ..\LocalLLM_Pipeline\LocalLLMAnalysis\serena_extract.ps1          LSP symbol data via clangd (optional)
1          ..\LocalLLM_Pipeline\LocalLLMAnalysis\archgen_local.ps1            Per-file .md docs
2 (free)   ..\LocalLLM_Pipeline\LocalLLMAnalysis\archxref.ps1                Cross-reference index
3 (free)   ..\LocalLLM_Pipeline\LocalLLMAnalysis\archgraph.ps1               Mermaid call graph diagrams
4          ..\LocalLLM_Pipeline\LocalLLMAnalysis\arch_overview_local.ps1      Subsystem architecture overview
4b (free)  ..\LocalLLM_Pipeline\LocalLLMAnalysis\archpass2_context.ps1        Per-file targeted context
5          ..\LocalLLM_Pipeline\LocalLLMAnalysis\archpass2_local.ps1          Selective re-analysis
```

## Key Configuration (`LocalLLM_Pipeline/Common/.env`)

- `LLM_ENDPOINT` (or `LLM_HOST` + `LLM_PORT`) -- Ollama server address (default `http://192.168.1.126:11434`)
- `LLM_MODEL` -- model name (default `qwen3-coder:30b`)
- `LLM_NUM_CTX` -- per-request context window (default `32768`). When > 0, `Invoke-LocalLLM` routes through Ollama's native `/api/chat`.
- `LLM_ANALYSIS_NUM_CTX` -- Analysis-specific window (default `49152`). The three LLM-calling Analysis scripts promote this value into `LLM_NUM_CTX` at load time so Invoke-LocalLLM picks it up without callsite changes.
- `LLM_TEMPERATURE`, `LLM_MAX_TOKENS`, `LLM_TIMEOUT` -- inference parameters
- `PRESET` -- `python`, `generals`/`cnc`/`sage`, `quake`, `unreal`, `godot`, `unity`, `source`, `rust`
- `INCLUDE_EXT_REGEX` / `EXCLUDE_DIRS_REGEX` -- preset overrides
- `MAX_FILE_LINES=800` -- source truncation
- `SKIP_TRIVIAL=1` -- skip generated/trivial files with stub docs
- `#Subsections begin` / `#Subsections end` block -- lists subdirectories for `Arch_Analysis_Pipeline.py` to process in sequence. Comment lines are ignored.
- `ARCHITECTURE_DIR` and `SERENA_CONTEXT_DIR` -- consumed by the Debug pipeline to optionally read Analysis outputs (see DebugWorkflow integration section).

## Architecture

- All scripts and prompts live in `LocalLLMAnalysis/`
- LLM-calling scripts (`archgen_local.ps1`, `arch_overview_local.ps1`, `archpass2_local.ps1`) dot-source `../Common/llm_common.ps1`
- Non-LLM scripts (`archxref.ps1`, `archgraph.ps1`, `archpass2_context.ps1`) define their own inline `Read-EnvFile` -- no shared dot-source needed
- Prompt `.txt` files are loaded from `$PSScriptRoot` (alongside their scripts)
- `.env` is read from `$PSScriptRoot/../Common/.env` (the shared config)
- Single-threaded LLM processing (GPU handles one inference at a time)
- `Arch_Analysis_Pipeline.py` orchestrates the six-step pipeline per subsection; `get_repo_root` returns `Path.cwd()` — **run the script from the root of the codebase you want to analyse** (e.g. `cd C:\Coding\nmon` then `python ...\LocalLLM_Pipeline\LocalLLMAnalysis\Arch_Analysis_Pipeline.py`). Subprocess cwd is inherited and per-script paths (`src/`, `tests/`, etc.) resolve there.

## clangd / LSP (Optional)

- Generate `compile_commands.json` via: `python ..\LocalLLM_Pipeline\LocalLLMAnalysis\generate_compile_commands.py`
- The generator discovers build artifacts in this order: (1) an existing `compile_commands.json`, (2) `.vcxproj` / `.vcproj` / `.dsp`, (3) delegate to `cmake` / `meson` / `ninja` / `bazel` if their config files are at the root (stops and prints an install URL if the required tool is missing).
- For this repo it parses `REDALERT/RedAlert.vcxproj` and `TIBERIANDAWN/TiberianDawn.vcxproj` directly (486 total translation units). The `CnCTDRAMapEditor/` is C# and is skipped (clangd is C++-only).
- clangd on VS Community Edition: `"C:\Program Files\Microsoft Visual Studio\18\Community\VC\Tools\Llvm\x64\bin\clangd.exe"`
- No separate index-building step needed -- `serena_extract.ps1` spawns clangd which builds the index on-the-fly (first run is slower)
- Index cached at `.cache/clangd/index/` for faster subsequent runs
- `serena_extract.ps1` produces `.serena_context.txt` files used by `archgen_local.ps1`

## Documentation Files (in `LocalLLMAnalysis/`)

- `Architecture Analysis Toolkit - Setup & Usage Guide.md` -- Setup and usage guide
- `CLI Instructions Reference.md` -- CLI reference for every script
- `Architecture Analysis Toolkit - Quickstart.md` -- Condensed reference
- `FileReference.md` -- Index of all files
- `Optimizations.md` -- Context optimization strategies
- `Serena Toolkit Documentation.md` -- LSP / clangd extraction internals
- `Architecture Analysis Toolkit Ochestration.md` -- `Arch_Analysis_Pipeline.py` reference

## Presets

Defined in `llm_common.ps1`. Use `-Preset` flag or `PRESET` in `.env`.

| Preset                       | Description                                                        |
|------------------------------|--------------------------------------------------------------------|
| `generals` / `cnc` / `sage` | C&C codebases: Generals/Zero Hour (SAGE) and TD/RA Remastered src |
| `quake` / `doom` / `idtech` | id Software / Quake-family                                         |
| `unreal` / `ue4` / `ue5`   | Unreal Engine                      |
| `godot`                      | Godot (C++/GDScript/C#)           |
| `unity`                      | Unity (C#/shaders)                 |
| `source` / `valve`          | Source Engine                      |
| `rust`                       | Rust engines (Bevy, etc.)          |
