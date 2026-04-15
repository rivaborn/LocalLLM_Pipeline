# Example — Running `InitialPrompt.md` Through the Full Coding Pipeline

End-to-end walkthrough: take a human-written `InitialPrompt.md`, produce `aidercommands.md` via Claude + local LLM, then have aider implement it.

Concrete paths assume the project data lives at `C:\Coding\nmonLocalLLM\` and the target source code lives at `C:\Coding\nmon\`. Adjust for your layout.

---

## Two phases

### Phase 1 — Generate the plan + aider commands

From `C:\Coding\nmonLocalLLM` (where `LocalLLMCodePrompts/` and `Implemented Plans/` live):

```powershell
cd C:\Coding\nmonLocalLLM
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1
```

That's all. `-TargetDir` defaults to `LocalLLMCodePrompts` under cwd and finds your `InitialPrompt.md` automatically.

Stages processed (default mode):

| Stage | Engine | Input | Output |
|---|---|---|---|
| 0 Codebase Summary (skipped on first run) | local | All `Implemented Plans\*.md` | `Implemented Plans\Codebase Summary.md` |
| **1 Improve Prompt** | **Claude** (Sonnet) | `InitialPrompt.md` | `Implementation Planning Prompt.md` + `PromptUpdates.md` |
| 2a Section list | local | Improved prompt + summary | `.section_plan.md` (ephemeral) |
| 2b Per-section | local | same | `Implemented Plans\Plans\Section N.md` (one file per section) + consolidated `Architecture Plan.md` |
| 3a Step list | local | Architecture Plan | `.step_plan.md` (ephemeral) |
| 3b Per-step aider command | local | Architecture Plan (sliced per step) | `aidercommands.md` |

Preview the plan first if you want:

```powershell
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1 -DryRun
```

If it dies partway (rate limit, etc.) just re-run the same command — it auto-resumes from `.progress`.

### Phase 2 — Execute the aider commands

Change directory to where you want the source files written (your actual project), then point `run_aider.py` at the generated `aidercommands.md`:

```powershell
cd C:\Coding\nmon
python ..\LocalLLM_Pipeline\LocalLLMCoding\run_aider.py ..\nmonLocalLLM\LocalLLMCodePrompts\aidercommands.md --local
```

Aider's cwd is inherited from `run_aider.py`'s cwd, so paths like `src/nmon/models.py` in each step resolve to `C:\Coding\nmon\src\nmon\models.py`. `--local` makes aider use the Ollama server (`qwen3.5:27b`) via `OLLAMA_API_BASE` auto-set from `Common\.env`.

Preview first:

```powershell
python ..\LocalLLM_Pipeline\LocalLLMCoding\run_aider.py ..\nmonLocalLLM\LocalLLMCodePrompts\aidercommands.md --dry-run
```

Resume after a failure at step N:

```powershell
python ..\LocalLLM_Pipeline\LocalLLMCoding\run_aider.py ..\nmonLocalLLM\LocalLLMCodePrompts\aidercommands.md --local --from-step N
```

---

## Critical caveat for this layout

The two phases have **different cwd requirements**:

- **Phase 1** must run from `C:\Coding\nmonLocalLLM\` so `$ProjectRoot` resolves to the folder holding `LocalLLMCodePrompts/` and `Implemented Plans/`.
- **Phase 2** must run from `C:\Coding\nmon\` so aider writes to the actual project's `src/`.

If you run Phase 2 from `nmonLocalLLM\`, aider will try to create `nmonLocalLLM\src\nmon\...` files — wrong location.

---

## Full one-shot script (optional)

If you do this often enough to want a batch file:

```powershell
# Phase 1: plan
Push-Location C:\Coding\nmonLocalLLM
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1
Pop-Location
if ($LASTEXITCODE -ne 0) { exit 1 }

# Phase 2: execute
Push-Location C:\Coding\nmon
python ..\LocalLLM_Pipeline\LocalLLMCoding\run_aider.py ..\nmonLocalLLM\LocalLLMCodePrompts\aidercommands.md --local
Pop-Location
```

---

## What the Phase 1 banner looks like

```
Target directory: C:\Coding\nmonLocalLLM\LocalLLMCodePrompts
Claude CLI: Claude1
Claude model: per-stage defaults (Sonnet: 0,1,3b  Opus: 2a,2b,3a)
Ultrathink: per-stage defaults (ON: 2a,2b,3a  OFF: 0,1,3b)
Mode: default -- Stage 1 = Claude; Stages 0, 2a, 2b, 3a, 3b = local
  Local endpoint: http://192.168.1.126:11434
  Local model:    gemma4:26b (num_ctx=24576)
```

Confirms only Stage 1 calls Claude, everything else goes to the local server.

---

## How `InitialPrompt.md` flows through the pipeline

```
InitialPrompt.md                               (your rough human idea)
    │
    │  Stage 1 (Claude Sonnet)
    │  "Review, improve, and critique this prompt"
    ▼
Implementation Planning Prompt.md              (refined, unambiguous)
PromptUpdates.md                               (critique: what changed and why)
    │
    │  Stage 2a (local) — decompose into sections
    │  Stage 2b (local) — write each architecture section to its own file
    ▼
Implemented Plans\Plans\Section 1.md, Section 2.md, ...
Architecture Plan.md                           (consolidated at end of 2b)
    │
    │  Stage 3a (local) — decompose into implementation steps
    │  Stage 3b (local) — write each step as a self-contained aider prompt
    ▼
aidercommands.md                               (ready for Phase 2)
    │
    │  Phase 2: run_aider.py parses each step and invokes aider
    │  aider (ollama_chat/qwen3.5:27b) — creates/edits source files
    ▼
C:\Coding\nmon\src\nmon\...                    (implementation lands here)
```

---

## Variations

### Fully local (Claude rate-limited or offline)

Same two phases, but pass `-Local` to Phase 1 so Stage 1 also runs on the local LLM:

```powershell
cd C:\Coding\nmonLocalLLM
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1 -Local
```

Phase 2 is unchanged (`run_aider.py --local`).

### Fully Claude (Ollama server down)

```powershell
cd C:\Coding\nmonLocalLLM
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1 -AllClaude
```

Phase 2 would need a cloud aider model too:

```powershell
cd C:\Coding\nmon
python ..\LocalLLM_Pipeline\LocalLLMCoding\run_aider.py ..\nmonLocalLLM\LocalLLMCodePrompts\aidercommands.md --model gpt-4o
```

### Regenerate just the aider commands (skip re-planning)

If you've edited `Architecture Plan.md` by hand and only want `aidercommands.md` regenerated:

```powershell
cd C:\Coding\nmonLocalLLM
..\LocalLLM_Pipeline\LocalLLMCoding\Arch_Coding_Pipeline.ps1 -FromStage 3 -Restart
```

### Re-run a single aider step (bad generated file)

```powershell
cd C:\Coding\nmon
python ..\LocalLLM_Pipeline\LocalLLMCoding\run_aider.py ..\nmonLocalLLM\LocalLLMCodePrompts\aidercommands.md --local --only-step 12
```
