# Debug Workflow Quickstart

Condensed reference for the nmon2 LLM-assisted debugging pipeline. See `DebugWorkflow.md` for full details.

All scripts run from the **repo root**, read `LocalLLM_Pipeline/Common/.env`, and call
the local Ollama server via the shared `Invoke-LocalLLM` helper in
`LocalLLM_Pipeline/Common/llm_common.ps1`.

---

## Model Selection

The default is `qwen3-coder:30b` with a per-request `num_ctx=32768` passed via
the native Ollama `/api/chat` endpoint. One model, one tag, one config -- no
custom `Modelfile` variants needed.

```ini
# LocalLLM_Pipeline/Common/.env (snippet)
LLM_DEFAULT_MODEL=qwen3-coder:30b   # universal fallback; LLM_MODEL etc. chain to this
LLM_NUM_CTX=32768
LLM_TIMEOUT=300
```

One-time pull on the Ollama server:

```powershell
ollama pull qwen3-coder:30b
```

Verify it fits in VRAM:

```powershell
ollama ps    # PROCESSOR column should say "100% GPU"
```

If `PROCESSOR` shows any CPU fraction, drop `LLM_NUM_CTX` (e.g. to 16384 or
12288) or switch to a smaller model -- see below.

### VRAM notes

KV cache scales roughly linearly with `num_ctx`. On a 24 GB card at
`num_ctx=32768`, expect 17-20 GB of weights plus 5-8 GB of KV cache. If this
overflows, the simplest fixes are (1) lower `LLM_NUM_CTX`, or (2) switch to a
smaller model:

```ini
# Smaller-GPU config (12 GB card)
LLM_DEFAULT_MODEL=qwen2.5-coder:14b
LLM_NUM_CTX=32768
LLM_TIMEOUT=180
```

The 14b model finds ~80% of what the 30b finds, but it's a real reviewer (not
an agentic one) and converges well on the iterative bug-fix loop.

### How per-request `num_ctx` works

When `LLM_NUM_CTX > 0`, `Invoke-LocalLLM` POSTs to `/api/chat` with
`options.num_ctx` set. When 0 or unset, it falls back to the legacy
`/v1/chat/completions` path (no per-request override, model uses its default
window). Scripts don't need to know which path they're using -- they just
call `Invoke-LocalLLM` and the helper picks the right endpoint.

### Running the scripts

```powershell
..\LocalLLM_Pipeline\LocalLLMDebug\bughunt_local.ps1
..\LocalLLM_Pipeline\LocalLLMDebug\dataflow_local.ps1
..\LocalLLM_Pipeline\LocalLLMDebug\bughunt_iterative_local.ps1 -TargetDir src/nmon
..\LocalLLM_Pipeline\LocalLLMDebug\interfaces_local.ps1
..\LocalLLM_Pipeline\LocalLLMDebug\testgap_local.ps1
```

All five pick up their model via `Get-LLMModel -RoleKey 'LLM_MODEL'` (chains
`LLM_MODEL` → `LLM_DEFAULT_MODEL` → fallback) and `LLM_NUM_CTX` from
`Common/.env`. Per-request `num_ctx` covers the high-context synth passes
in the three "heavy" scripts (`bughunt_iterative`, `interfaces`, `testgap`)
— no separate high-ctx model variant is needed.

**Don't use `devstral-small-2`**: agentic model, not a reviewer. It
over-rewrites, hallucinates findings, and triggers the `BLOAT` / `DIVERGING`
guards constantly. Every convergence guard in the script exists because of
what devstral did on earlier runs.

---

## Optional: LocalLLMAnalysis integration

Three Debug scripts can pick up LocalLLMAnalysis artifacts when available:

| Debug script           | Consumes                                 | `.env` key             |
|------------------------|------------------------------------------|------------------------|
| `bughunt_local.ps1`    | `<ARCH>/xref_index.md`                   | `ARCHITECTURE_DIR`     |
| `dataflow_local.ps1`   | `<ARCH>/architecture.md` (synth pass)    | `ARCHITECTURE_DIR`     |
| `interfaces_local.ps1` | `<SERENA>/<rel>.serena_context.txt`      | `SERENA_CONTEXT_DIR`   |

Default values in `Common/.env`: `ARCHITECTURE_DIR=architecture`,
`SERENA_CONTEXT_DIR=.serena_context`. Both skip silently if the path doesn't
exist -- run `Arch_Analysis_Pipeline.py` first to generate them, then update
`ARCHITECTURE_DIR` to point at the renamed `N. <subsection>` folder.

**Quick caveats (see `DebugWorkflow.md` for full detail):**

- **Cache.** `interfaces_local.ps1` SHA1-caches extractions on the source hash
  — run `-Clean` after enabling `SERENA_CONTEXT_DIR` so existing files get
  re-extracted with LSP context.
- **Tokens.** Injections add 2-10K tokens per call. If prompts hit the
  window ceiling, bump `LLM_NUM_CTX` or slim the injected file.
- **VRAM.** Each +8K of `num_ctx` costs ~1 GB of KV cache on a 24 GB card.
  Confirm with `ollama ps` that `PROCESSOR` stays at `100% GPU` — anything
  less means partial offload and 3-4× slower inference.

---

## Pipeline at a Glance

| Phase | Script                        | Purpose                                                 | Output                       |
|-------|-------------------------------|---------------------------------------------------------|------------------------------|
| 1     | `Arch_Analysis_Pipeline.py`             | Architecture docs (per-file + overview + xref + graphs) | `1. src/`                    |
| 1     | `dataflow_local.ps1`          | Cross-module data flow trace                            | `architecture/DATA_FLOW.md`  |
| 1     | `interfaces_local.ps1`        | Function contracts + silent failure modes               | `architecture/INTERFACES.md` |
| 2     | `bughunt_local.ps1`           | Single-pass bug scan (no fixes)                         | `bug_reports/`               |
| 3     | `testgap_local.ps1`           | Test coverage gap audit                                 | `test_gaps/GAP_REPORT.md`    |
| 4     | `bughunt_iterative_local.ps1` | Iterative analyse-and-fix loop                          | `bug_fixes/`                 |

**Recommended order:** Arch_Analysis_Pipeline → dataflow → interfaces → bughunt → testgap → bughunt_iterative.

All outputs are SHA1-cached — re-runs only re-process changed files. Use `-Clean` to wipe outputs and caches; `-Force` (where supported) ignores the cache without deleting outputs.

---

## Script Options

### `python LocalLLMAnalysis/Arch_Analysis_Pipeline.py` (in LocalLLMAnalysis)
Orchestrates the six-step architecture pipeline over each subsection in `.env`.

| Option           | Description                                                                 |
|------------------|-----------------------------------------------------------------------------|
| `--dry-run`      | Show commands without running them                                          |
| `--start-from N` | Skip the first N-1 subsections                                              |
| `--skip-lsp`     | Omit `generate_compile_commands` + `serena_extract` (always use for Python) |

### `bughunt_local.ps1`
Single-pass, non-modifying bug scanner. Writes `bug_reports/<rel>.md` + `SUMMARY.md`.

| Option              | Default | Description                               |
|---------------------|---------|-------------------------------------------|
| `-TargetDir <path>` | `.`     | Directory to scan                         |
| `-Clean`            | off     | Delete all reports + caches, then re-scan |
| `-Force`            | off     | Ignore SHA1 cache, re-scan every file     |
| `-EnvFile <path>`   | `.env`  | Alternate env file                        |

### `bughunt_iterative_local.ps1`
Iterative analyse-and-fix loop running up to 4 analysis types per iteration. Stages fixes in `bug_fixes/`. Tracks the best (lowest-HIGH) version across iterations and reverts to it on any non-CLEAN exit, so a bad run can never leave a file worse than it started.

| Option               | Default | Description                            |
|----------------------|---------|----------------------------------------|
| `-TargetDir <path>`  | `.`     | Source directory to process            |
| `-TestDir <path>`    | `tests` | Test directory for the test loop       |
| `-MaxIterations <n>` | `3`     | Max fix attempts per file              |
| `-ApplyFixes`        | off     | Write fixes back to source immediately |
| `-SkipBugs`          | off     | Disable bug analysis                   |
| `-SkipDataflow`      | off     | Disable data-flow analysis             |
| `-SkipContracts`     | off     | Disable contract analysis              |
| `-SkipTests`         | off     | Disable test-quality loop              |
| `-Clean`             | off     | Delete all output + caches             |
| `-Force`             | off     | Ignore SHA1 cache                      |
| `-EnvFile <path>`    | `.env`  | Alternate env file                     |

Stop statuses: `CLEAN`, `MAX_ITER`, `DIVERGING`, `BLOAT`, `STUCK`, `SYNTAX_ERR`, `ERROR`.

Convergence guards (tune via `.env`, not CLI):

| Env key                   | Default | Effect                                                |
|---------------------------|---------|-------------------------------------------------------|
| `BUGHUNT_BLOAT_RATIO`     | `1.5`   | Reject fix that grows file beyond this ratio vs. orig |
| `BUGHUNT_BLOAT_MIN_SLACK` | `15`    | Absolute line floor for tiny files                    |
| `BUGHUNT_DIVERGE_AFTER`   | `2`     | Abort after N consecutive non-improving iterations    |
| `LLM_TIMEOUT`             | `300`   | Per-LLM-call timeout (seconds); other scripts use 120 |

### `dataflow_local.ps1`
Two-pass extraction + synthesis. Produces `architecture/DATA_FLOW.md`.

| Option              | Default | Description                           |
|---------------------|---------|---------------------------------------|
| `-TargetDir <path>` | `.`     | Restrict extraction to a subdirectory |
| `-Clean`            | off     | Delete all extractions + output       |
| `-EnvFile <path>`   | `.env`  | Alternate env file                    |

No `-Force`. To re-extract one file, delete its `<sha1>.txt` from `architecture/.dataflow_state/extractions/`.

### `testgap_local.ps1`
Maps source → test files, runs per-file gap analysis, then synthesises a prioritised gap report.

| Option            | Default | Description            |
|-------------------|---------|------------------------|
| `-SrcDir <path>`  | `src`   | Source root            |
| `-TestDir <path>` | `tests` | Test root              |
| `-Clean`          | off     | Delete output + caches |
| `-EnvFile <path>` | `.env`  | Alternate env file     |

No `-Force` or `-TargetDir`. Per-file invalidation: delete the entry in `test_gaps/.testgap_state/cache/`.

### `interfaces_local.ps1`
Extracts per-function contracts (Requires / Guarantees / Raises / Silent failure / Thread safety), then synthesises `INTERFACES.md`.

| Option              | Default | Description                       |
|---------------------|---------|-----------------------------------|
| `-TargetDir <path>` | `.`     | Restrict to a subdirectory        |
| `-Clean`            | off     | Delete all contract docs + caches |
| `-EnvFile <path>`   | `.env`  | Alternate env file                |

---

## Quick Recipes

**Full cold start**
```powershell
python LocalLLMAnalysis/Arch_Analysis_Pipeline.py --skip-lsp
..\LocalLLM_Pipeline\LocalLLMDebug\dataflow_local.ps1
..\LocalLLM_Pipeline\LocalLLMDebug\interfaces_local.ps1
..\LocalLLM_Pipeline\LocalLLMDebug\bughunt_local.ps1
..\LocalLLM_Pipeline\LocalLLMDebug\testgap_local.ps1
```

**Wrong GPU metric on screen**
```powershell
..\LocalLLM_Pipeline\LocalLLMDebug\bughunt_iterative_local.ps1 -TargetDir src/nmon/gpu -SkipTests
```
Then read `DATA_FLOW.md` (Handoff Points) + `INTERFACES.md` (Silent Failure Modes).

**Tests pass but production broken**
```powershell
..\LocalLLM_Pipeline\LocalLLMDebug\testgap_local.ps1
..\LocalLLM_Pipeline\LocalLLMDebug\bughunt_iterative_local.ps1 -SkipBugs -SkipDataflow -SkipContracts
```

**Auto-fix one subsystem on a branch**
```powershell
..\LocalLLM_Pipeline\LocalLLMDebug\bughunt_iterative_local.ps1 -TargetDir src/nmon -ApplyFixes
```

---

## What to Load Into Claude Code

| Question                     | File                                                          |
|------------------------------|---------------------------------------------------------------|
| What does this module do?    | `1. src/<rel>.md`                                             |
| Where does data flow?        | `architecture/DATA_FLOW.md`                                   |
| What can fail silently here? | `architecture/INTERFACES.md` (or `interfaces/<rel>.iface.md`) |
| Known bugs?                  | `bug_reports/SUMMARY.md` → specific `bug_reports/<rel>.md`    |
| What did the fixer do?       | `bug_fixes/<rel>.iter_log.md`                                 |
| Test coverage?               | `test_gaps/GAP_REPORT.md`                                     |
