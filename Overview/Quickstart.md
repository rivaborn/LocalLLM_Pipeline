# LocalLLM Pipeline — Quickstart

A single reference for all three pipelines: **Analysis**, **Debug**, and **Coding**. Covers invocation patterns, every CLI option, and end-to-end examples.

---

## 1. Layout and Invocation Pattern

```
C:\Coding\
├── LocalLLM_Pipeline\             TOOLKIT (this repo)
│   ├── Common\
│   │   ├── .env                   Shared config for every pipeline
│   │   └── llm_common.ps1         Shared helper module
│   ├── LocalLLMAnalysis\          Per-file + subsystem architecture docs
│   ├── LocalLLMDebug\             Data flow / interfaces / bug hunt / test gaps
│   ├── LocalLLMCoding\            Claude-assisted planning + aider automation
│   └── Overview\                  (this folder)
│
├── nmon\                          Example target project (source code)
│   ├── src\
│   ├── tests\
│   └── pyproject.toml
│
└── nmonLocalLLM\                  Project-specific data for Coding pipeline
    ├── Implemented Plans\         Past architecture plans + bug-fix archives
    ├── LocalLLMCodePrompts\       Current InitialPrompt.md, aidercommands.md, …
    └── 1. src\                    Completed Analysis output (renamed)
```

**The toolkit is separate from every project it operates on.** Different pipelines want different cwd:

| Pipeline | cwd when invoking | Why |
|---|---|---|
| Analysis | target project root (e.g. `C:\Coding\nmon`) | Scans `src/` via `Path.cwd()` |
| Debug | target project root (e.g. `C:\Coding\nmon`) | `git rev-parse --show-toplevel` from cwd |
| Coding | where you want `Implemented Plans/` + `LocalLLMCodePrompts/` to live (e.g. `C:\Coding\nmonLocalLLM`) | `$ProjectRoot = (Get-Location).Path` |

Every toolkit path is then `..\LocalLLM_Pipeline\LocalLLM<X>\<script>`.

---

## 2. Shared Config — `..\LocalLLM_Pipeline\Common\.env`

One file drives every pipeline. Key knobs:

| Key | Default | Used by |
|---|---|---|
| `LLM_ENDPOINT` or `LLM_HOST`+`LLM_PORT` | `192.168.1.126:11434` | All LLM calls |
| `LLM_MODEL` | `qwen3-coder:30b` | Analysis + Debug |
| `LLM_NUM_CTX` | `32768` | Debug (Analysis overrides to 49152) |
| `LLM_ANALYSIS_NUM_CTX` | `49152` | Promoted into `LLM_NUM_CTX` by Analysis scripts |
| `LLM_MODEL_HIGH_CTX` | `qwen3-coder:30b` | Legacy; same tag now |
| `LLM_PLANNING_MODEL` | `gemma4:26b` | Coding stages 2a/2b/3a/3b when `-Local` |
| `LLM_PLANNING_NUM_CTX` | `24576` | Same |
| `LLM_AIDER_MODEL` | `qwen3.5:27b` | `run_aider.py --local` |
| `LLM_AIDER_NUM_CTX` | `40960` | Same |
| `LLM_TEMPERATURE` | `0.1` | All |
| `LLM_TIMEOUT` | `300` seconds | All |
| `PRESET` | `python` | File inclusion regex |
| `CODEBASE_DESC` | (long string) | Injected into system prompts |
| `MAX_FILE_LINES` | `800` | Source truncation |
| `ARCHITECTURE_DIR` | `architecture` | Debug reads Analysis artifacts from here |
| `SERENA_CONTEXT_DIR` | `.serena_context` | Debug reads LSP context from here |

See the file itself for the complete list including the `#Subsections begin`/`#Subsections end` block consumed by `Arch_Analysis_Pipeline.py`.

---

## 3. Analysis Pipeline

Generates per-file and subsystem architecture documentation, plus cross-reference indexes and call graphs. Run from your **target project's root** (e.g. `cd C:\Coding\nmon`).

### 3.1 Orchestrator — `Arch_Analysis_Pipeline.py`

Runs the six-step pipeline per subsection listed in `Common/.env`, then renames `architecture/` to `N. <subsection>`.

```powershell
python ..\LocalLLM_Pipeline\LocalLLMAnalysis\Arch_Analysis_Pipeline.py [options]
```

| Option | Description |
|---|---|
| `--dry-run` | Print the planned commands without executing. |
| `--start-from N` | Skip the first N-1 subsections (1-based). |
| `--skip-lsp` | Omit `generate_compile_commands.py` + `serena_extract.ps1` (use for Python codebases where LSP is not available). |

**Typical runs:**
```powershell
cd C:\Coding\nmon

# Full pipeline over all subsections
python ..\LocalLLM_Pipeline\LocalLLMAnalysis\Arch_Analysis_Pipeline.py --skip-lsp

# Dry run to preview
python ..\LocalLLM_Pipeline\LocalLLMAnalysis\Arch_Analysis_Pipeline.py --dry-run

# Resume after an interruption at subsection 3
python ..\LocalLLM_Pipeline\LocalLLMAnalysis\Arch_Analysis_Pipeline.py --start-from 3
```

### 3.2 Individual stage scripts

All accept `-EnvFile <path>` to override the default `..\Common\.env`. All Analysis LLM scripts promote `LLM_ANALYSIS_NUM_CTX` → `LLM_NUM_CTX` on startup, so every call runs at the 49152-token Analysis window.

#### `archgen_local.ps1` — Per-file documentation (LLM)
```powershell
..\LocalLLM_Pipeline\LocalLLMAnalysis\archgen_local.ps1 [-TargetDir <path>] [-Preset <name>] [-Clean] [-NoHeaders] [-EnvFile <path>] [-Test]
```
| Option | Default | Description |
|---|---|---|
| `-TargetDir <path>` | `.` | Restrict analysis to a subdirectory. |
| `-Preset <name>` | (from `.env`) | Override `PRESET` (e.g. `python`, `cnc`, `unreal`). |
| `-Clean` | off | Delete all architecture docs + SHA1 state and re-run from scratch. |
| `-NoHeaders` | off | Omit the metadata header block from each generated .md. |
| `-Test` | off | Run internal unit tests and exit. |

#### `archxref.ps1` — Cross-reference index (no LLM)
```powershell
..\LocalLLM_Pipeline\LocalLLMAnalysis\archxref.ps1 [-TargetDir <path>] [-EnvFile <path>] [-Test]
```
Parses Pass 1 docs into `architecture/xref_index.md`. Fast; no LLM calls.

#### `archgraph.ps1` — Mermaid call graph (no LLM)
```powershell
..\LocalLLM_Pipeline\LocalLLMAnalysis\archgraph.ps1 [-TargetDir <path>] [-MaxCallEdges <n>] [-MinCallSignificance <n>] [-EnvFile <path>] [-Test]
```
| Option | Default | Description |
|---|---|---|
| `-MaxCallEdges <n>` | `150` | Cap edges in the aggregate call graph. |
| `-MinCallSignificance <n>` | `2` | Minimum call count to include an edge. |

#### `arch_overview_local.ps1` — Subsystem overview (LLM)
```powershell
..\LocalLLM_Pipeline\LocalLLMAnalysis\arch_overview_local.ps1 [-TargetDir <path>] [-Single] [-Clean] [-Full] [-EnvFile <path>]
```
| Option | Default | Description |
|---|---|---|
| `-TargetDir <path>` | `all` | Restrict to a subdirectory; `all` means the whole Pass 1 output. |
| `-Single` | off | Skip chunking; feed everything in one call (only for small codebases). |
| `-Full` | off | Include full per-file docs in the synth prompt (richer, larger). |
| `-Clean` | off | Delete existing `architecture.md` first. |

#### `archpass2_context.ps1` — Targeted context extraction (no LLM)
```powershell
..\LocalLLM_Pipeline\LocalLLMAnalysis\archpass2_context.ps1 [-TargetDir <path>] [-EnvFile <path>] [-Test]
```
Extracts per-file targeted context blocks from the overview and xref into `architecture/.pass2_context/<rel>.ctx.txt`.

#### `archpass2_local.ps1` — Selective Pass 2 re-analysis (LLM)
```powershell
..\LocalLLM_Pipeline\LocalLLMAnalysis\archpass2_local.ps1 [-TargetDir <path>] [-Clean] [-Only <rel>] [-Top <n>] [-ScoreOnly] [-EnvFile <path>]
```
| Option | Default | Description |
|---|---|---|
| `-Only <rel>` | — | Process exactly one file (e.g. `src/nmon/storage.py`). |
| `-Top <n>` | `0` | Process only the top-N most complex/cross-cutting files. |
| `-ScoreOnly` | off | Compute the complexity/cross-cutting score and list candidates; no LLM calls. |

#### `serena_extract.ps1` — LSP context extraction (no LLM; optional)
```powershell
..\LocalLLM_Pipeline\LocalLLMAnalysis\serena_extract.ps1 [-TargetDir <path>] [-Preset <name>] [-Jobs <n>] [-Workers <n>] [-Force] [-SkipRefs] [-Compress] [-MinFreeRAM <gb>] [-RAMPerWorker <gb>] [-ClangdPath <path>] [-EnvFile <path>] [-Test]
```
| Option | Default | Description |
|---|---|---|
| `-Jobs <n>` | `2` | Parallel clangd processes. |
| `-Workers <n>` | `0` (auto) | Threads per process; 0 = auto-scale by RAM. |
| `-Force` | off | Re-extract even for files with cached outputs. |
| `-SkipRefs` | off | Skip cross-file references (faster, smaller output). |
| `-Compress` | off | Compress outputs (reduces token count in Debug integration). |
| `-MinFreeRAM <gb>` | `6.0` | Don't start new workers below this free-RAM threshold. |
| `-RAMPerWorker <gb>` | `5.0` | RAM budget per worker, used for auto-scaling. |
| `-ClangdPath <path>` | `clangd` | Full path to clangd executable if not on `PATH`. |

Requires `compile_commands.json` at the repo root. Generate with `python ..\LocalLLM_Pipeline\LocalLLMAnalysis\generate_compile_commands.py`.

### 3.3 Analysis output

```
architecture/
├── *.md                         Per-file docs (one per source file)
├── *.pass2.md                   Pass 2 re-analysis for selected files
├── xref_index.md                Cross-reference index
├── callgraph.mermaid            Mermaid call graph (rendered)
├── callgraph.md                 Call graph with Mermaid + prose
├── subsystems.mermaid           Subsystem dependency graph
├── architecture.md              Synthesized subsystem overview
└── .pass2_context/              Per-file targeted context
```

After `Arch_Analysis_Pipeline.py` completes a subsection, `architecture/` is renamed to `N. <subsection>`. If you want the Debug pipeline to consume this output, set `ARCHITECTURE_DIR=N. <subsection>` in `Common/.env`.

---

## 4. Debug Pipeline

Runs local-LLM analyses (data flow, interface contracts, test gaps, bug hunts) and optionally hands off to Claude Code to apply bug fixes. Run from your **target project's root**.

### 4.1 Orchestrator — `Arch_Debug_Pipeline.ps1`

Runs all four analysis scripts → calls Claude Code per file to apply fixes → archives a change log to `Implemented Plans/`.

```powershell
..\LocalLLM_Pipeline\LocalLLMDebug\Arch_Debug_Pipeline.ps1 -TargetDir <path> [options]
```

| Option | Default | Description |
|---|---|---|
| `-TargetDir <path>` | (required) | Source directory to analyse (e.g. `src/nmon`). |
| `-TestDir <path>` | `tests` | Test directory for test-gap analysis. |
| `-Claude <account>` | `Claude1` | Claude account (`Claude1` → `.clauderivalon`, `Claude2` → `.claudefksogbetun`). |
| `-Model <name>` | (auto) | Override Claude model for all files. Auto: **Opus** for files with real bugs, **Sonnet** for clean ones. |
| `-Ultrathink` | off | Force ultrathink for every file. |
| `-NoUltrathink` | off | Disable ultrathink everywhere. |
| `-EnvFile <path>` | (Common default) | Override `.env` location. |
| `-Restart` | off | Ignore `.debug_progress` and start from step 1. |
| `-DryRun` | off | Preview every step without running. |

**Typical runs:**
```powershell
cd C:\Coding\nmon

# Full debug pipeline
..\LocalLLM_Pipeline\LocalLLMDebug\Arch_Debug_Pipeline.ps1 -TargetDir src/nmon

# Force Opus + ultrathink on all files (budget-insensitive mode)
..\LocalLLM_Pipeline\LocalLLMDebug\Arch_Debug_Pipeline.ps1 -TargetDir src/nmon -Model opus -Ultrathink

# Budget mode: Sonnet on everything, no ultrathink
..\LocalLLM_Pipeline\LocalLLMDebug\Arch_Debug_Pipeline.ps1 -TargetDir src/nmon -Model sonnet -NoUltrathink

# Second Claude account
..\LocalLLM_Pipeline\LocalLLMDebug\Arch_Debug_Pipeline.ps1 -TargetDir src/nmon -Claude Claude2

# Preview, no actual calls
..\LocalLLM_Pipeline\LocalLLMDebug\Arch_Debug_Pipeline.ps1 -TargetDir src/nmon -DryRun

# Start over (wipes .debug_progress)
..\LocalLLM_Pipeline\LocalLLMDebug\Arch_Debug_Pipeline.ps1 -TargetDir src/nmon -Restart
```

### 4.2 Individual stage scripts

All standalone; run directly if you only want one analysis.

#### `dataflow_local.ps1` — Data flow trace (LLM)
```powershell
..\LocalLLM_Pipeline\LocalLLMDebug\dataflow_local.ps1 [-TargetDir <path>] [-Clean] [-EnvFile <path>]
```
Writes `architecture/DATA_FLOW.md` plus per-file extractions. Synth pass optionally injects `architecture.md` from Analysis if `ARCHITECTURE_DIR` is set.

#### `interfaces_local.ps1` — Interface contracts (LLM)
```powershell
..\LocalLLM_Pipeline\LocalLLMDebug\interfaces_local.ps1 [-TargetDir <path>] [-Clean] [-EnvFile <path>]
```
Writes `architecture/INTERFACES.md` + per-file `.iface.md`. Injects serena LSP context when `SERENA_CONTEXT_DIR` is set (run `-Clean` to re-extract with LSP context if cache is stale).

#### `testgap_local.ps1` — Test gap analysis (LLM)
```powershell
..\LocalLLM_Pipeline\LocalLLMDebug\testgap_local.ps1 [-SrcDir <path>] [-TestDir <path>] [-Clean] [-EnvFile <path>]
```
| Option | Default | Description |
|---|---|---|
| `-SrcDir <path>` | `src` | Source root. |
| `-TestDir <path>` | `tests` | Test root. |

Writes `test_gaps/GAP_REPORT.md` + per-file `.gap.md`.

#### `bughunt_local.ps1` — Single-pass bug scan (LLM)
```powershell
..\LocalLLM_Pipeline\LocalLLMDebug\bughunt_local.ps1 [-TargetDir <path>] [-Clean] [-Force] [-EnvFile <path>]
```
| Option | Default | Description |
|---|---|---|
| `-Clean` | off | Delete all reports + SHA1 state. |
| `-Force` | off | Ignore SHA1 cache; re-scan every file. |

Writes `bug_reports/SUMMARY.md` + per-file reports. Injects `xref_index.md` from Analysis when `ARCHITECTURE_DIR` is set, giving the model cross-file (caller/callee) awareness.

#### `bughunt_iterative_local.ps1` — Iterative analyze-and-fix loop (LLM)
```powershell
..\LocalLLM_Pipeline\LocalLLMDebug\bughunt_iterative_local.ps1 [-TargetDir <path>] [-TestDir <path>] [-MaxIterations <n>] [-ApplyFixes] [-SkipBugs] [-SkipDataflow] [-SkipContracts] [-SkipTests] [-Clean] [-Force] [-EnvFile <path>]
```
| Option | Default | Description |
|---|---|---|
| `-MaxIterations <n>` | `3` | Maximum fix-and-review iterations per file. |
| `-ApplyFixes` | off | Write fixes back to source. Without this, fixes stage in `bug_fixes/`. |
| `-SkipBugs` | off | Disable the bug-analysis pass. |
| `-SkipDataflow` | off | Disable the data-flow analysis pass. |
| `-SkipContracts` | off | Disable the contract-analysis pass. |
| `-SkipTests` | off | Disable the test-quality loop. |

Has convergence guards (`BUGHUNT_BLOAT_RATIO`, `BUGHUNT_DIVERGE_AFTER`) configurable in `.env` — aborts on bloat or divergence so bad runs can never leave a file worse than it started.

### 4.3 Debug output

```
architecture/
├── DATA_FLOW.md
├── INTERFACES.md
└── interfaces/<rel>.iface.md

bug_reports/
├── SUMMARY.md
└── src/<rel>.md

bug_fixes/ (iterative only)
└── src/<rel>.iter_log.md

test_gaps/
├── GAP_REPORT.md
└── src/<rel>.gap.md

Implemented Plans/Bug Fix Changes <N>.md   (after Arch_Debug_Pipeline step 6)
```

---

## 5. Coding Pipeline

Uses Claude to produce an architecture plan + aider commands, then runs aider against a local LLM to implement the plan. Run from your **project-data folder** (e.g. `cd C:\Coding\nmonLocalLLM`) — `Implemented Plans/` and `LocalLLMCodePrompts/` are resolved relative to cwd.

### 5.1 Planning — `Arch_Coding_Pipeline.ps1`

Four-stage pipeline. **By default Claude is used only for Stage 1 (prompt refinement)**; every other stage (0, 2a, 2b, 3a, 3b) runs on the local Ollama LLM. Override with `-Local` (100% local, skip Claude entirely) or `-AllClaude` (100% Claude, useful when the local server is unavailable).

```powershell
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1 [options]
```

| Option | Default | Description |
|---|---|---|
| `-TargetDir <path>` | `LocalLLMCodePrompts` (under cwd) | Folder containing `InitialPrompt.md` and where outputs are written. |
| `-Claude <account>` | `Claude1` | Claude account. |
| `-Model <name>` | per-stage defaults | Override Claude model for every Claude stage (`sonnet`/`opus`/`haiku` or full ID). Does not affect local stages. |
| `-Ultrathink` | off | Force ultrathink on for all Claude stages. |
| `-NoUltrathink` | off | Force off for all Claude stages. |
| `-Local` | off | Route **every** stage to the local Ollama LLM (no Claude at all). Mutually exclusive with `-AllClaude`. |
| `-AllClaude` | off | Route **every** stage to Claude. Mutually exclusive with `-Local`. |
| `-LocalEndpoint <url>` | (from `.env`) | Override Ollama URL for local stages. |
| `-LocalModel <tag>` | (from `.env` `LLM_PLANNING_MODEL`) | Override the local planning model. |
| `-SkipStage <1,2,3>` | — | Skip one or more stages. |
| `-FromStage <1-3>` | `1` | Start from a specific stage; skips earlier. |
| `-Restart` | off | Ignore saved `.progress`; start from Stage 1 (or `-FromStage`). |
| `-Force` | off | Overwrite outputs without confirmation. |
| `-DryRun` | off | Preview without calling any LLM. |

**Engine routing by mode:**

| Stage | default | `-Local` | `-AllClaude` |
|---|:-:|:-:|:-:|
| 0 Codebase Summary | local | local | Claude |
| **1 Improve Prompt** | **Claude** | local | Claude |
| 2a Section list | local | local | Claude |
| 2b Per-section | local | local | Claude |
| 3a Step list | local | local | Claude |
| 3b Per-step aider command | local | local | Claude |

**Per-stage Claude defaults** (only applied to Claude-routed stages; used when `-Model` not specified):

| Stage | Default Claude model | Ultrathink |
|---|---|---|
| 0 Codebase Summary | Sonnet | off |
| 1 Improve Prompt | Sonnet | off |
| 2a Section list | Opus | on |
| 2b Per-section | Opus | on |
| 3a Step list | Opus | on |
| 3b Per-step aider command | Sonnet | off |

**Typical runs:**
```powershell
cd C:\Coding\nmonLocalLLM     # where Implemented Plans/ + LocalLLMCodePrompts/ live

# Default: Claude for Stage 1 only; local LLM for everything else
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1

# Fully local (e.g. Claude rate-limited or offline)
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1 -Local

# Fully Claude (e.g. Ollama server down, or intentional cloud-only run)
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1 -AllClaude

# Local override: point Stage 1's local fallback at a different endpoint/model (only when -Local is active)
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1 -Local -LocalEndpoint http://localhost:11434 -LocalModel qwen3:32b

# Different prompts folder
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1 -TargetDir .\LocalLLMCodePrompts_V2

# Resume after interruption (automatic) — mode-mismatch guard refuses mixing modes across the same run
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1

# Ignore saved progress and start fresh
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1 -Restart

# Regenerate only aider commands from existing Architecture Plan.md
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1 -FromStage 3 -Restart

# Second Claude account; force Sonnet for every Claude stage (only affects -AllClaude or default Stage 1)
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1 -Claude Claude2 -Model sonnet

# Preview (see engine routing in the banner)
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1 -DryRun
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1 -Local -DryRun
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1 -AllClaude -DryRun
```

**Stage outputs:**
```
<TargetDir>/
├── Implementation Planning Prompt.md    (Stage 1)
├── PromptUpdates.md                     (Stage 1)
├── Architecture Plan.md                 (Stage 2; concatenated from section files)
└── aidercommands.md                     (Stage 3)

Implemented Plans/
├── Codebase Summary.md                  (Stage 0, if prior plans exist)
└── Plans/
    ├── Section 1.md                     (Stage 2b; one file per section — model never edits a consolidated doc)
    ├── Section 2.md
    └── …
```

### 5.2 Execution — `run_aider.py`

Parses `aidercommands.md` and invokes aider once per step.

```powershell
python ..\LocalLLM_Pipeline\LocalLLMCoding\run_aider.py [file] [options]
```

| Option | Default | Description |
|---|---|---|
| `file` (positional) | `aidercommands.md` in `<TargetDir>` or script dir | Markdown file to read. |
| `--from-step N` | `1` | Skip steps before N; resume here. |
| `--only-step N` | — | Run exactly one step. |
| `--model <name>` | (from `--local`) | Explicit aider model string, e.g. `ollama_chat/qwen3-coder:30b` or `gpt-4o`. Wins over `--local-model`. |
| `--local` | off | Route aider to the local Ollama server. Reads `Common/.env`, sets `OLLAMA_API_BASE`, builds `--model ollama_chat/<LLM_AIDER_MODEL>`. |
| `--local-endpoint <url>` | (from `.env`) | Override Ollama URL (implies `--local`). |
| `--local-model <tag>` | (from `.env` `LLM_AIDER_MODEL`) | Override local model (implies `--local`). Wrapped as `ollama_chat/<tag>`. |
| `--dry-run` | off | Preview steps without calling aider. |

**Endpoint precedence** (first match wins): `--local-endpoint` → `OLLAMA_API_BASE` → `LLM_ENDPOINT` env var → `LLM_ENDPOINT` in `.env` → `LLM_HOST`+`LLM_PORT` → default `http://192.168.1.126:11434`.

**Typical runs:**
```powershell
# Recommended: --local reads Common/.env and wires OLLAMA_API_BASE automatically
python ..\LocalLLM_Pipeline\LocalLLMCoding\run_aider.py --local

# Resume after failure at step 8
python ..\LocalLLM_Pipeline\LocalLLMCoding\run_aider.py --from-step 8 --local

# Re-run just step 12 (e.g. regenerate a buggy file)
python ..\LocalLLM_Pipeline\LocalLLMCoding\run_aider.py --only-step 12 --local

# Preview
python ..\LocalLLM_Pipeline\LocalLLMCoding\run_aider.py --dry-run

# Different aidercommands file
python ..\LocalLLM_Pipeline\LocalLLMCoding\run_aider.py .\LocalLLMCodePrompts_V2\aidercommands.md --local

# Use a different local model for one run
python ..\LocalLLM_Pipeline\LocalLLMCoding\run_aider.py --local --local-model qwen3-coder:30b

# Use a cloud model via aider directly
python ..\LocalLLM_Pipeline\LocalLLMCoding\run_aider.py --model gpt-4o
```

---

## 6. End-to-End Examples

### 6.1 Green-field project: plan and build nmon from scratch
```powershell
# 1. Draft initial prompt
cd C:\Coding\nmonLocalLLM
#  Edit LocalLLMCodePrompts\InitialPrompt.md describing what you want to build

# 2. Turn it into an architecture plan + aider commands
#    Default: Claude refines the prompt (Stage 1); local LLM handles the rest
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1

# 3. Execute the plan via aider + local Ollama
python ..\LocalLLM_Pipeline\LocalLLMCoding\run_aider.py --local
#  Output: source files materialise in the cwd
```

### 6.2 Feature iteration on an existing project
```powershell
# 1. Draft a new InitialPrompt describing the feature
cd C:\Coding\nmonLocalLLM
#  Edit LocalLLMCodePrompts\InitialPrompt.md (Stage 0 will summarize prior Implemented Plans automatically)

# 2. Replan — Claude refines the new feature prompt, local LLM does the rest
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1

# 3. Execute
python ..\LocalLLM_Pipeline\LocalLLMCoding\run_aider.py --local
```

### 6.2b Same iteration but Claude is rate-limited
```powershell
# Fall back to fully local; prompt refinement quality may drop but nothing blocks
cd C:\Coding\nmonLocalLLM
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1 -Local
python ..\LocalLLM_Pipeline\LocalLLMCoding\run_aider.py --local
```

### 6.3 Full debug sweep on an existing project
```powershell
cd C:\Coding\nmon

# 1. (Optional) run Analysis first for richer Debug prompts
python ..\LocalLLM_Pipeline\LocalLLMAnalysis\Arch_Analysis_Pipeline.py --skip-lsp
#  After completion, set ARCHITECTURE_DIR="1. src" in Common/.env so Debug reads the renamed folder

# 2. Debug pipeline: local analyses → Claude applies fixes → archive
..\LocalLLM_Pipeline\LocalLLMDebug\Arch_Debug_Pipeline.ps1 -TargetDir src/nmon

# 3. Changes archived to C:\Coding\nmonLocalLLM\Implemented Plans\Bug Fix Changes N.md
```

### 6.4 Just hunt bugs in one module with cross-file awareness
```powershell
cd C:\Coding\nmon

# Prereq: run Analysis once so xref_index.md exists
python ..\LocalLLM_Pipeline\LocalLLMAnalysis\Arch_Analysis_Pipeline.py --skip-lsp
#  Edit Common/.env: ARCHITECTURE_DIR=1. src

# Run bughunt against just the gpu subsystem
..\LocalLLM_Pipeline\LocalLLMDebug\bughunt_local.ps1 -TargetDir src/nmon/gpu
#  Report: bug_reports/src/nmon/gpu/*.md
```

### 6.5 Iterative auto-fix loop (experimental; writes to source when `-ApplyFixes`)
```powershell
cd C:\Coding\nmon

# Safe run: fixes stage in bug_fixes/ for review, source untouched
..\LocalLLM_Pipeline\LocalLLMDebug\bughunt_iterative_local.ps1 -TargetDir src/nmon/gpu -SkipTests

# Aggressive: apply fixes directly to source
..\LocalLLM_Pipeline\LocalLLMDebug\bughunt_iterative_local.ps1 -TargetDir src/nmon -ApplyFixes
```

---

## 7. Common Workflow Tips

- **Resumability is built in.** Every orchestrator persists progress and resumes on re-run. Use `-Restart` (PowerShell) / `--from-step N` (Python) to override.
- **SHA1 caching.** Analysis and Debug scripts skip unchanged files. Use `-Clean` (wipes cache + output) or `-Force` (wipes cache only) to invalidate.
- **Claude accounts.** `-Claude Claude1` / `-Claude Claude2` picks which `CLAUDE_CONFIG_DIR` to use (profile-based). Useful for separate billing or rate-limit mitigation.
- **-Local mode requirements.** Ollama server reachable, `LLM_AIDER_MODEL` / `LLM_PLANNING_MODEL` pulled on the server. Verify with `ollama ps` that `PROCESSOR` says `100% GPU`.
- **Windows-only.** The PowerShell scripts use Windows path separators and `msvcrt`/NVAPI patterns in examples. Python scripts (`Arch_Analysis_Pipeline.py`, `run_aider.py`) work cross-platform but most workflows assume Windows.

---

## 8. Where to Read More

| Pipeline | Documentation folder |
|---|---|
| Analysis | `..\LocalLLM_Pipeline\LocalLLMAnalysis\Documentation\` |
| Debug | `..\LocalLLM_Pipeline\LocalLLMDebug\Documentation\` |
| Coding | `..\LocalLLM_Pipeline\LocalLLMCoding\Documentation\` |

Key deep-dives:
- `LocalLLMAnalysis\Documentation\Architecture Analysis Toolkit - Setup & Usage Guide.md` — full Analysis setup
- `LocalLLMAnalysis\Documentation\CLI Instructions Reference.md` — exhaustive Analysis CLI reference
- `LocalLLMDebug\Documentation\DebugWorkflow.md` — end-to-end Debug reference with integration caveats
- `LocalLLMCoding\Documentation\Generate Aider Commands.md` — Coding pipeline deep-dive including `-Local` mode and engine routing
- `LocalLLMCoding\Documentation\run_aider.md` — model selection, resume semantics, troubleshooting
