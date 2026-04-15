# Architecture Analysis Toolkit -- Quickstart

Generate architecture documentation for a codebase using a local Ollama LLM + optional clangd LSP.

**Layout.** The toolkit lives at `C:\Coding\LocalLLM_Pipeline\` (or wherever you cloned it) as a **sibling of your target project**, e.g. `C:\Coding\nmon\`. Invoke scripts **from your target project's root** (`cd C:\Coding\nmon`); paths like `..\LocalLLM_Pipeline\LocalLLMAnalysis\<script>` reach the toolkit. Shared config at `..\LocalLLM_Pipeline\Common\.env` is used by the Analysis, Debug, and Coding pipelines.

---

## Setup

### 1. Configure `LocalLLM_Pipeline/Common/.env`

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

# Preset overrides (INCLUDE / EXCLUDE regexes if the preset's defaults
# don't match your project)
INCLUDE_EXT_REGEX=\.(py|toml)$
EXCLUDE_DIRS_REGEX=[/\\](\.git|architecture|__pycache__|\.venv|venv|dist|build)([/\\]|$)

#Subsections begin
src
#Subsections end
```

### 2. Run the pipeline

```powershell
python ..\LocalLLM_Pipeline\LocalLLMAnalysis\Arch_Analysis_Pipeline.py
```

Or run each stage manually (from your target project root):

```powershell
..\LocalLLM_Pipeline\LocalLLMAnalysis\archgen_local.ps1
..\LocalLLM_Pipeline\LocalLLMAnalysis\archxref.ps1
..\LocalLLM_Pipeline\LocalLLMAnalysis\archgraph.ps1
..\LocalLLM_Pipeline\LocalLLMAnalysis\arch_overview_local.ps1
..\LocalLLM_Pipeline\LocalLLMAnalysis\archpass2_context.ps1
..\LocalLLM_Pipeline\LocalLLMAnalysis\archpass2_local.ps1 -Top 100
```

---

## With LSP Extraction (Optional)

Requires clangd in PATH. Generate `compile_commands.json` first -- the generator discovers whatever build artifacts are present (existing `compile_commands.json`, then `.vcxproj`/`.vcproj`/`.dsp`, falling back to `cmake`/`meson`/`ninja`/`bazel` if their config files sit at the repo root):

```powershell
python ..\LocalLLM_Pipeline\LocalLLMAnalysis\generate_compile_commands.py
```

For this repo it parses `REDALERT/RedAlert.vcxproj` and `TIBERIANDAWN/TiberianDawn.vcxproj` directly (486 TU entries). The C# map editor is skipped -- clangd is C++-only.

If clangd is not in PATH (VS Community Edition):
```powershell
$env:PATH += ";C:\Program Files\Microsoft Visual Studio\18\Community\VC\Tools\Llvm\x64\bin"
```

Then extract (clangd builds its index on-the-fly during first run):

```powershell
..\LocalLLM_Pipeline\LocalLLMAnalysis\serena_extract.ps1 -Preset cnc       # First run is slower (builds index)
..\LocalLLM_Pipeline\LocalLLMAnalysis\archgen_local.ps1 -Preset cnc        # Auto-injects LSP symbols
```

---

## Pipeline

Run in order. Steps marked *free* make zero LLM calls.

```
0 (free)   ..\LocalLLM_Pipeline\LocalLLMAnalysis\serena_extract.ps1          LSP symbols (optional)
1          ..\LocalLLM_Pipeline\LocalLLMAnalysis\archgen_local.ps1            Per-file .md docs
2 (free)   ..\LocalLLM_Pipeline\LocalLLMAnalysis\archxref.ps1                Cross-reference index
3 (free)   ..\LocalLLM_Pipeline\LocalLLMAnalysis\archgraph.ps1               Mermaid diagrams
4          ..\LocalLLM_Pipeline\LocalLLMAnalysis\arch_overview_local.ps1      Architecture overview
4b (free)  ..\LocalLLM_Pipeline\LocalLLMAnalysis\archpass2_context.ps1        Targeted context
5          ..\LocalLLM_Pipeline\LocalLLMAnalysis\archpass2_local.ps1          Pass 2 enrichment (selective)
```

---

## Script Reference

### archgen_local.ps1

| Option       | Default       | Description              |
|--------------|---------------|--------------------------|
| `-TargetDir` | `.`           | Subdirectory to scan     |
| `-Preset`    | *(from .env)* | Engine preset            |
| `-Clean`     | off           | Remove output and restart|
| `-EnvFile`   | `.env`        | Config file path         |

### arch_overview_local.ps1

| Option       | Default | Description                                   |
|--------------|---------|-----------------------------------------------|
| `-TargetDir` | `all`   | Subdirectory scope                            |
| `-Single`    | off     | Force single-pass mode (small codebases only) |
| `-Full`      | off     | Force full regeneration                       |
| `-Clean`     | off     | Remove previous overview                      |

### archpass2_local.ps1

| Option       | Default       | Description                           |
|--------------|---------------|---------------------------------------|
| `-TargetDir` | `.`           | Subdirectory scope                    |
| `-Clean`     | off           | Remove Pass 2 output and restart      |
| `-Only`      | *(empty)*     | Comma-separated file paths to process |
| `-Top`       | `0` (all)     | Only process N highest-scoring files  |
| `-ScoreOnly` | off           | Print scores without running          |

### archxref.ps1 / archgraph.ps1 / archpass2_context.ps1

No LLM calls. See `Instructions.md` for full parameters.

---

## Presets

| Preset                       | Languages              | Description                                          |
|------------------------------|------------------------|------------------------------------------------------|
| `generals` / `cnc` / `sage` | `.cpp .h .c .inl .inc` | C&C: Generals/Zero Hour + TD/RA Remastered src       |
| `quake` / `doom` / `idtech` | `.c .h .cpp .inl .inc` | id Software / Quake-family |
| `unreal` / `ue4` / `ue5`   | `.cpp .h .hpp .cs`     | Unreal Engine              |
| `godot`                      | `.cpp .h .gd .cs`     | Godot                      |
| `unity`                      | `.cs .shader .hlsl`   | Unity                      |
| `source` / `valve`          | `.cpp .h .c .vpc`     | Source Engine              |
| `rust`                       | `.rs .toml`           | Rust engines               |

---

## Output

```
architecture/
  <path>.md              Pass 1 doc
  <path>.pass2.md        Pass 2 doc
  xref_index.md          Cross-references
  architecture.md        Overview
  callgraph.md           Mermaid diagrams
  .pass2_context/        Targeted Pass 2 context
  .archgen_state/        Pass 1 state
  .pass2_state/          Pass 2 state

LocalLLMAnalysis/
  .serena_context/       LSP extraction output (optional)
```

---

## Resumability

All scripts are incremental. Interrupt with Ctrl+C and re-run to continue.
