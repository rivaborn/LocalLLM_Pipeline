# run_aider.py — Usage Guide

Automates the nmon implementation by reading `aidercommands.md` and calling
aider once per step with the correct files and prompt.

## Layout

`run_aider.py` lives in `LocalLLMCoding/`. The prompt files it reads
(`aidercommands.md`, `InitialPrompt.md`, `Architecture Plan.md`, etc.) live in
a separate **prompts directory** — by default `LocalLLMCodePrompts/`. You invoke
the script from the project root:

```
C:\Coding\LocalLLM_Pipeline\              <- you run commands from here
├─ src/nmon/                         <- aider creates/edits files here
├─ tests/
├─ pyproject.toml
├─ LocalLLMCoding\                   <- script + documentation
│  └─ run_aider.py
├─ LocalLLMCodePrompts\              <- default prompts directory
│  ├─ aidercommands.md
│  ├─ Architecture Plan.md
│  ├─ Implementation Planning Prompt.md
│  └─ InitialPrompt.md
└─ Implemented Plans\                <- archived plans after completion
   ├─ Codebase Summary.md
   └─ archive\
```

The script resolves `aidercommands.md` relative to the **prompts directory**
(default `LocalLLMCodePrompts/`). Use `--prompts-dir` to point at a different
folder.

## Prerequisites

- Python 3.10+
- Aider installed and on your PATH: `pip install aider-chat`
- Ollama installed and running: https://ollama.com/download
- A Qwen 2.5 Coder model pulled locally (see [Model selection](#model-selection) for which one)

## Remote Ollama server

`run_aider.py` automatically reads `LLM_HOST` and `LLM_PORT` from
`LocalLLMAnalysis/.env` and sets `OLLAMA_API_BASE` for you. If your `.env`
contains:

```
LLM_HOST=192.168.1.126
LLM_PORT=11434
```

The script will print `Ollama API base: http://192.168.1.126:11434` on startup.
No manual environment variable needed.

You can also override this with `--ollama-url` or by setting `OLLAMA_API_BASE`
directly:

```powershell
# Override via flag
python .\LocalLLMCoding\run_aider.py --ollama-url http://192.168.1.126:11434

# Override via environment variable
$env:OLLAMA_API_BASE = "http://192.168.1.126:11434"
python .\LocalLLMCoding\run_aider.py
```

## Quick start

```powershell
python .\LocalLLMCoding\run_aider.py --model ollama/qwen2.5-coder:32b-12k
```

To use a different prompts folder:

```powershell
python .\LocalLLMCoding\run_aider.py --prompts-dir LocalLLMCodePrompts_V2 --model ollama/qwen2.5-coder:32b-12k
```

This runs all steps in order. Each step calls:
```
aider --message "<prompt>" --yes --model ollama/qwen2.5-coder:32b-12k <files>
```
Aider creates or edits the files, then exits. The script moves on to the next
step.

## Model selection

Aider's workload here is **whole-file edits on small Python files**. The nmon
modules are 50–200 lines and the prompts in `aidercommands.md` are detailed
and self-contained, so each aider call has:

- **Input:** ~3000–4500 tokens (system prompt + user prompt + file content)
- **Output:** up to ~2000 tokens (rewritten file content)
- **Total context needed:** ~5000–6500 tokens per call, worst case

A good model for this workflow meets two criteria:

1. **Strong at instruction-following and code generation.** Qwen 2.5 Coder is
   the best local option in its size class.
2. **Fits fully on your GPU with its context window.** The default `num_ctx`
   for `qwen2.5-coder:32b` is 32768, which adds ~8 GB of KV cache on top of
   the ~20 GB weights — enough to overflow a 24 GB card, force partial CPU
   offload, and slow inference ~4×. That's where timeouts come from.

### Best choice — `qwen2.5-coder:32b-12k` (~24 GB VRAM, fully resident)

A custom 12k-context variant of `qwen2.5-coder:32b`. Same weights as the base
model, smaller KV cache, fits fully on a 24 GB GPU. The 12288-token window
covers aider's worst-case input plus a 4000-token output for whole-file
rewrites of the largest nmon files.

**One-time setup on the Ollama server (Windows PowerShell):**

```powershell
ollama pull qwen2.5-coder:32b
@"
FROM qwen2.5-coder:32b
PARAMETER num_ctx 12288
"@ | Set-Content -Encoding ASCII Modelfile
ollama create qwen2.5-coder:32b-12k -f Modelfile
```

`ollama create` reuses the existing weights — no re-download. Takes a few
seconds, leaves the base `qwen2.5-coder:32b` untouched, and registers a new
tag you can target directly:

```powershell
python .\LocalLLMCoding\run_aider.py --model ollama/qwen2.5-coder:32b-12k
```

**Verify it fits fully on GPU** before starting a long run:

```powershell
ollama ps
```

Look at the `PROCESSOR` column — it should say **`100% GPU`**. Anything like
`75% GPU / 25% CPU` means partial offload and you'll hit timeouts on the
larger steps.

### VRAM fit on a 24 GB GPU

qwen 32b weights (q4_K_M) ≈ 19.85 GB. KV cache scales roughly linearly with
`num_ctx`:

| Variant                           | KV cache | Total    | Fit on 24 GB?              |
|-----------------------------------|----------|----------|----------------------------|
| `qwen2.5-coder:32b-8k`            | ~2.0 GB  | ~22.9 GB | Resident, fast             |
| **`qwen2.5-coder:32b-12k`**       | ~3.0 GB  | ~23.9 GB | **Resident, recommended**  |
| `qwen2.5-coder:32b-16k`           | ~4.0 GB  | ~24.9 GB | Partial CPU offload (slow) |
| `qwen2.5-coder:32b` (32k default) | ~8.0 GB  | ~28.9 GB | Partial CPU offload (slow) |

The 8k variant works for most steps but can clip on the largest nmon files
(TUI widgets, storage). **12k is the safer default for this workflow.** 16k
and above overflow a 24 GB card and should be avoided unless you have 32+ GB.

### If VRAM is limited — Qwen2.5-Coder 14B (~9 GB VRAM)

Fits comfortably on 12 GB cards at the default 32k context — no custom variant
needed. Roughly 80% of the 32b's quality in practice; still a capable coder.

```powershell
ollama pull qwen2.5-coder:14b
python .\LocalLLMCoding\run_aider.py --model ollama/qwen2.5-coder:14b
```

### Smaller fallback — Qwen2.5-Coder 7B (~5 GB VRAM)

May struggle on the larger TUI steps. Use `--only-step` to retry those with a
bigger model if needed.

```powershell
ollama pull qwen2.5-coder:7b
python .\LocalLLMCoding\run_aider.py --model ollama/qwen2.5-coder:7b
```

### Alternative — DeepSeek-Coder-V2 16B (~10 GB VRAM)

Strong on multi-file reasoning; a reasonable second pick if qwen 14b feels
shallow on your workload.

```powershell
ollama pull deepseek-coder-v2:16b
python .\LocalLLMCoding\run_aider.py --model ollama/deepseek-coder-v2:16b
```

### Don't use `devstral-small-2`

Despite the name, devstral is an **agentic** model — tuned for SWE-bench-style
"take action" tasks, not careful instruction-following. On aider workflows it
over-rewrites files, hallucinates changes beyond what the prompt asked for,
and routinely fails the "match the prompt exactly" criterion that
`aidercommands.md` depends on. It is the wrong tool for this workflow
regardless of VRAM headroom.

### Check available VRAM before choosing

```powershell
nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv
```

## Resuming after a failure

If a step fails the script stops and prints a resume command:

```
[STOPPED] Failed at step 8.
  Fix the issue then resume with: python .\LocalLLMCoding\run_aider.py --from-step 8
```

Run the suggested command to pick up where it left off:

```bash
python .\LocalLLMCoding\run_aider.py --from-step 8 --model ollama/qwen2.5-coder:32b-12k
```

## Re-running a single step

To rerun just one step without affecting the others:

```bash
python .\LocalLLMCoding\run_aider.py --only-step 12 --model ollama/qwen2.5-coder:32b-12k
```

Useful when a generated file has a bug and you want aider to rewrite it.

## Previewing steps without running

Check what the script will do before committing:

```bash
python .\LocalLLMCoding\run_aider.py --dry-run
```

Prints each step's title, aider command, and first line of the prompt. No files
are created or modified.

## Using a different prompts folder

Use `--prompts-dir` to point at a different set of prompts. This is useful for
iterative development — each iteration can have its own folder:

```powershell
python .\LocalLLMCoding\run_aider.py --prompts-dir LocalLLMCodePrompts_V2 --model ollama/qwen2.5-coder:32b-12k
```

The first positional argument overrides the markdown filename within the prompts
directory (default: `aidercommands.md`):

```powershell
# Read my_commands.md from the default prompts folder
python .\LocalLLMCoding\run_aider.py my_commands.md --model ollama/qwen2.5-coder:32b-12k
```

## Completed folder tracking

After all steps complete, the script:
1. Writes a `.completed` marker in the prompts folder
2. Copies `Architecture Plan.md` to `Implemented Plans/` with a sequential number
   (e.g. `Architecture Plan 2.md`) and a timestamp header

If you re-run against a completed folder, the script asks for confirmation:

```
This prompts folder has already been processed:
  C:\Coding\LocalLLM_Pipeline\LocalLLMCodePrompts
  Completed: 2026-04-13T10:30:00
  Steps run: 23

Run again? [y/N]
```

Use `--force` to skip the confirmation.

## All options

| Flag              | Description                                                        |
|-------------------|--------------------------------------------------------------------|
| `file`            | Markdown file to read (default: `aidercommands.md`)                |
| `--prompts-dir`   | Prompts folder (default: `LocalLLMCodePrompts`)                    |
| `--from-step N`   | Skip steps before N and start at N                                 |
| `--only-step N`   | Run exactly one step                                               |
| `--model MODEL`   | Aider model string, e.g. `ollama/qwen2.5-coder:32b-12k`           |
| `--ollama-url`    | Override Ollama API base URL                                       |
| `--force`         | Skip confirmation for already-completed folders                    |
| `--dry-run`       | Preview steps without calling aider                                |

## How it works

`aidercommands.md` is structured with one `## Step N` section per file.
Each section contains:

1. A `bash` code block — the aider command listing which files to include
2. A plain code block — the prompt describing what to implement

The script parses these two blocks per step and calls:
```
aider --message "<prompt>" --yes <files> --model <model>
```

The `--message` flag makes aider non-interactive: it processes the prompt,
writes the files, and exits automatically.

## Troubleshooting

**Ollama not responding** — Make sure the Ollama service is running:
```bash
ollama serve
```

**Token limit / context window errors** — The model's `num_ctx` is too small
for that step's input + output. If you're on a 32b variant, rebuild it with a
larger window:
```powershell
@"
FROM qwen2.5-coder:32b
PARAMETER num_ctx 16384
"@ | Set-Content -Encoding ASCII Modelfile
ollama create qwen2.5-coder:32b-16k -f Modelfile
python .\LocalLLMCoding\run_aider.py --only-step N --model ollama/qwen2.5-coder:32b-16k
```
Note that 16k overflows VRAM on a 24 GB card and will partial-offload — fine
for one-off retries on a single stuck step, not for a full run. If you're on
the 14b model, upgrade to 32b-12k.

**Timeouts / very slow inference** — The model is partially offloaded to CPU.
Check with `ollama ps` — the `PROCESSOR` column should say `100% GPU`. If it
says anything else, the model doesn't fit in VRAM at its current `num_ctx`.
Switch to a smaller context variant (`32b-12k` → `32b-8k`) or a smaller model
(`32b` → `14b`).

**Out of VRAM** — The model is too large for your GPU at any `num_ctx`. Switch
to the next size down (`32b` → `14b` → `7b`), or close other GPU-heavy
applications first.

**Aider not found** — Run `pip install aider-chat` and ensure your shell can
find it (`aider --version` should print a version number).

**Ollama server not reachable** — If the server is on another machine, make
sure `OLLAMA_API_BASE` is set (see [Remote Ollama server](#remote-ollama-server)
above). `aider --version` should print a version; if `aider` can't connect,
aider itself will print the URL it tried and error out.

**A generated file has import errors** — Run `--only-step N` to regenerate it.
If the error persists, edit `aidercommands.md` to clarify the prompt for that
step and rerun.

**Step succeeds but the code is wrong** — Run `pytest tests/ -v` after all
steps complete. Fix failures by running `--only-step N` on the relevant source
or test file.
