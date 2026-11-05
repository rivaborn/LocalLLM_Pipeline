# Local LLM Software Development Pipeline

The `LocalLLMCoding/` directory contains a two-stage pipeline for building complete Python applications. Claude Code acts as the architect (writing the plan), and a local LLM acts as the implementer (writing the code). The local backend is selected by `LLM_BACKEND` in `Common/.env` -- by default vLLM behind the LLMConfig OpenAI `/v1` gateway, with native Ollama selectable. The pipeline was developed while building **nmon**, a real-time Nvidia GPU monitor, but the approach is general-purpose.

---

## Why Two Stages

Planning and implementation have very different requirements. Planning needs broad reasoning, strong domain knowledge, and the ability to hold an entire system design in mind at once. Implementation needs focused, precise code generation within a tight context window. Using a cloud model (Claude) for planning and a local coder model (`qwen3-coder-30b`, served by vLLM through the LLMConfig gateway) for code generation plays to each tool's strengths and avoids their weaknesses.

---

## Pipeline Overview

```
1. Initial Prompt          User writes a rough spec
        |
        v
2. Implementation Planning Prompt.md          Claude refines it into a detailed planning prompt
        |
        v
3. Architecture Plan.md     Claude generates a full architecture plan
        |
        v
4. aidercommands.md        Claude converts the plan into 25 single-file steps
        |
        v
5. run_aider.py            Script feeds each step to the local LLM via aider
        |
        v
6. Source code              Local LLM writes each file, one at a time
```

---

## Stage 1 -- Writing the Planning Prompt

**Files:** `InitialPrompt.md` -> `Implementation Planning Prompt.md`

The user writes a rough specification describing what the application should do. Claude reviews the prompt and improves it -- fixing inconsistencies, filling gaps, and adding the specificity needed for a good architecture plan.

`InitialPrompt.md` records the original rough prompt and Claude's critique of it. `Implementation Planning Prompt.md` is the refined version that Claude actually processes.

The refined prompt is explicit about everything the architecture plan must contain: target environment, technology choices, functional requirements, non-functional requirements, and the exact format of the deliverable (down to "function signatures with parameter types and pseudocode logic for each function"). This level of detail matters because vague prompts produce vague plans, and vague plans produce broken code.

---

## Stage 2 -- Generating the Architecture Plan

**File:** `Architecture Plan.md`

Claude processes `Implementation Planning Prompt.md` and produces a comprehensive architecture document covering:

- Full directory tree and module structure
- SQLite schema and Python dataclasses
- Per-module breakdown with function signatures, pseudocode, and error handling
- Data flow diagram from GPU driver through collector to storage to TUI
- TUI layout mapped to specific Rich components
- Configuration schema with validation rules
- Detailed test strategy with 40+ named test cases
- Dependency list with rationale for each inclusion and exclusion

Every non-obvious design decision includes a rationale (why threads over asyncio, why pynvml over nvidia-smi, why Braille charts over plotext). Documenting the *why* lets the implementation model resolve edge cases consistently instead of guessing.

---

## Stage 3 -- Writing the Implementation Instructions

**File:** `aidercommands.md`

Claude converts the architecture plan into a sequence of 25 self-contained implementation steps. Each step creates exactly one file and contains:

1. A `bash` code block with the aider command (which files to include)
2. A plain code block with the prompt (what to implement, with inlined specs)

This structure was arrived at through iteration. The early attempts failed in several ways:

- **Single-pass approach** (all 30 files at once) failed because errors cascaded across files and exhausted aider's reflection limit.
- **Batched approach** (groups of 2-6 files) failed on small-context models because the full architecture document plus multiple files exceeded the context window.
- **Architecture-as-context approach** (`--read Architecture Plan.md`) wasted context by loading 600 lines of spec when each step only needed the portion relevant to one file.

The final design -- one file per step, specs inlined, no external context document -- keeps each aider session small enough to fit in any reasonable context window while providing everything the model needs to write that specific file correctly.

---

## Stage 4 -- Automated Execution

**Files:** `run_aider.py`, `run_aider.md`

`run_aider.py` automates running all 25 steps. It:

1. Parses `aidercommands.md`, splitting on `## Step N` headers and extracting the bash command and prompt from each section
2. Calls aider for each step with `--message` (non-interactive mode) and `--yes` (auto-confirm file writes)
3. Stops on failure and prints a resume command

The script runs from the project root. It resolves the command file relative to its own directory (`LocalLLMCoding/`), while aider creates files in the current working directory (the project root).

### CLI Options

| Flag            | Description                                              |
|-----------------|----------------------------------------------------------|
| `file`          | Markdown file to read (default: `aidercommands.md`)      |
| `--from-step N` | Resume from step N after a failure                       |
| `--only-step N` | Rerun a single step                                      |
| `--model MODEL` | Override the aider model (default: `openai/qwen3-coder-30b` on vllm, `ollama_chat/qwen3-coder:30b` on ollama) |
| `--dry-run`     | Preview steps without calling aider                      |

### Local backend wiring

`run_aider.py` reads `Common/.env` and wires aider to the backend automatically. With the default `LLM_BACKEND=vllm` it sets `OPENAI_API_BASE` to the LLMConfig `/v1` gateway and passes `--model openai/<LLM_AIDER_MODEL>`; the gateway auto-loads the served-name model on first request:

```powershell
# default (vllm): no flags needed -- endpoint + model come from Common/.env
python .\LocalLLMCoding\run_aider.py
# -> OPENAI_API_BASE=http://192.168.1.15:11430/v1 , --model openai/qwen3-coder-30b
```

On the `ollama` backend it sets `OLLAMA_API_BASE` to the native Ollama server (`:11434`) instead and passes `--model ollama_chat/<LLM_AIDER_MODEL>` (e.g. `ollama_chat/qwen3-coder:30b`).

---

## Supporting Documentation

**File:** `nmonInstructions.md`

User guide for the nmon application itself -- installation, quick start, keyboard controls. Not part of the pipeline, but produced alongside the code.

**File:** `TechStack.md`

Detailed technical reference for nmon's internals: runtime stack, threading model, data flow, storage schema, GPU source abstraction, TUI design, NVAPI reverse-engineering findings, and configuration. Documents what was built, not how the pipeline built it.

**File:** `LLM-Assisted Development Workflow.md`

Retrospective on the entire workflow. Documents the problems encountered at each stage, the solutions that worked, and the best practices distilled from the experience. Covers prompt design, model selection, context management, and automation patterns.

---

## Model Selection

The local LLM needs to be a strong code generator that fits in VRAM with enough context window for single-file edits (~5000-6500 tokens per call). The default is the served-name `qwen3-coder-30b` (vllm backend); the gateway auto-loads it and its context is fixed at serve time, so VRAM tuning lives in the LLMConfig alias definition rather than here.

| Model                                | VRAM    | Notes                                          |
|--------------------------------------|---------|------------------------------------------------|
| `qwen3-coder-30b` (vllm served-name) | ~24 GB  | Default; best quality; recommended for 24 GB GPUs |
| `qwen3-coder:30b` (ollama tag)       | ~24 GB  | Same model on the `ollama` backend; tune `num_ctx` yourself |
| smaller 14B-class coder              | ~9 GB   | Good for 12 GB GPUs                             |
| smaller 7B-class coder               | ~5 GB   | Adequate; may struggle on complex files        |

On the `ollama` backend a 30B-class tag at a large `num_ctx` adds several GB of KV cache and can overflow a 24 GB card; create a reduced-context variant via `ollama create` (same weights, smaller window) so it fits fully on GPU. On the default `vllm` backend this is handled at serve time per alias instead.

---

## Key Design Principles

- **Separate planning from implementation.** Use the strongest available model for architecture; use a local model for code generation. Each role has different requirements.
- **One file per LLM call.** Eliminates cascading errors, minimises context usage, and makes failures easy to isolate and retry.
- **Inline specs, don't reference large documents.** Extract only the portion relevant to the current file. This is what made small local models viable.
- **Make instruction files machine-readable.** Consistent headers, predictable code block structure, and fixed language tags make automation trivial.
- **Build in resume from the start.** Long sequences will fail partway through. `--from-step N` means a failure at step 18 doesn't require rerunning steps 1-17.
- **Document rationale, not just decisions.** When the implementation model hits an ambiguity, the rationale in the architecture plan lets it resolve it the way the architect intended.
