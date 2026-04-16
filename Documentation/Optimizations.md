# Architecture Analysis Toolkit -- Optimizations

Strategies for managing the context window of a local LLM when analyzing codebases. The Analysis pipeline currently runs at `LLM_ANALYSIS_NUM_CTX=49152` (per-request `num_ctx` for `qwen3-coder:30b` via the native Ollama `/api/chat` endpoint), but most optimizations below still matter at any window size — they were tuned when the working window was closer to 8-12K.

---

## Active Optimizations

### 1. Skip Generated and Trivial Files
**Savings: 30-40% fewer LLM calls.** Detected by filename pattern (`.generated.h`, `.gen.cpp`, `Module.*.cpp`) or line count (<20 lines). Stub docs written without LLM calls. Controlled by `SKIP_TRIVIAL=1` and `MIN_TRIVIAL_LINES=20` in `LocalLLM_Pipeline/Common/.env`.

### 2. Aggressive Source Truncation
**Keeps every request within context budget.** Source capped at `MAX_FILE_LINES` (default 800) via head+tail truncation. `Truncate-Source` in `Common/llm_common.ps1`.

### 3. Compressed LSP Context
**~80% reduction in LSP tokens.** When available, only Symbol Overview is loaded (drops references, trimmed source). `Load-CompressedLSP` in `Common/llm_common.ps1`. Also used by `LocalLLMDebug/interfaces_local.ps1` when `SERENA_CONTEXT_DIR` is configured.

### 4. Adaptive Output Budget
**10-20% output token reduction.** Budget proportional to file size: 300 tokens (<50 lines) to 800 tokens (500+ lines). `Get-OutputBudget` in `Common/llm_common.ps1`.

### 5. Simplified Prompts
**~300 fewer input tokens per request.** Bullet points instead of tables, fewer sections, explicit token limits.

### 6. Per-File Targeted Pass 2 Context
**~70% reduction in Pass 2 context tokens.** `archpass2_context.ps1` extracts 30-80 lines of relevant context per file instead of 500+ generic lines.

### 7. Chunked Overview with Summary Extraction
**Fits overview within context window.** Only headings + purpose extracted from each doc. Chunk threshold 400 lines.

### 8. SHA1 Incremental Processing
**Zero LLM calls for unchanged files on re-run.** Hash recorded in `hashes.tsv`, checked before each file.

### 9. Preset Regex Overrides
**Prevents wasted LLM calls on irrelevant files.** `INCLUDE_EXT_REGEX` and `EXCLUDE_DIRS_REGEX` in `Common/.env` override the built-in preset defaults, letting you add languages the preset doesn't cover (e.g. `.cs` for a C# subsection) or exclude build-output / third-party dirs (`bin`, `obj`, `Steamworks.NET`, etc.) without editing `llm_common.ps1`. Both `archgen_local.ps1` and `archpass2_local.ps1` honor these via `Cfg 'INCLUDE_EXT_REGEX'` / `Cfg 'EXCLUDE_DIRS_REGEX'`.

### 10. Per-Request `num_ctx` via native Ollama `/api/chat`
**Analysis runs at 49152 tokens without a custom Modelfile.** `Invoke-LocalLLM` in `Common/llm_common.ps1` detects `LLM_NUM_CTX > 0` and routes through Ollama's native `/api/chat` endpoint with `options.num_ctx`. Analysis scripts promote `LLM_ANALYSIS_NUM_CTX` into `LLM_NUM_CTX` after loading config, so every Analysis call gets the larger window without changing callsites. One model tag (`qwen3-coder:30b`) covers every pipeline.

---

## Token Budget Per Request

| Component                | Tokens         |
|--------------------------|----------------|
| System prompt            | ~80            |
| Output schema (prompt)   | ~200           |
| Source code (800 lines)  | ~4000-6000     |
| LSP context (compressed) | ~500           |
| **Total input**          | **~5000-7000** |
| **Output budget**        | **~300-800**   |
| **Total per call**       | **~6000-8000** |

At `LLM_ANALYSIS_NUM_CTX=49152`, this leaves ~40K tokens of headroom per call — enough to accommodate future integration features (injecting xref/architecture context) without hitting the window.
