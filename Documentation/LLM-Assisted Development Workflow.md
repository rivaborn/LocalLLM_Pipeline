# LLM-Assisted Development Workflow
## Using Claude Code for Planning + a Local LLM for Implementation

---

## Overview

This document describes a two-stage workflow for building a non-trivial Python
application — in this case **nmon**, a real-time Nvidia GPU monitor — using two
complementary AI tools:

- **Claude Code** (this session) as the architect and orchestrator: writing the
  detailed plan, structuring the implementation instructions, and authoring the
  automation script.
- **A local LLM via aider** as the implementer: generating the actual source
  files, one at a time, from those instructions.

The key insight is that these two roles have very different requirements.
Planning requires broad reasoning, strong domain knowledge, and the ability to
hold a large design in mind at once. Implementation requires focused, precise
code generation within a tight context window. Matching each role to the right
tool avoids the worst failure modes of each.

---

## Stage 1 — Writing the Planning Prompt

The starting point was `Implementation Planning Prompt.md`, a structured specification that asked
for an architecture plan rather than code. Writing a good planning prompt turned
out to be as important as any other step. The prompt specified:

- **Target environment**: Windows 11, Python 3.10+, one or more Nvidia GPUs
- **Technology choices**: Rich for the TUI, SQLite for persistence, pynvml or
  nvidia-smi for GPU data
- **Functional requirements**: four screens, specific metrics, time windows,
  keyboard controls
- **Non-functional requirements**: graceful degradation, low overhead, hot-plug
  tolerance, portable config
- **Explicit deliverables**: what sections the output document must contain,
  down to the level of "function signatures with parameter types and pseudocode
  logic for each function"

This level of specificity matters. A vague prompt like "design a GPU monitor"
produces a vague plan. Spelling out every required section forces the plan to be
complete enough to hand off to a code-generation model without ambiguity.

---

## Stage 2 — Generating the Architecture Plan

Claude Code processed `Implementation Planning Prompt.md` and produced `Architecture Plan.md`, a
~600-line document covering:

1. Full directory tree
2. SQLite schema and Python dataclasses
3. Per-module breakdown with function signatures, pseudocode, and error handling
4. Data flow diagram from GPU → collector → DB → UI
5. TUI layout mapped to specific Rich components
6. `config.toml` schema with validation rules
7. Detailed test strategy: 40+ named test cases, mocking strategy, integration
   approach
8. Dependency list with rationale for inclusions and exclusions
9. Build and run instructions

Several design decisions were made explicitly and documented with rationale:

- **Threading over asyncio**: two daemon threads (collector, key reader) plus
  the main render loop is simpler and sufficient; asyncio adds complexity with
  no benefit at this scale.
- **pynvml over nvidia-smi**: direct library bindings avoid subprocess overhead
  and XML parsing; nvidia-smi kept as an auto-detected fallback.
- **Braille Unicode charts over plotext**: avoids an extra dependency; Braille
  block characters (U+2800–U+28FF) provide adequate resolution for terminal
  line charts.
- **SQLite WAL mode**: allows the TUI thread to read while the collector thread
  writes without blocking.

Documenting the *why* behind each decision makes the plan robust — when the
implementer (the local LLM) encounters an edge case, it has enough context to
resolve it consistently rather than guessing.

---

## Stage 3 — Writing the Implementation Instructions

The first attempt was a single `aidercommands.md` file asking aider to
"implement all 30 files in one continuous pass." This failed immediately, and
the path to a working solution involved several iterations.

### Problem 1: Aider stops after every step

**Symptom**: Aider treated each numbered item in the list as a separate task,
completed it, and waited for user input before continuing.

**Solution**: Two changes together:
1. Add the `--yes` flag so aider auto-confirms all file writes without prompting.
2. Reword the instruction header to explicitly say "implement all files in a
   single continuous pass without stopping."

Neither change alone was sufficient. `--yes` handles confirmation dialogs but
not conversational pauses; the explicit instruction handles conversational
pauses but not confirmation dialogs.

### Problem 2: "Only 3 reflections allowed, stopping"

**Symptom**: Aider generated a file, hit an error, tried to fix it up to three
times, then gave up and stopped entirely.

**Cause**: The scope was too large. When aider tries to create many files in one
session, early errors cascade — a broken import in file 3 causes file 7 to fail
too, and the reflection limit is exhausted before the root cause is resolved.

**Solution**: Split the 30 files into 7 batches of 2–6 files each, grouped by
dependency layer (models → GPU sources → storage → collector → TUI → tests).
Each batch was small enough that errors stayed isolated and fixable within the
reflection limit.

### Problem 3: Token limit exceeded with a small local model

**Symptom**:
```
Model openai/devstral-small-2 has hit a token limit!
Input tokens: ~27,232 of 0 -- possibly exhausted context window!
```

**Cause**: Even a single batch was too large for a small-context model when
combined with the `--read Architecture Plan.md` flag. The architecture document
alone consumed the majority of the context window before aider had generated a
single line of code.

**Root cause analysis**: There were two compounding issues:
1. The model (`devstral-small-2`) had a very small context window.
2. Loading the full architecture document as context was wasteful — each step
   only needed the spec for the one file it was creating, not all 600 lines.

**Solution**: Rewrite `aidercommands.md` as **25 single-file steps**, each
self-contained:
- One file per aider invocation, no batching.
- Drop `--read Architecture Plan.md` entirely.
- Inline the relevant specification directly in each step's prompt — only the
  function signatures and logic for that specific file.
- List only the direct dependencies of each file in the aider command, not every
  source file in the project.

This reduced the per-session context to: the new file being created + its
immediate imports + the inline prompt. A fraction of what the batched approach
required.

---

## Stage 4 — Automating the Steps with a Script

Running 25 separate aider invocations by hand — copy-pasting the command, then
the prompt — is tedious and error-prone. The next step was `run_aider.py`, a
script that:

1. **Parses** `aidercommands.md`, splitting on `## Step N` headers and
   extracting the `bash` code block (aider command) and the plain code block
   (prompt) from each section.
2. **Builds** the aider CLI call: `aider --message "<prompt>" --yes <files>
   [--model MODEL]`. The `--message` flag is the key — it makes aider
   non-interactive, processing the prompt and exiting immediately.
3. **Runs** each step in sequence, stops on failure, and prints a
   `--from-step N` resume command so the session can be restarted from the
   point of failure.

Additional flags: `--from-step N` to resume, `--only-step N` to rerun a single
file, `--dry-run` to preview without executing, `--model` to override the model
for all steps.

The structure of `aidercommands.md` as machine-readable markdown — consistent
section headers, exactly two code blocks per step with predictable language
tags — was not accidental. Writing the instruction file with the automation
script in mind made parsing trivial and removed a whole class of potential
failures.

---

## Stage 5 — Model Selection for Local Inference

The final question was which local model to use. The answer depends on
available VRAM:

| Model                 | VRAM    | Role                                 |
|-----------------------|---------|--------------------------------------|
| `qwen3.5:27b`         | ~18 GB  | Current default for `run_aider.py`   |
| `qwen3-coder:30b`     | ~20 GB  | Current default for analysis scripts |
| `qwen2.5-coder:32b`   | ~20 GB  | Older but proven                     |
| `qwen2.5-coder:14b`   | ~9 GB   | Good for most steps on 12 GB cards   |
| `qwen2.5-coder:7b`    | ~5 GB   | Adequate; may struggle on TUI files  |
| `deepseek-coder-v2:16b` | ~10 GB | Strong alternative                   |

The general principle: for code generation in a structured step-by-step context,
a dedicated coding model outperforms a general-purpose model of the same size.
Coder-tuned models are purpose-trained on code; a general model at the same
VRAM cost will do worse on implementation tasks.

Model tags and per-request context windows live in
`LocalLLM_Pipeline/Common/.env` -- switching models is a one-line edit. See
[`run_aider.md`](run_aider.md) and [`Generate Aider Commands.md`](Generate%20Aider%20Commands.md)
for the current defaults and override precedence.

---

## Stage 6 — Running Planning on a Local LLM

The workflow originally used Claude for every stage of
`Arch_Coding_Pipeline.ps1`. Later iterations added a `-Local` mode that keeps
Claude for the two high-leverage one-shot stages (Codebase Summary, Prompt
refinement) but routes the high-volume architectural decomposition and step
generation to the same local Ollama server that `run_aider.py` uses:

```powershell
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1 -Local
python ..\LocalLLM_Pipeline\LocalLLMCoding\run_aider.py --local
```

With `-Local`:

- Stage 0 (Codebase Summary) and Stage 1 (Improve Prompt) stay on Claude.
- Stage 2a/2b (Architecture Plan) and Stage 3a/3b (Aider Commands) run against
  the local LLM using `LLM_PLANNING_MODEL` / `LLM_PLANNING_NUM_CTX` from
  `Common/.env`.
- Stage 2b writes each section to its own file under
  `Implemented Plans/Plans/Section N.md` -- the local model never edits a
  consolidated document, and a later section cannot corrupt an earlier one.
- Stage 3b parses `Architecture Plan.md` by `##` headings and injects only the
  sections relevant to each step's files, keeping the prompt inside the local
  model's context window.

The split is pragmatic: Opus-class judgement is most valuable for prompt
distillation and cross-iteration synthesis, while the repetitive per-section
and per-step work is a good fit for a capable local model once the prompt is
already well-specified.

---

## Best Practices

### 1. Separate planning from implementation

Use your strongest available model (Claude, GPT-4o, etc.) for architecture and
planning. Use a local model for code generation. The planning model needs to
reason about the whole system; the implementation model only needs to generate
one file at a time.

### 2. Write the spec before writing the prompt

The planning prompt (`Implementation Planning Prompt.md`) was explicit about every required output
section. Vague prompts produce vague plans. List exactly what the output
document must contain, down to the level of function signatures and pseudocode.

### 3. Document rationale, not just decisions

Every non-obvious architectural choice in `Architecture Plan.md` includes a
"why". When the implementation model encounters an ambiguity or edge case, it
resolves it in the direction the architect intended rather than guessing. This
matters more the smaller the model is.

### 4. Scope each LLM call to one file

The single most effective change in this workflow was reducing each aider
session to exactly one file. This:
- Eliminates cascading errors across files.
- Minimises context window usage.
- Makes failures easy to isolate and retry.
- Keeps the prompt focused, which improves output quality.

### 5. Make instruction files machine-readable from the start

Write `aidercommands.md` (or equivalent) with consistent, parseable structure —
predictable headers, a fixed number of code blocks per section, consistent
language tags. Doing this makes automation trivial and avoids a parsing layer
that could introduce its own bugs.

### 6. Use `--message` for non-interactive automation

Aider's `--message` flag runs a single prompt and exits. Combined with `--yes`
for auto-confirmation, it transforms an interactive tool into a batch processor.
Any tool with a non-interactive mode should be used that way in automation.

### 7. Always include a resume mechanism

Long automated sequences will fail partway through. `run_aider.py`'s
`--from-step N` flag means a failure at step 18 doesn't require rerunning
steps 1–17. Design retry and resume into the automation before you need it.

### 8. Match model size to context requirements

Check the context window of the model before choosing it. A model that can't
fit the prompt + output in its window will either truncate silently or fail
loudly. For single-file code generation, even a 7B model with a 32k context
window is usable; a 32B model with an 8k context window is not.

### 9. Inline specs; don't reference large documents

Passing a 600-line architecture document as context to every step wastes the
context window and fills it with irrelevant content. Extract only the portion
relevant to the current file and inline it in the prompt. This is the step
that unlocked the use of small local models in this workflow.

### 10. Verify with tests before trusting the output

The final step of `aidercommands.md` instructs aider to run `pytest tests/ -v`
and fix failures before stopping. Automated code generation without automated
verification is incomplete. The test suite was specced at the planning stage
precisely so it could serve as the acceptance criterion at the implementation
stage.
