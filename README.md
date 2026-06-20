# LocalLLM Pipeline

A toolkit that drives **local LLMs (vLLM via LLMConfig, or Ollama)** and **Claude Code** through
three multi-stage pipelines run against *another* codebase:

- **coding**   — turn an `InitialPrompt.md` into an `Architecture Plan.md` + `aidercommands.md`, then execute it with [aider](https://aider.chat).
- **analysis** — generate per-file docs, a cross-reference index, Mermaid diagrams, and an architecture overview for each declared subsection.
- **debug**    — data-flow / interface / test-gap / bug-hunt analysis, then propose and apply fixes.

It is a toolkit, not a deployable app. You run it *from the target project's directory*; the current
working directory is treated as that repo's root for path resolution. The pipeline scripts locate
themselves via `__file__`, so the toolkit can live anywhere.

## How it works

A unified orchestrator (`Common/ArchPipeline.py`) registers four subcommands — `all`, `coding`,
`analysis`, `debug` — and dispatches each pipeline stage to either Claude Code or the local LLM
backend. The local backend defaults to **vLLM via the LLMConfig gateway** (`:11430/v1`, which
auto-loads the requested model onto the GPU on first request); set `LLM_BACKEND=ollama` to use a raw
Ollama server instead. Planning and review stages that need strong cross-file reasoning run on Claude
by default; bulk generation runs locally. Python modules orchestrate; per-step PowerShell workers do
the language-specific extraction.

```
Common/ArchPipeline.py        entry point (argparse shim)
Common/_pipeline/             Python orchestrator package
  config.py                     .env loading, backend/model/endpoint resolution
  claude.py                     claude CLI wrapper (account switching, streaming)
  ollama.py                     local LLM client (vLLM gateway /v1 + Ollama /api/chat)
  modes/                        one module per pipeline (all / analysis / debug / coding)
  prompts/                      stage prompt templates (stage0..stage3c)
Common/.env                   shared config (gitignored; copy from .env.example)
Common/Documentation/         per-module reference docs
LocalLLMAnalysis/             PowerShell workers for the analysis pipeline
LocalLLMDebug/                PowerShell workers for the debug pipeline
LocalLLMCoding/               aider runner + import-repair (stages 4-5)
legacy/                       pre-ArchPipeline.py entry points (unmaintained)
```

## Requirements

- **Python 3.10+**
- **PowerShell** (the per-step workers are `.ps1` scripts)
- **[LLMConfig](https://github.com/rivaborn/LLMConfig)** gateway reachable (default `http://192.168.1.40:11430`) serving the models you configure — or, with `LLM_BACKEND=ollama`, an **[Ollama](https://ollama.com)** server (`:11434`)
- **[Claude Code CLI](https://claude.com/claude-code)** (`claude` on PATH) for Claude-routed stages
- **[aider](https://aider.chat)** for coding-mode Stage 4
- Optional: a Python language-server / ctags toolchain for symbol resolution in analysis

## Setup

```bash
cp Common/.env.example Common/.env   # then edit Common/.env
```

At minimum set the backend, host, and models in `Common/.env`:

```ini
LLM_BACKEND=vllm                     # vllm (gateway /v1) | ollama (native)
LLM_HOST=192.168.1.40               # vllm -> :11430 gateway; ollama -> :LLM_PORT
LLM_PORT=11434
LLM_DEFAULT_MODEL=qwen3-coder-30b    # vLLM served-name (use a ':' tag on ollama)
LLM_PLANNING_MODEL=qwen3.5-27b       # planning stages; leave blank to reuse the default

# Describe the TARGET codebase the pipeline will operate on
PRESET=python
CODEBASE_DESC="..."

# Subsections the analysis pipeline iterates (one path per line)
#Subsections begin
src/mypackage
#Subsections end
```

Model resolution everywhere follows: role-specific key → `LLM_DEFAULT_MODEL` → hardcoded fallback.
To swap the coder model system-wide, edit `LLM_DEFAULT_MODEL` and nothing else. Use vLLM served-names
(`qwen3-coder-30b`) on the `vllm` backend and Ollama tags (`qwen3-coder:30b`) on `ollama` — the gateway
routes by id format.

## Usage

Always `cd` into the **target project** first — its directory is the repo root the pipeline reads
and writes.

```bash
# Full pipeline: coding -> analysis -> debug (default when no subcommand)
python <path-to-toolkit>/Common/ArchPipeline.py

# Individual modes
python <toolkit>/Common/ArchPipeline.py coding   --initial-prompt ./InitialPrompt.md
python <toolkit>/Common/ArchPipeline.py analysis
python <toolkit>/Common/ArchPipeline.py debug    --target-dir src/mypackage

# Preview without invoking any worker or LLM
python <toolkit>/Common/ArchPipeline.py <mode> --dry-run
```

Press **Ctrl+Q** to cancel between steps; progress is saved so a re-run resumes where it stopped
(pass `--restart` to start fresh).

### Common flags

| Flag | Mode | Effect |
|---------------------|----------|--------------------------------------------------------------|
| `--local`           | coding   | Run every stage on the local backend (vLLM/Ollama)           |
| `--all-claude`      | coding   | Run every stage on Claude Code                               |
| `--review`          | coding   | Enable Claude review + auto-fix at Stage 2c and 3c           |
| `--from-stage N`    | coding   | Skip stages `0..N-1`                                         |
| `--skip-stage N...` | coding   | Skip the listed stage numbers entirely                       |
| `--from-step N`     | coding   | Resume aider at step `N` inside `aidercommands.md`           |
| `--package-name X`  | coding   | Thread `src/X/` through every stage prompt                   |
| `--claude Claude1`  | coding   | Choose the Claude account (`Claude1` / `Claude2`)            |
| `--start-from N`    | analysis | Skip subsections `1..N-1`                                    |
| `--skip-lsp`        | analysis | Skip one-time compile-commands + symbol-extract setup        |
| `--target-dir DIR`  | debug    | Source directory to analyse (required)                       |
| `--from-section S`  | all      | Start the end-to-end run from `coding` / `analysis` / `debug`|
| `--restart`         | coding/debug | Ignore saved progress and start fresh                   |
| `--dry-run`         | any      | Print what would run without executing                      |

## Coding-mode stages

| Stage | Role | Default engine |
|-------|-----------------------------------|---------------------|
| 0     | Summarize implemented plans       | local               |
| 1     | Improve the initial prompt        | Claude              |
| 2a/2b | Section + per-section architecture| local               |
| 2c    | Architecture Plan review + auto-fix | Claude (`--review`) |
| 3a/3b | Step plan + per-step aider commands | local             |
| 3c    | `aidercommands.md` review + auto-fix | Claude (`--review`) |
| 4     | Execute `aidercommands.md` via aider | aider subprocess  |
| 5     | Post-generation import repair     | subprocess          |

## Testing

There is no root pytest config; run tests from their own directories:

```bash
cd LocalLLMCoding   && pytest test_sanity.py -v
cd LocalLLMAnalysis && pytest test_arch_analysis_pipeline.py -v
```

## Configuration reference

`Common/.env` controls the backend (`LLM_BACKEND`), endpoint, per-role model overrides, token /
context / timeout budgets, the target codebase `PRESET` and `CODEBASE_DESC`, file-include/exclude
regexes, and the `#Subsections begin/end` list the analysis pipeline iterates. When the analysis
pipeline has run, the debug pipeline can consume its outputs via `ARCHITECTURE_DIR` /
`SERENA_CONTEXT_DIR` (these integrations skip gracefully when the expected files are absent).

Per-module reference docs live in `Common/Documentation/`. Project conventions for contributors are
in `CLAUDE.md`.
