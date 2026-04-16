# Architecture Analysis Toolkit -- File Reference

All toolkit files live in `..\LocalLLM_Pipeline\LocalLLMAnalysis\`. Scripts are run from your target project's root (e.g. `cd C:\Coding\nmon` then `..\LocalLLM_Pipeline\LocalLLMAnalysis\<script>.ps1`).

## Pipeline Scripts

### llm_common.ps1 (at `LocalLLM_Pipeline/Common/llm_common.ps1`) -- Shared Helper Module
Dot-sourced by all `*_local.ps1` scripts in Analysis, Debug, and Coding pipelines. Provides `Invoke-LocalLLM` (Ollama client with retry; routes via native `/api/chat` when `LLM_NUM_CTX > 0`), `Get-LLMEndpoint` (builds URL from `LLM_ENDPOINT` or `LLM_HOST`/`LLM_PORT`), `Resolve-ArchFile` + `Get-SerenaContextDir` (for optional Debug←Analysis integration), and shared utilities: `Read-EnvFile`, `Get-SHA1`, `Get-Preset`, `Get-FenceLang`, `Test-TrivialFile`, `Write-TrivialStub`, `Get-OutputBudget`, `Truncate-Source`, `Load-CompressedLSP`, `Show-SimpleProgress`, `Test-CancelKey`.

### archgen_local.ps1 -- Pass 1: Per-File Documentation
Generates one `.md` doc per source file via local LLM. Synchronous. Truncates source to `MAX_FILE_LINES`, loads compressed LSP symbols when available, adaptive output budget (300-800 tokens). SHA1 hash DB for resumability, trivial file skipping, progress display with ETA.

### archxref.ps1 -- Cross-Reference Index
Parses Pass 1 docs and builds cross-reference index: function-to-file mappings, call graph edges, global state ownership, header dependencies, subsystem interfaces. Pure text processing -- no LLM calls.

### archgraph.ps1 -- Call Graph & Dependency Diagrams
Generates Mermaid diagrams from Pass 1 docs: function-level call graphs grouped by subsystem, subsystem dependency diagrams. No LLM calls.

### arch_overview_local.ps1 -- Architecture Overview
Synthesizes per-file docs into subsystem-level overview. Chunks for large codebases (threshold 400 lines). Extracts only headings + purpose for token efficiency. Two-tier: per-subsystem overviews then final synthesis.

### archpass2_context.ps1 -- Targeted Pass 2 Context
Extracts relevant architecture overview paragraphs and xref entries per file. Zero LLM calls, runs in seconds.

### archpass2_local.ps1 -- Pass 2: Selective Re-Analysis
Re-analyzes files with architecture context. Scoring (`-Top N`, `-ScoreOnly`), targeted context, SHA1 hash DB. Source capped at 300 lines.

### serena_extract.ps1 -- LSP Context Extraction (Optional)
Orchestrates adaptive parallel LSP extraction via clangd. Zero LLM calls. Auto-scales workers based on RAM. Requires `compile_commands.json` + clangd index.

### serena_extract.py -- Adaptive Parallel LSP Client
Python script that spawns clangd processes via LSP JSON-RPC. Shared queue, RAM monitoring, crash recovery, incremental support, PCH cleanup.

### generate_compile_commands.py -- Compilation Database Generator
Generates `compile_commands.json` for clangd by discovering whichever build artifacts are available. Resolution order: (1) an existing `compile_commands.json` anywhere under the root is copied verbatim; (2) native Visual Studio / Visual C++ project files are parsed directly -- `.vcxproj` (MSBuild), `.vcproj` (VS 2002-2008), `.dsp` (VC6) -- extracting per-project `AdditionalIncludeDirectories`, `PreprocessorDefinitions`, and source lists; `.sln` files are reported for visibility; (3) if no native projects are found, the script delegates to `cmake` / `meson` / `ninja` / `bazel` when their config files sit at the repo root, stopping with an install URL if the required tool is not on `PATH`. On duplicate source paths, `.vcxproj` > `.vcproj` > `.dsp`.

## Prompt Files

### archgen_local_prompt.txt -- Pass 1 Prompt
Per-file analysis targeting ~400-600 token output. Bullet-point format.

### arch_overview_local_prompt.txt -- Overview Prompt
Architecture overview requesting ~800 token output.

### archpass2_local_prompt.txt -- Pass 2 Prompt
Cross-cutting enrichment targeting ~400 token output.

## Configuration

### `.env` (at `LocalLLM_Pipeline/Common/.env`, shared with Debug and Coding pipelines)
LLM settings: `LLM_ENDPOINT` (or `LLM_HOST`+`LLM_PORT`), `LLM_MODEL`, `LLM_NUM_CTX`, `LLM_ANALYSIS_NUM_CTX`, `LLM_PLANNING_MODEL`/`_NUM_CTX`, `LLM_AIDER_MODEL`/`_NUM_CTX`, `LLM_TEMPERATURE`, `LLM_MAX_TOKENS`, `LLM_TIMEOUT`. Pipeline settings: `PRESET`, `CODEBASE_DESC`, `MAX_FILE_LINES`, `SKIP_TRIVIAL`, `INCLUDE_EXT_REGEX`, `EXCLUDE_DIRS_REGEX`. Debug←Analysis integration: `ARCHITECTURE_DIR`, `SERENA_CONTEXT_DIR`. Also contains a `#Subsections begin` / `#Subsections end` block for `Arch_Analysis_Pipeline.py`.

### .clangd (at repo root, optional)
Controls clangd behavior for LSP extraction. Disables diagnostics, enables background indexing.

## Documentation (in `LocalLLMAnalysis/`)

| File                                                       | Description                                |
|------------------------------------------------------------|--------------------------------------------|
| `Architecture Analysis Toolkit - Setup & Usage Guide.md`   | Full setup and usage guide                 |
| `CLI Instructions Reference.md`                            | CLI reference for every script             |
| `Architecture Analysis Toolkit - Quickstart.md`            | Condensed reference                        |
| `Architecture Analysis Toolkit Ochestration.md`            | `Arch_Analysis_Pipeline.py` reference      |
| `Architecture Analysis Toolkit Component Descriptions.md`  | Project context for AI assistants          |
| `Optimizations.md`                                         | Context optimization strategies            |
| `Serena Toolkit Documentation.md`                          | LSP / clangd extraction reference          |
| `FilesOverview.md`                                         | One-line summary of every doc              |
| `FileReference.md`                                         | This file                                  |
| `New Codebase Instructions.md`                             | Checklist for adapting the toolkit         |

## Other Directories

| Directory                      | Description                                         |
|--------------------------------|-----------------------------------------------------|
| `architecture/`                | Generated output (docs, xref, diagrams, state). Renamed to `N. <subsection>/` after each subsection completes. |
| `.serena_context/` (at repo root) | LSP extraction output (optional)                  |
| `.cache/clangd/`               | clangd persistent index (optional)                  |
