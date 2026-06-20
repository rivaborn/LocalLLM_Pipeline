# Model Selection for Local LLM Pipelines

Picking the right model is the most consequential setting after the repository layout. The wrong choice -- a model too weak for the task, or a strong model that doesn't fit in VRAM -- is the single biggest source of frustration across all three pipelines. This document explains the constraints, the reasoning behind the recommended models, and how to create custom context-window variants.

> **Backend note.** `LLM_BACKEND` in `Common/.env` selects the local backend. The **default is `vllm`**: requests hit the LLMConfig OpenAI `/v1` gateway (`http://192.168.1.40:11430/v1`), which auto-loads the requested served-name model onto the GPU on first request. On vLLM the **context window is fixed per served-name alias at serve time** (both pipeline aliases serve at 65536), so the per-request `num_ctx` tuning and KV-cache math in sections 1-5 below **apply only to the `ollama` backend** (`LLM_BACKEND=ollama`, native Ollama at `:11434`). On vLLM you instead pick an alias whose serve-time window is large enough and keep the output budget (`LLM_PLANNING_MAX_TOKENS`, default 16384) under that ceiling; alias VRAM/context tuning lives in the LLMConfig repo. The defaults are vLLM served-names (planning `qwen3.5-27b`, coder `qwen3-coder-30b`), selected via `Common/.env`. The sections that follow describe the ollama-backend tuning model in detail; the same model-quality reasoning carries over to picking a vLLM alias.

---

## 1. The Core Constraint (ollama backend)

Every Ollama call allocates a KV (key-value) cache sized for the model's `num_ctx` parameter. The fundamental rule is:

```
input tokens + output tokens ≤ num_ctx
```

If the prompt plus the generated output exceeds `num_ctx`, Ollama silently truncates the input from the front -- losing imports, system prompt, and top-of-file context. These are exactly the tokens the model needs most.

Meanwhile, a larger `num_ctx` consumes more VRAM. If the model weights plus the KV cache exceed available VRAM, Ollama partially offloads to system RAM (CPU), which is roughly 4x slower. That slowdown causes timeouts on longer generation calls.

The goal is to find the smallest `num_ctx` that fits every call the pipeline makes, so the model stays fully on GPU and runs at full speed.

---

## 2. GPU VRAM Budget (ollama backend)

The model's VRAM footprint has two components:

1. **Model weights** -- fixed, determined by the model size and quantization level. Loaded once and constant for the lifetime of the session.
2. **KV cache** -- variable, determined by `num_ctx`. Allocated when the model is loaded and scales roughly linearly with context length.

There is also a small overhead for scratch buffers and activations, typically a few hundred MB.

### A 30B-class coder tag at q4_K_M quantization

| Model size | Weights  | Notes                                         |
|------------|----------|-----------------------------------------------|
| 30-32B     | ~19.85 GB | Fits on 24 GB GPUs with a reduced context     |
| 14B        | ~9 GB    | Fits on 12 GB GPUs with the default 32k context |
| 7B         | ~5 GB    | Fits on 8 GB GPUs                             |

### KV cache scaling for a 30B-class coder tag (ollama backend)

KV cache size scales roughly linearly with `num_ctx`. Exact values vary slightly by Ollama version and scratch buffer allocation, but these are representative:

| num_ctx | KV cache | Total VRAM (weights + cache) | 24 GB headroom | Status                     |
|---------|----------|------------------------------|----------------|----------------------------|
| 4096    | ~1.0 GB  | ~21.9 GB                     | ~2.1 GB        | Fully resident, fast       |
| 8192    | ~2.0 GB  | ~22.9 GB                     | ~1.1 GB        | Fully resident, fast       |
| 12288   | ~3.0 GB  | ~23.9 GB                     | ~0.1 GB        | Fully resident, tight      |
| 16384   | ~4.0 GB  | ~24.9 GB                     | **overflow**   | Partial CPU offload (slow) |
| 32768   | ~8.0 GB  | ~28.9 GB                     | **overflow**   | Partial CPU offload (slow) |

The default `num_ctx` for a 30B-class coder tag is **32768**. That's ~6x larger than any input these pipelines produce and adds ~8 GB of KV cache -- enough to push total VRAM past a 24 GB card and force partial CPU offload. This is the root cause of the timeouts on a first run with the default model on the ollama backend. (On the default vLLM backend this is a non-issue: the alias serves at a fixed window and the gateway packs VRAM at load time.)

---

## 3. Context Window Requirements

To choose the right `num_ctx`, we measured the actual token usage of every LLM call across all three pipelines. All measurements assume `MAX_FILE_LINES=800` (the default source truncation limit).

### Architecture Analysis (LocalLLMAnalysis)

| Script                                    | Max input | Output cap | Total needed |
|-------------------------------------------|-----------|------------|--------------|
| `archgen_local.ps1` (per-file)            | ~7400     | 900        | ~8300        |
| `arch_overview_local.ps1` (chunked synth) | ~4000     | 1200       | ~5200        |
| `archpass2_local.ps1` (per-file)          | ~4000     | 900        | ~4900        |

All steps fit under 8192 tokens with headroom.

### Software Development (LocalLLMCoding)

| Script                          | Max input    | Output cap | Total needed   |
|---------------------------------|--------------|------------|----------------|
| `run_aider.py` (per-file step)  | ~3000-4500   | ~2000      | ~5000-6500     |

Each aider call sends a self-contained prompt plus the file being created or edited. The prompt inlines only the spec relevant to that one file, keeping total context modest. Even the largest nmon modules (TUI widgets, storage) fit comfortably under 8192.

### Debugging (LocalLLMDebug)

| Script                                       | Max input | Output cap | Total needed |
|----------------------------------------------|-----------|------------|--------------|
| `bughunt_local.ps1`                          | ~7000     | 900        | ~7900        |
| `bughunt_iterative_local.ps1` (analysis)     | ~7000     | 900        | ~7900        |
| **`bughunt_iterative_local.ps1` (fix call)** | ~7000     | **4000**   | **~11000**   |
| `dataflow_local.ps1` (per-file extract)      | ~6500     | 400        | ~6900        |
| `dataflow_local.ps1` (synthesis)             | ~6000     | 1800       | ~7800        |
| `interfaces_local.ps1` (per-file extract)    | ~7000     | 700        | ~7700        |
| **`interfaces_local.ps1` (synthesis)**       | ~10500    | 2000       | **~12500**   |
| `testgap_local.ps1` (per-file)               | ~7500     | 700        | ~8200        |
| **`testgap_local.ps1` (synthesis)**          | ~10500    | 1800       | **~12300**   |

Most calls fit under 8192. Three do not:

- `bughunt_iterative_local.ps1` fix calls need ~11000 tokens because the LLM must output a full rewritten file (up to 4000 tokens).
- `interfaces_local.ps1` and `testgap_local.ps1` synthesis passes concatenate all per-file extractions into a single prompt, which can exceed 10000 tokens on larger codebases.

---

## 4. Choosing the Context Window Sizes (ollama backend)

The measurements above lead directly to two optimal `num_ctx` values (on the ollama backend; on vLLM the alias's serve-time window plays this role):

### 8192 (8k) -- for analysis-only scripts

Every architecture analysis step and every analysis-only debugging call fits under 8192 with headroom. At this context length, a 30B-class coder tag uses ~22.9 GB of VRAM on a 24 GB card, leaving ~1.1 GB of headroom. Fast and stable.

### 12288 (12k) -- for fix calls and heavy synthesis

The three debugging scripts that exceed 8192 -- `bughunt_iterative_local.ps1` (fix calls), `interfaces_local.ps1` (synthesis), and `testgap_local.ps1` (synthesis) -- all fit under 12288. At this context length, a 30B-class coder tag uses ~23.9 GB on a 24 GB card. Tight but fully resident.

### Why not 16k or higher?

At 16384, the total VRAM hits ~24.9 GB -- exceeding a 24 GB card. Ollama partially offloads to CPU, inference slows ~4x, and long generation calls time out. The 16k variant is usable for one-off retries on a single stuck step, but not for full pipeline runs.

### Why not a single 12k for everything?

You could use 12k for all scripts. The reason for two variants is efficiency: the 8k variant leaves more VRAM headroom, reducing the chance of OOM from background processes or GPU memory fragmentation during long runs. It also loads marginally faster. Since most scripts don't need more than 8k, there's no reason to allocate the extra KV cache.

---

## 5. Creating Custom Context-Window Variants (ollama backend)

This section applies to the `ollama` backend only. On the default vLLM backend you do not create per-context variants -- the context is fixed when the served-name alias is defined in the LLMConfig repo.

Ollama's `ollama create` command builds a new model tag that overrides parameters while reusing the existing weights. No re-download, no extra disk space for weights -- the new variant is just a parameter override.

### One-time setup on the Ollama server (PowerShell)

```powershell
# Pull the base model (first time only)
ollama pull qwen3-coder:30b

# 8k variant -- for analysis-only calls
@"
FROM qwen3-coder:30b
PARAMETER num_ctx 8192
"@ | Set-Content -Encoding ASCII Modelfile
ollama create qwen3-coder:30b-8k -f Modelfile

# 12k variant -- for fix calls and synthesis passes
@"
FROM qwen3-coder:30b
PARAMETER num_ctx 12288
"@ | Set-Content -Encoding ASCII Modelfile
ollama create qwen3-coder:30b-12k -f Modelfile
```

`ollama create` takes a few seconds. The base `qwen3-coder:30b` tag is unchanged and can still be used directly.

### Verifying the fit

After creating or switching models, check that the model is fully on GPU:

```powershell
ollama ps
```

```
NAME                      SIZE     PROCESSOR    CONTEXT
qwen3-coder:30b-8k        ~22 GB   100% GPU     8192
```

The `PROCESSOR` column is what matters. **`100% GPU`** means the model is fully resident and inference runs at full speed (~15-25 tok/s for a 30B coder on a 4090, somewhat less on a 3090). Anything else -- `75% GPU / 25% CPU`, `50% GPU / 50% CPU` -- means partial offload and you will hit timeouts on longer generations.

---

## 6. Model Quality Tiers

The current defaults are the vLLM served-names `qwen3-coder-30b` (coder roles) and `qwen3.5-27b` (planning roles), selected via `Common/.env` and auto-loaded by the gateway. The tiers below describe the quality/VRAM tradeoff; the `:`-tag forms are the equivalents on the `ollama` backend. The full vLLM alias catalog (exact alias names and serve-time windows) lives in the LLMConfig repo.

### 30B-class coder (e.g. served-name `qwen3-coder-30b`, ollama tag `qwen3-coder:30b`) -- best quality (~20 GB weights)

The strongest local code model in its class and the pipeline default for coder roles. Produces the most thorough bug reports, the most accurate interface contracts, and the highest-quality code generation. Needs a 24 GB GPU; on the ollama backend it also needs custom context-window variants to avoid CPU offload (on vLLM the alias handles this at serve time).

### 14B-class coder -- good quality (~9 GB weights)

Fits comfortably on 12 GB GPUs at the default 32k context (ollama backend) -- no custom variants needed. Roughly 80% of the 30B's review depth in practice. Still a capable code reviewer and generator. The right choice when VRAM is limited.

On the ollama backend, set `LLM_DEFAULT_MODEL` to the 14B tag in `.env` and leave the role-specific keys blank; its default 32k context window handles everything -- analysis, synthesis, and fix calls -- without needing separate variants.

### 7B-class coder -- adequate (~5 GB weights)

Fits on 8 GB GPUs. Adequate for straightforward analysis but may struggle on complex TUI files, multi-module synthesis, and iterative fix loops where nuance matters. Use `--only-step` or `-TargetDir` to retry difficult files with a larger model if needed.

### deepseek-coder-v2:16b -- strong alternative (~10 GB weights)

Good at multi-file reasoning. A reasonable second pick if a 14B coder feels shallow on your workload. Similar VRAM footprint to a 14B coder.

### devstral-small-2 -- do not use

Despite the name, devstral is an **agentic** model tuned for SWE-bench-style "take action" tasks, not careful instruction-following or code review. On iterative review tasks it aggressively rewrites code, hallucinates new "bugs" each pass, and balloons file size by 2-6x. Every convergence and bloat guard in `bughunt_iterative_local.ps1` was added because of devstral behaviour, not qwen behaviour.

On aider workflows it over-rewrites files and hallucinates changes beyond what the prompt asked for, routinely failing the "match the prompt exactly" criterion that `aidercommands.md` depends on.

If you see `BLOAT` or `DIVERGING` statuses firing frequently, a weak or agentic model is usually the cause.

---

## 7. Wiring Models to Scripts via .env

The pipelines resolve the model per role through a single fallback chain
(`cfg.resolve_model` in Python, `Get-LLMModel` in PowerShell):

```
role-specific key  ->  LLM_DEFAULT_MODEL  ->  hardcoded fallback
```

| Key                  | Used by                                                                  |
|----------------------|--------------------------------------------------------------------------|
| `LLM_DEFAULT_MODEL`  | Universal fallback for every role key below (blank/unset => fallback)    |
| `LLM_MODEL`          | Debug + analysis workers                                                 |
| `LLM_PLANNING_MODEL` | Coding planning stages (0, 1) when run locally                           |
| `LLM_AIDER_MODEL`    | Coding coder stages 2a/2b/3a/3b + 4 (`run_aider.py`) + 5 (`fix_imports.py`) |

Use vLLM served-names (`-`) on the default `vllm` backend and Ollama tags (`:`) on the `ollama` backend.

### 24 GB GPU configuration, vllm backend (one model everywhere)

```ini
LLM_BACKEND=vllm
LLM_DEFAULT_MODEL=qwen3-coder-30b
LLM_TIMEOUT=300
```

All role keys left blank => every worker uses the `qwen3-coder-30b` served-name.
The gateway auto-loads it; its context is fixed at the alias serve-time window
(65536 for the pipeline aliases), so no per-request `num_ctx` and no "high-ctx"
variant are needed -- just keep the output budget under the ceiling.

### 24 GB GPU configuration, ollama backend (one model everywhere)

```ini
LLM_BACKEND=ollama
LLM_DEFAULT_MODEL=qwen3-coder:30b
LLM_TIMEOUT=300
```

All role keys left blank => every worker uses `qwen3-coder:30b`. Per-request
`num_ctx` handles the high-context synthesis passes in the three heavy
debug workers (`bughunt_iterative_local.ps1`, `interfaces_local.ps1`,
`testgap_local.ps1`), so no separate "high-ctx" model variant is needed.

### 12 GB GPU configuration, ollama backend (one model everywhere)

```ini
LLM_BACKEND=ollama
LLM_DEFAULT_MODEL=<14B-class coder tag>
LLM_TIMEOUT=180
```

### Reasoning model for planning, coder model for everything else

```ini
LLM_DEFAULT_MODEL=qwen3-coder-30b
LLM_PLANNING_MODEL=qwen3.5-27b
LLM_THINK=true
```

`LLM_THINK` only affects the `ollama` backend (it sends `think:true` on `/api/chat`); it is inert on vLLM, whose pipeline aliases set no reasoning-parser.

### Aider (LocalLLMCoding)

`run_aider.py` derives aider's `--model` from `Common/.env` automatically -- `openai/<LLM_AIDER_MODEL>` on the vllm backend (default `openai/qwen3-coder-30b`), `ollama_chat/<LLM_AIDER_MODEL>` on ollama. To override explicitly:

```powershell
# vllm backend (served-name via litellm's openai/ provider -> the /v1 gateway):
python .\LocalLLMCoding\run_aider.py --model openai/qwen3-coder-30b

# ollama backend (a reduced-context variant tag):
python .\LocalLLMCoding\run_aider.py --model ollama_chat/qwen3-coder:30b-12k
```

On the ollama backend the 12k variant is recommended for aider because fix/rewrite calls generate up to ~2000 tokens of output, and the largest nmon files push total context past 8k. On vLLM the alias's serve-time window covers this.

---

## 8. Smaller GPUs and Fallback Strategy (ollama backend)

Variant tuning applies to the `ollama` backend; on the default `vllm` backend the served-name alias fixes context at serve time, so the "custom variant" column does not apply.

| GPU VRAM | Recommended model (ollama tag) | Custom variant needed? | Notes                                       |
|----------|--------------------------------|------------------------|---------------------------------------------|
| 24 GB    | `qwen3-coder:30b`              | Yes (8k + 12k)         | Best quality, requires tuning               |
| 16 GB    | 14B-class coder                | No                     | Default 32k context fits with headroom      |
| 12 GB    | 14B-class coder                | No                     | Tight but fully resident                    |
| 8 GB     | 7B-class coder                 | No                     | Adequate; may need retries on complex files |

Check available VRAM before choosing:

```powershell
nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv
```

---

## 9. Per-Script Model Recommendations

The recommended-model columns below give **ollama-backend** variant tags (the `-8k`/`-12k` reduced-context variants from section 5). On the default **vllm** backend, point every LLM-calling script at the coder served-name (`qwen3-coder-30b`) and let the alias's serve-time window cover these token totals.

### Architecture Analysis (LocalLLMAnalysis)

| Script                       | Recommended model (ollama) | Why                                           |
|------------------------------|----------------------------|-----------------------------------------------|
| `Arch_Analysis_Pipeline.py`  | `qwen3-coder:30b-8k`       | Orchestrates all steps; every step fits in 8k |
| `archgen_local.ps1`          | `qwen3-coder:30b-8k`       | Per-file docs, ~8300 tokens max               |
| `archxref.ps1`               | --                         | No LLM calls (text processing only)           |
| `archgraph.ps1`              | --                         | No LLM calls (text processing only)           |
| `arch_overview_local.ps1`    | `qwen3-coder:30b-8k`       | Chunked synthesis, ~5200 tokens max           |
| `archpass2_context.ps1`      | --                         | No LLM calls (text processing only)           |
| `archpass2_local.ps1`        | `qwen3-coder:30b-8k`       | Per-file enrichment, ~4900 tokens max         |
| `serena_extract.ps1`         | --                         | No LLM calls (clangd/LSP only)                |

### Software Development (LocalLLMCoding)

| Script         | Recommended model (ollama) | Why                                                  |
|----------------|----------------------------|------------------------------------------------------|
| `run_aider.py` | `qwen3-coder:30b-12k`      | Whole-file rewrites need room for ~2000-token output |

### Debugging (LocalLLMDebug)

| Script                        | Recommended model (ollama) | Why                                                         |
|-------------------------------|----------------------------|-------------------------------------------------------------|
| `bughunt_local.ps1`           | `qwen3-coder:30b-8k`       | Analysis-only, 900-token output, ~7900 tokens total         |
| `bughunt_iterative_local.ps1` | `qwen3-coder:30b-12k`      | Fix calls need 4000-token output, ~11000 tokens total       |
| `dataflow_local.ps1`          | `qwen3-coder:30b-8k`       | Extraction and synthesis both fit under 8k                  |
| `interfaces_local.ps1`        | `qwen3-coder:30b-12k`      | Synthesis concatenates all extractions, ~12500 tokens total |
| `testgap_local.ps1`           | `qwen3-coder:30b-12k`      | Synthesis concatenates all analyses, ~12300 tokens total    |
