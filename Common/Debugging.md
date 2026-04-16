# ArchPipeline.py — Debug / Test Plan

Staged test plan for the unified `ArchPipeline.py` orchestrator. Cheapest
checks first, real runs last.

**Environment assumed below:**
- cwd: `C:\Coding\WorkFolder\nmon`
- InitialPrompt.md: `C:\Coding\WorkFolder\nmon\LocalLLMCodePrompts\InitialPrompt.md`
- Derived repo-root (coding mode default): `C:\Coding\WorkFolder\nmon`
- Derived target-dir (coding mode default): `C:\Coding\WorkFolder\nmon\LocalLLMCodePrompts`

## 1. Argparse smoke tests (no side effects)

```bash
cd C:\Coding\LocalLLM_Pipeline\Common
python ArchPipeline.py --help
python ArchPipeline.py analysis --help
python ArchPipeline.py debug --help
python ArchPipeline.py coding --help
```

Catches import errors, missing modules, broken `register()` calls.

## 2. Dry-run each mode (no LLM, no workers)

```bash
# analysis -- run from the repo root
cd C:\Coding\WorkFolder\nmon
python C:\Coding\LocalLLM_Pipeline\Common\ArchPipeline.py analysis --dry-run --skip-lsp

# debug -- run from the repo root
cd C:\Coding\WorkFolder\nmon
python C:\Coding\LocalLLM_Pipeline\Common\ArchPipeline.py debug --target-dir src/nmon --dry-run

# coding, variant A -- run from repo root, point at the prompt explicitly
cd C:\Coding\WorkFolder\nmon
python C:\Coding\LocalLLM_Pipeline\Common\ArchPipeline.py coding --initial-prompt LocalLLMCodePrompts\InitialPrompt.md --dry-run

# coding, variant B -- drop into the prompts folder, use defaults
cd C:\Coding\WorkFolder\nmon\LocalLLMCodePrompts
python C:\Coding\LocalLLM_Pipeline\Common\ArchPipeline.py coding --dry-run
```

Watch for: resolved repo-root and target-dir printed match the values
above, the prompt file is discovered, no exceptions.

## 3. `--repo-root` path-agnostic test

Run each mode from an unrelated cwd (e.g. `C:\`) with explicit paths —
confirms cwd isn't silently relied on anywhere.

```bash
cd C:\
python C:\Coding\LocalLLM_Pipeline\Common\ArchPipeline.py analysis --repo-root C:\Coding\WorkFolder\nmon --dry-run --skip-lsp
python C:\Coding\LocalLLM_Pipeline\Common\ArchPipeline.py debug    --repo-root C:\Coding\WorkFolder\nmon --target-dir src/nmon --dry-run
python C:\Coding\LocalLLM_Pipeline\Common\ArchPipeline.py coding   --initial-prompt C:\Coding\WorkFolder\nmon\LocalLLMCodePrompts\InitialPrompt.md --dry-run
```

## 4. Real execution — smallest scope first

Run from `C:\Coding\WorkFolder\nmon\LocalLLMCodePrompts` (coding) or
`C:\Coding\WorkFolder\nmon` (analysis / debug). Stop to inspect output
after each step.

1. **coding**, `--from-stage 3 --skip-stage 3` → runs only Stage 0
   (cheapest; no LLM call if `Implemented Plans/` is empty).
   ```bash
   cd C:\Coding\WorkFolder\nmon\LocalLLMCodePrompts
   python C:\Coding\LocalLLM_Pipeline\Common\ArchPipeline.py coding --from-stage 3 --skip-stage 3
   ```
2. **coding**, one real stage at a time (`--from-stage 1 --skip-stage 2 3`, etc.).
3. **debug**, single-file target if possible.
   ```bash
   cd C:\Coding\WorkFolder\nmon
   python C:\Coding\LocalLLM_Pipeline\Common\ArchPipeline.py debug --target-dir src/nmon
   ```
4. **analysis** last (longest-running).
   ```bash
   cd C:\Coding\WorkFolder\nmon
   python C:\Coding\LocalLLM_Pipeline\Common\ArchPipeline.py analysis
   ```

## What to watch for

- Resolved paths printed at the top of each run match the expected values:
  - coding: repo-root = `C:\Coding\WorkFolder\nmon`,
    target-dir = `C:\Coding\WorkFolder\nmon\LocalLLMCodePrompts`.
  - analysis / debug: repo-root = `C:\Coding\WorkFolder\nmon`.
- `.progress` / `.debug_progress` files appear in the expected location
  (target-dir for coding, repo-root for debug).
- Resume behavior: interrupt with Ctrl+Q mid-run, rerun, confirm it picks
  up where it left off.
- Mode-mismatch guard fires when switching between default / `--local` /
  `--all-claude` without `--restart`.
- Prompt templates found at `Common/_pipeline/prompts/*.md` regardless of
  cwd.
