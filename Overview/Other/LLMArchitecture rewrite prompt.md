# Local LLM Pipeline Rewrite Plan

## Goal

Rewrite the archgen documentation pipeline to use a local Ollama server (`http://192.168.1.126:11434`) with `qwen2.5-coder:14b` instead of Claude CLI. The local LLM has significantly less context window (~8K-32K tokens vs Claude's 200K) and lower capability, so every script must be redesigned to minimize per-request workload.

## Hardware & Model Constraints

| Constraint     | Value                                                                      |
|----------------|----------------------------------------------------------------------------|
| GPU            | RTX 3090 (24 GB VRAM)                                                      |
| Model          | `qwen2.5-coder:14b`                                                       |
| Context window | ~32K tokens (vs Claude's 200K)                                             |
| Throughput     | ~1 request at a time on GPU (no parallelism benefit beyond 1-2 jobs)       |
| Capability     | Good at code understanding, weaker at long-form synthesis than Claude      |
| API            | Ollama REST API (`/api/generate` or OpenAI-compatible `/v1/chat/completions`) |
| Server         | `http://192.168.1.126:11434`                                               |

## Key Design Decisions

### 1. Use OpenAI-compatible API (simplest integration)

Ollama exposes `POST /v1/chat/completions` which accepts standard `messages` format. This is the cleanest approach:

```powershell
$body = @{
    model    = "qwen2.5-coder:14b"
    messages = @(
        @{ role = "system"; content = $systemPrompt }
        @{ role = "user";   content = $userPrompt }
    )
    stream      = $false
    temperature = 0.1
} | ConvertTo-Json -Depth 5

$resp = Invoke-RestMethod -Uri "http://192.168.1.126:11434/v1/chat/completions" `
    -Method Post -ContentType "application/json" -Body $body
$output = $resp.choices[0].message.content
```

### 2. Reduce context sent per request

With ~32K tokens vs 200K, we must be aggressive about cutting input:

| Current feature                         | Action for local LLM                                        |
|-----------------------------------------|-------------------------------------------------------------|
| Full source file (up to 3000 lines)     | **Cap at 800 lines** with head+tail truncation              |
| Bundled headers (up to 8)               | **Remove entirely** -- too expensive for limited context    |
| LSP context injection                   | **Keep but compress** -- symbols only, drop references section |
| Directory context                       | **Remove** -- not worth the tokens                          |
| Shared directory headers                | **Remove**                                                  |
| Engine knowledge preamble               | **Remove**                                                  |
| Batch mode (multiple files per request) | **Remove** -- each file individually                        |
| Pattern cache / structural hashing      | **Keep** -- saves LLM calls entirely                        |
| Trivial file skipping                   | **Keep** -- saves LLM calls entirely                        |

### 3. Simplified prompts

The current prompts ask for rich structured output (~1000-1200 tokens). For local LLM:

- **New prompt**: Ask for ~400-600 token output max
- **Fewer sections**: Drop "Control Flow Notes", simplify "Key Functions" to just name + purpose + calls
- **Simpler schema**: Use flat bullet points instead of nested markdown tables
- **Explicit instruction**: "Be extremely concise. One sentence per item."

### 4. Parallelism model changes

Claude pipeline used `Start-Job` with 8 parallel workers hitting the cloud API. Local LLM on a single RTX 3090:

- **Single worker only** (GPU can only run one inference at a time)
- Remove all `Start-Job` / parallel dispatch machinery
- Run synchronously in the main script (simpler, no worker scripts needed)
- But keep the worker as a separate function for code organization

### 5. Remove Claude-specific features

| Feature                                          | Action                                          |
|--------------------------------------------------|-------------------------------------------------|
| Claude CLI invocation (`& claude -p`)            | Replace with `Invoke-RestMethod` to Ollama      |
| Claude config dirs (`CLAUDE1_CONFIG_DIR`, etc.)  | Remove                                          |
| Rate limit detection/handling                    | Remove (local server has no rate limits)        |
| Account rotation (`-Claude1` flag)               | Remove                                          |
| Prompt caching (system prompt trick)             | Remove (not applicable)                         |
| `--max-turns`, `--output-format` flags           | Remove                                          |
| `--append-system-prompt-file`                    | Replace with `messages[0].role = "system"`      |

### 6. Keep existing infrastructure

| Feature                       | Status                                        |
|-------------------------------|-----------------------------------------------|
| SHA1 hash DB for resumability | **Keep as-is**                                |
| Trivial file detection        | **Keep as-is**                                |
| Tiered model selection        | **Remove** (only one local model)             |
| Progress tracking             | **Keep but simplify** (no parallel workers)   |
| Preset system                 | **Keep as-is**                                |
| `.env` configuration          | **Keep, add new vars**                        |
| `archxref.ps1`                | **Keep as-is** (no LLM calls)                 |
| `archgraph.ps1`               | **Keep as-is** (no LLM calls)                 |
| `archpass2_context.ps1`       | **Keep as-is** (no LLM calls)                 |

---

## New `.env` Variables

```env
# Local LLM settings
LLM_ENDPOINT=http://192.168.1.126:11434
LLM_MODEL=qwen2.5-coder:14b
LLM_TEMPERATURE=0.1
LLM_MAX_TOKENS=800
LLM_TIMEOUT=120

# Reduced limits for local LLM
MAX_FILE_LINES=800
JOBS=1

#Subsections begin
REDALERT
# 530 files at root + 79 in WIN32LIB subdir
TIBERIANDAWN
# 307 files at root + 82 in WIN32LIB subdir
CnCTDRAMapEditor
# 257 C# files
#Subsections end
```

The `#Subsections begin` / `#Subsections end` block lists subdirectories for `Arch_Analysis_Pipeline.py` to process in sequence. Comment lines (e.g. `# 530 files`) are ignored by the parser.

---

## File-by-File Plan

### New file: `llm_common.ps1` -- Shared LLM helper module

Extracted shared code used by all scripts:

```
Functions:
- Invoke-LocalLLM($systemPrompt, $userPrompt, $endpoint, $model, $temperature, $maxTokens, $timeout)
  -> Returns response text or throws on error
  -> Handles Ollama connection errors with retry (3 attempts, 5s delay)
  -> Validates response is not empty

- Read-EnvFile($path) -- shared .env parser (currently duplicated in every script)
- Get-SHA1($filePath)
- Get-Preset($name)
- Get-FenceLang($file, $default)
- Test-TrivialFile($rel, $fullPath, $minLines)
- Write-TrivialStub($rel, $outPath)
```

This eliminates the massive code duplication across scripts.

### `archgen_local.ps1` -- Pass 1: Per-File Documentation (rewrite of archgen.ps1)

**Changes from original:**

1. **Remove**: `Start-Job` parallel dispatch, worker script, batch mode, Claude CLI invocation, rate limit handling, account rotation, tiered model, header bundling, directory context, preamble, pattern cache, source elision, max-tokens cap, JSON output, classification phase
2. **Add**: `Invoke-LocalLLM` calls inline (synchronous loop)
3. **Keep**: File scanning, preset system, .env loading, SHA1 hash DB, trivial file skipping, progress display
4. **New prompt**: Simplified schema targeting ~400-600 token output

**Processing loop** (simplified):
```
foreach file in queue:
    source = read file (cap at MAX_FILE_LINES with head+tail)
    lsp = load compressed LSP context if available (symbols only)
    prompt = build simplified prompt (source + lsp + compact schema)
    response = Invoke-LocalLLM(system, prompt)
    write response to .md file
    record hash
    show progress
```

**No worker script needed** -- `archgen_worker.ps1` equivalent is inlined.

### `archgen_local_prompt.txt` -- Simplified Pass 1 prompt

```
Analyze this source file. Be extremely concise.

# <FILE PATH>

## Purpose
1-2 sentences.

## Responsibilities
- 3-5 bullets max

## Key Types
Name | Kind | Purpose (one line each, or "None")

## Key Functions
For each important function:
### <name>
- Purpose: (one sentence)
- Calls: (list of called functions)

## Globals
Name | Type | Purpose (or "None")

## Dependencies
- Key includes and external symbols

Rules: No speculation. Max 500 tokens.
```

### `arch_overview_local.ps1` -- Architecture Overview (rewrite of arch_overview.ps1)

**Changes from original:**

1. **Chunking is mandatory** -- the local LLM can't handle the full diagram data in one call
2. **Smaller chunks** -- reduce `CHUNK_THRESHOLD` from 1500 to ~400 lines
3. **Simpler prompts** -- ask for bullet-point overview, not full prose
4. **Two-tier synthesis** remains but with much smaller per-subsystem inputs
5. **Remove**: Claude CLI, rate limit handling, account rotation
6. **Keep**: Subsystem discovery, recursive splitting, incremental hashing

**Key change**: Instead of sending all per-file doc content, send only the `# heading` + `## Purpose` line from each doc (extracted via simple text parsing). This dramatically reduces tokens per chunk.

### `arch_overview_local_prompt.txt` -- Simplified overview prompts

Subsystem prompt and synthesis prompt, both requesting concise bullet-point output.

### `archpass2_local.ps1` -- Pass 2: Selective Re-Analysis (rewrite of archpass2.ps1)

**Changes from original:**

1. **Remove**: Worker script, `Start-Job`, Claude CLI, rate limit handling, account rotation, tiered model
2. **Add**: `Invoke-LocalLLM` calls inline (synchronous)
3. **Keep**: Scoring system (`-Top N`), `-ScoreOnly`, targeted context loading, SHA1 hash DB
4. **Simpler payload**: Source (capped at 300 lines) + Pass 1 doc + targeted context only
5. **Simpler prompt**: Ask for 3-4 specific enrichments, not full re-analysis

**No worker script needed** -- `archpass2_worker.ps1` equivalent is inlined.

### `archpass2_local_prompt.txt` -- Simplified Pass 2 prompt

Focus on:
- Architectural role (2 sentences)
- Key cross-references (incoming/outgoing as bullets)
- Design patterns (1-2 bullets)

Drop: Data flow, learning notes, potential issues.

### `archxref.ps1` -- No changes needed

Pure text processing, no LLM calls.

### `archgraph.ps1` -- No changes needed

Pure text processing, no LLM calls.

### `archpass2_context.ps1` -- No changes needed

Pure text processing, no LLM calls.

### `archgen_dirs.ps1` -- Skip for local LLM

Directory-level overviews were a context enrichment for Claude's Pass 1. With the local LLM's limited context, injecting directory overviews is counterproductive. **Do not port this script.**

### `serena_extract.ps1` / `serena_extract.py` -- No changes needed

LSP extraction talks to clangd directly, no LLM involved.

---

## New Prompt Design Philosophy

### Principles for local 14B model:

1. **Short system prompts** (~50-100 tokens, not 500)
2. **Explicit output format** with examples, not just schema descriptions
3. **Hard token limits** in the prompt itself ("Max 500 tokens output")
4. **One task per request** -- no multi-file batching
5. **Prefer bullets over tables** -- tables confuse smaller models
6. **No meta-instructions** like "be deterministic" or "adapt terminology" -- just state what to do
7. **Aggressive source truncation** -- the model needs room for its output within the context window

### Token budget per request (target):

| Component                  | Tokens          |
|----------------------------|-----------------|
| System prompt              | ~80             |
| Output schema              | ~200            |
| Source code (800 lines)    | ~4000-6000      |
| LSP context (compressed)   | ~500            |
| **Total input**            | **~5000-7000**  |
| **Output budget**          | **~500-800**    |
| **Total**                  | **~6000-8000**  |

This fits comfortably in 32K context. For very large files (>800 lines), truncation keeps us in budget.

---

## Implementation Order

### Phase 1: Foundation
1. Create `llm_common.ps1` with shared functions and `Invoke-LocalLLM`
2. Create `archgen_local_prompt.txt` (simplified prompt)
3. Test `Invoke-LocalLLM` against Ollama server manually

### Phase 2: Pass 1 (archgen)
4. Create `archgen_local.ps1` -- the main per-file doc generator
5. Test on a small subset (~10 files) from the target codebase
6. Iterate on prompt quality

### Phase 3: Overview
7. Create `arch_overview_local.ps1` with aggressive chunking
8. Create `arch_overview_local_prompt.txt`
9. Test on the Pass 1 output

### Phase 4: Pass 2
10. Create `archpass2_local.ps1`
11. Create `archpass2_local_prompt.txt`
12. Test on a handful of high-scoring files

### Phase 5: Integration
13. Update `.env` with local LLM settings
14. End-to-end test: full pipeline on the target codebase
15. Tune `MAX_FILE_LINES`, `LLM_MAX_TOKENS`, prompt wording based on output quality

---

## Files to Create (Summary)

| File                             | Type | Description                                  |
|----------------------------------|------|----------------------------------------------|
| `llm_common.ps1`                | New  | Shared helper module (LLM client + utilities) |
| `archgen_local.ps1`             | New  | Pass 1 per-file docs via local LLM          |
| `archgen_local_prompt.txt`      | New  | Simplified Pass 1 prompt                     |
| `arch_overview_local.ps1`       | New  | Architecture overview via local LLM          |
| `arch_overview_local_prompt.txt` | New  | Simplified overview prompts                  |
| `archpass2_local.ps1`           | New  | Pass 2 re-analysis via local LLM            |
| `archpass2_local_prompt.txt`    | New  | Simplified Pass 2 prompt                     |

## Files Unchanged

| File                    | Reason       |
|-------------------------|--------------|
| `archxref.ps1`          | No LLM calls |
| `archgraph.ps1`         | No LLM calls |
| `archpass2_context.ps1` | No LLM calls |
| `serena_extract.ps1`    | No LLM calls |
| `serena_extract.py`     | No LLM calls |

## Files Not Ported

| File                   | Reason                                            |
|------------------------|---------------------------------------------------|
| `archgen_worker.ps1`   | Logic inlined into `archgen_local.ps1`            |
| `archpass2_worker.ps1` | Logic inlined into `archpass2_local.ps1`          |
| `archgen_dirs.ps1`     | Context enrichment not worth limited token budget |

---

## Target Codebase Notes (C&C Remastered Collection source release)

This repo is the Command & Conquer Remastered Collection source drop (EA/Westwood, open-sourced 2020) containing the original C++ DLL source for Tiberian Dawn (1995) and Red Alert (1996), plus a C# WinForms map editor. Key characteristics:

- Three top-level trees:
  - `REDALERT/`        -- ~530 C++ files + `WIN32LIB/` subdir (~79 files)
  - `TIBERIANDAWN/`    -- ~307 C++ files + `WIN32LIB/` subdir (~82 files)
  - `CnCTDRAMapEditor/` -- 257 C# files (WinForms editor for both games)
- Build system: MSBuild `.vcxproj` for RA/TD (`REDALERT/RedAlert.vcxproj`, `TIBERIANDAWN/TiberianDawn.vcxproj`), `.csproj` for the map editor. Solutions: `CnCRemastered.sln`, `CnCTDRAMapEditor.sln`.
- Small-to-moderate size (~1100 source files total) -- well within local-LLM pipeline budgets.
- `compile_commands.json` is generated by `generate_compile_commands.py` parsing the two `.vcxproj` files directly (486 translation units with per-project include paths and defines).
- Preset: `cnc` (alias of `generals`/`sage`). Extended in `.env` via `INCLUDE_EXT_REGEX` / `EXCLUDE_DIRS_REGEX` to cover C# files for the map editor subsection.

### Current .env for this codebase:

```env
LLM_HOST=192.168.1.126
LLM_PORT=11434
LLM_MODEL=devstral-small-2
LLM_TEMPERATURE=0.1
LLM_MAX_TOKENS=800
LLM_TIMEOUT=120

PRESET=cnc
CODEBASE_DESC="Command & Conquer Remastered Collection source release (EA/Westwood, open-sourced 2020). Contains the original C++ game logic for Tiberian Dawn (1995) and Red Alert (1996) as shipped in the Remastered Collection DLLs, plus a C# WinForms map editor."

INCLUDE_EXT_REGEX=\.(cpp|h|hpp|c|cc|cxx|inl|inc|cs)$
EXCLUDE_DIRS_REGEX=[/\\](\.git|architecture|Debug|Release|x64|Win32|\.vs|bin|obj|Steamworks\.NET)([/\\]|$)

MAX_FILE_LINES=800
SKIP_TRIVIAL=1
MIN_TRIVIAL_LINES=20
CHUNK_THRESHOLD=400

#Subsections begin
REDALERT
TIBERIANDAWN
CnCTDRAMapEditor
#Subsections end
```

---

## Risk Assessment

| Risk                               | Mitigation                                                          |
|------------------------------------|---------------------------------------------------------------------|
| Output quality lower than Claude   | Simpler prompts, iterate on wording, accept less detail             |
| Model hallucinates function names  | Prompt says "No speculation", verify against LSP if available       |
| Context window exceeded            | Aggressive truncation, monitor token counts                         |
| Slow throughput (~5-15s per file)  | Acceptable for moderate codebases; progress display shows ETA       |
| Ollama server unreachable          | Retry logic in `Invoke-LocalLLM` (3 attempts)                      |
| Model returns malformed markdown   | Post-process: ensure `# heading` exists, strip preamble chatter    |

---

## aider Integration

The user has aider installed. For the implementation phase, aider can be used to:
- Generate initial script scaffolding from this plan
- Iterate on prompt engineering with live testing
- Refactor shared code into `llm_common.ps1`

Recommended aider workflow:
```bash
aider llm_common.ps1 archgen_local.ps1 archgen_local_prompt.txt
```
