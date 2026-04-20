# Model Selection for Local LLM Pipelines

Picking the right model is the most consequential setting after the repository layout. The wrong choice -- a model too weak for the task, or a strong model that doesn't fit in VRAM -- is the single biggest source of frustration across all three pipelines. This document explains the constraints, the reasoning behind the recommended models, and how to create custom context-window variants.

---

## 1. The Core Constraint

Every Ollama call allocates a KV (key-value) cache sized for the model's `num_ctx` parameter. The fundamental rule is:

```
input tokens + output tokens ≤ num_ctx
```

If the prompt plus the generated output exceeds `num_ctx`, Ollama silently truncates the input from the front -- losing imports, system prompt, and top-of-file context. These are exactly the tokens the model needs most.

Meanwhile, a larger `num_ctx` consumes more VRAM. If the model weights plus the KV cache exceed available VRAM, Ollama partially offloads to system RAM (CPU), which is roughly 4x slower. That slowdown causes timeouts on longer generation calls.

The goal is to find the smallest `num_ctx` that fits every call the pipeline makes, so the model stays fully on GPU and runs at full speed.

---

## 2. GPU VRAM Budget

The model's VRAM footprint has two components:

1. **Model weights** -- fixed, determined by the model size and quantization level. Loaded once and constant for the lifetime of the session.
2. **KV cache** -- variable, determined by `num_ctx`. Allocated when the model is loaded and scales roughly linearly with context length.

There is also a small overhead for scratch buffers and activations, typically a few hundred MB.

### Qwen 2.5 Coder at q4_K_M quantization

| Model size | Weights  | Notes                                         |
|------------|----------|-----------------------------------------------|
| 32B        | ~19.85 GB | Fits on 24 GB GPUs with a reduced context     |
| 14B        | ~9 GB    | Fits on 12 GB GPUs with the default 32k context |
| 7B         | ~5 GB    | Fits on 8 GB GPUs                             |

### KV cache scaling for qwen2.5-coder:32b

KV cache size scales roughly linearly with `num_ctx`. Exact values vary slightly by Ollama version and scratch buffer allocation, but these are representative:

| num_ctx | KV cache | Total VRAM (weights + cache) | 24 GB headroom | Status                     |
|---------|----------|------------------------------|----------------|----------------------------|
| 4096    | ~1.0 GB  | ~21.9 GB                     | ~2.1 GB        | Fully resident, fast       |
| 8192    | ~2.0 GB  | ~22.9 GB                     | ~1.1 GB        | Fully resident, fast       |
| 12288   | ~3.0 GB  | ~23.9 GB                     | ~0.1 GB        | Fully resident, tight      |
| 16384   | ~4.0 GB  | ~24.9 GB                     | **overflow**   | Partial CPU offload (slow) |
| 32768   | ~8.0 GB  | ~28.9 GB                     | **overflow**   | Partial CPU offload (slow) |

The default `num_ctx` for `qwen2.5-coder:32b` is **32768**. That's ~6x larger than any input these pipelines produce and adds ~8 GB of KV cache -- enough to push total VRAM past a 24 GB card and force partial CPU offload. This is the root cause of the timeouts on a first run with the default model.

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

## 4. Choosing the Context Window Sizes

The measurements above lead directly to two optimal `num_ctx` values:

### 8192 (8k) -- for analysis-only scripts

Every architecture analysis step and every analysis-only debugging call fits under 8192 with headroom. At this context length, qwen2.5-coder:32b uses ~22.9 GB of VRAM on a 24 GB card, leaving ~1.1 GB of headroom. Fast and stable.

### 12288 (12k) -- for fix calls and heavy synthesis

The three debugging scripts that exceed 8192 -- `bughunt_iterative_local.ps1` (fix calls), `interfaces_local.ps1` (synthesis), and `testgap_local.ps1` (synthesis) -- all fit under 12288. At this context length, qwen2.5-coder:32b uses ~23.9 GB on a 24 GB card. Tight but fully resident.

### Why not 16k or higher?

At 16384, the total VRAM hits ~24.9 GB -- exceeding a 24 GB card. Ollama partially offloads to CPU, inference slows ~4x, and long generation calls time out. The 16k variant is usable for one-off retries on a single stuck step, but not for full pipeline runs.

### Why not a single 12k for everything?

You could use 12k for all scripts. The reason for two variants is efficiency: the 8k variant leaves more VRAM headroom, reducing the chance of OOM from background processes or GPU memory fragmentation during long runs. It also loads marginally faster. Since most scripts don't need more than 8k, there's no reason to allocate the extra KV cache.

---

## 5. Creating Custom Context-Window Variants

Ollama's `ollama create` command builds a new model tag that overrides parameters while reusing the existing weights. No re-download, no extra disk space for weights -- the new variant is just a parameter override.

### One-time setup on the Ollama server (PowerShell)

```powershell
# Pull the base model (first time only)
ollama pull qwen2.5-coder:32b

# 8k variant -- for analysis-only calls
@"
FROM qwen2.5-coder:32b
PARAMETER num_ctx 8192
"@ | Set-Content -Encoding ASCII Modelfile
ollama create qwen2.5-coder:32b-8k -f Modelfile

# 12k variant -- for fix calls and synthesis passes
@"
FROM qwen2.5-coder:32b
PARAMETER num_ctx 12288
"@ | Set-Content -Encoding ASCII Modelfile
ollama create qwen2.5-coder:32b-12k -f Modelfile
```

`ollama create` takes a few seconds. The base `qwen2.5-coder:32b` tag is unchanged and can still be used directly.

### Verifying the fit

After creating or switching models, check that the model is fully on GPU:

```powershell
ollama ps
```

```
NAME                      SIZE     PROCESSOR    CONTEXT
qwen2.5-coder:32b-8k      ~22 GB   100% GPU     8192
```

The `PROCESSOR` column is what matters. **`100% GPU`** means the model is fully resident and inference runs at full speed (~15-25 tok/s for qwen 32b on a 4090, somewhat less on a 3090). Anything else -- `75% GPU / 25% CPU`, `50% GPU / 50% CPU` -- means partial offload and you will hit timeouts on longer generations.

---

## 6. Model Quality Tiers

### qwen2.5-coder:32b -- best quality (~20 GB weights)

The strongest local code model in its class. Produces the most thorough bug reports, the most accurate interface contracts, and the highest-quality code generation. Requires a 24 GB GPU and custom context-window variants to avoid CPU offload.

### qwen2.5-coder:14b -- good quality (~9 GB weights)

Fits comfortably on 12 GB GPUs at the default 32k context -- no custom variants needed. Roughly 80% of the 32b's review depth in practice. Still a capable code reviewer and generator. The right choice when VRAM is limited.

When using the 14b model, set `LLM_DEFAULT_MODEL=qwen2.5-coder:14b` in `.env` and leave the role-specific keys blank. The 14b's default 32k context window handles everything -- analysis, synthesis, and fix calls -- without needing separate variants.

### qwen2.5-coder:7b -- adequate (~5 GB weights)

Fits on 8 GB GPUs. Adequate for straightforward analysis but may struggle on complex TUI files, multi-module synthesis, and iterative fix loops where nuance matters. Use `--only-step` or `-TargetDir` to retry difficult files with a larger model if needed.

### deepseek-coder-v2:16b -- strong alternative (~10 GB weights)

Good at multi-file reasoning. A reasonable second pick if qwen 14b feels shallow on your workload. Similar VRAM footprint to qwen 14b.

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
| `LLM_PLANNING_MODEL` | Coding planning stages (0, 1, 2a, 2b, 3a, 3b) when `--local`             |
| `LLM_AIDER_MODEL`    | Coding stages 4 (`run_aider.py`) + 5 (`fix_imports.py`)                  |

### 24 GB GPU configuration (one model everywhere)

```ini
LLM_DEFAULT_MODEL=qwen3-coder:30b
LLM_TIMEOUT=300
```

All role keys left blank => every worker uses `qwen3-coder:30b`. Per-request
`num_ctx` handles the high-context synthesis passes in the three heavy
debug workers (`bughunt_iterative_local.ps1`, `interfaces_local.ps1`,
`testgap_local.ps1`), so no separate "high-ctx" model variant is needed.

### 12 GB GPU configuration (one model everywhere)

```ini
LLM_DEFAULT_MODEL=qwen2.5-coder:14b
LLM_TIMEOUT=180
```

### Reasoning model for planning, coder model for everything else

```ini
LLM_DEFAULT_MODEL=qwen3-coder:30b
LLM_PLANNING_MODEL=gemma4:26b
LLM_THINK=true
```

### Aider (LocalLLMCoding)

Aider uses its own `--model` flag rather than the `.env` file:

```powershell
python .\LocalLLMCoding\run_aider.py --model ollama/qwen2.5-coder:32b-12k
```

The 12k variant is recommended for aider because fix/rewrite calls generate up to ~2000 tokens of output, and the largest nmon files push total context past 8k.

---

## 8. Smaller GPUs and Fallback Strategy

| GPU VRAM | Recommended model         | Custom variant needed? | Notes                                    |
|----------|---------------------------|------------------------|------------------------------------------|
| 24 GB    | `qwen2.5-coder:32b`      | Yes (8k + 12k)         | Best quality, requires tuning            |
| 16 GB    | `qwen2.5-coder:14b`      | No                     | Default 32k context fits with headroom   |
| 12 GB    | `qwen2.5-coder:14b`      | No                     | Tight but fully resident                 |
| 8 GB     | `qwen2.5-coder:7b`       | No                     | Adequate; may need retries on complex files |

Check available VRAM before choosing:

```powershell
nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv
```

---

## 9. Per-Script Model Recommendations

### Architecture Analysis (LocalLLMAnalysis)

| Script                    | Recommended model          | Why                                           |
|---------------------------|----------------------------|-----------------------------------------------|
| `Arch_Analysis_Pipeline.py`         | `qwen2.5-coder:32b-8k`    | Orchestrates all steps; every step fits in 8k  |
| `archgen_local.ps1`       | `qwen2.5-coder:32b-8k`    | Per-file docs, ~8300 tokens max               |
| `archxref.ps1`            | --                         | No LLM calls (text processing only)           |
| `archgraph.ps1`           | --                         | No LLM calls (text processing only)           |
| `arch_overview_local.ps1` | `qwen2.5-coder:32b-8k`    | Chunked synthesis, ~5200 tokens max           |
| `archpass2_context.ps1`   | --                         | No LLM calls (text processing only)           |
| `archpass2_local.ps1`     | `qwen2.5-coder:32b-8k`    | Per-file enrichment, ~4900 tokens max         |
| `serena_extract.ps1`      | --                         | No LLM calls (clangd/LSP only)               |

### Software Development (LocalLLMCoding)

| Script                    | Recommended model            | Why                                                    |
|---------------------------|------------------------------|--------------------------------------------------------|
| `run_aider.py`            | `qwen2.5-coder:32b-12k`     | Whole-file rewrites need room for ~2000-token output    |

### Debugging (LocalLLMDebug)

| Script                        | Recommended model          | Why                                                         |
|-------------------------------|----------------------------|-------------------------------------------------------------|
| `bughunt_local.ps1`           | `qwen2.5-coder:32b-8k`    | Analysis-only, 900-token output, ~7900 tokens total         |
| `bughunt_iterative_local.ps1` | `qwen2.5-coder:32b-12k`   | Fix calls need 4000-token output, ~11000 tokens total       |
| `dataflow_local.ps1`          | `qwen2.5-coder:32b-8k`    | Extraction and synthesis both fit under 8k                  |
| `interfaces_local.ps1`        | `qwen2.5-coder:32b-12k`   | Synthesis concatenates all extractions, ~12500 tokens total |
| `testgap_local.ps1`           | `qwen2.5-coder:32b-12k`   | Synthesis concatenates all analyses, ~12300 tokens total    |
