# Using LocalLLM_Pipeline Outputs with opencode

After running the coding, analysis, and debug pipelines on a project, you have a generated app plus a substantial set of artifacts under `architecture/`, `bug_reports/`, `test_gaps/`, and `.debug_changes.md`. This document explains how to plug those artifacts into an [opencode](https://opencode.ai) interactive session so you can debug, refine, and extend the app efficiently — without re-running the batch pipelines for every change.

The split is intentional:

- **Pipelines** are batch, end-to-end, model-driven generators of *findings* (bugs, gaps, interfaces, data flow).
- **opencode** is the interactive editor that gives you *hands* to act on those findings.

Three integration layers, in order of leverage.

---

## 1. Project-level `AGENTS.md` (biggest single win)

opencode auto-loads `<project-root>/AGENTS.md` for every session in that project. Putting a pointer-table of pipeline artifacts there means opencode discovers them on every prompt without any per-prompt reminders, and **without inlining their contents into the context window** — they're read on demand.

Create `<project-root>/AGENTS.md`:

```markdown
# <project-name> — agent instructions

## Stack
<one-line summary: language, framework, layout>

## Pipeline-generated references (read these on demand, do not inline them)

| When I ask about... | Read first |
|---|---|
| Overall architecture / what lives where | `architecture/OVERVIEW.md` |
| Cross-module data flow | `architecture/DATA_FLOW.md` |
| Function/method signatures + contracts | `architecture/INTERFACES.md` or `architecture/interfaces/<module>.iface.md` |
| Cross-references for a symbol | `architecture/xref_index.md` or use **serena** (`find_symbol`, `find_referencing_symbols`) |
| Call graphs / module graphs | `architecture/*.mmd` |
| Known bugs in a file | `bug_reports/src/<path>.md` |
| Missing test coverage | `test_gaps/src/<path>.gap.md` |
| Recent auto-applied fixes | `.debug_changes.md` at repo root |

Use Read, not Grep, on these. They're structured markdown meant to be read whole.

## Tool routing
- Library API questions: use **context7** MCP, not WebFetch.
- Symbol navigation: use **serena** MCP (`.serena_context/` was built by the pipeline; serena reads it directly).
- `python -c` via bash for any non-trivial arithmetic, regex testing, or data-format transformation.

## Project conventions
<repo-specific rules the agent can't infer from the code alone>

## What not to do
<deliberate design decisions the agent should NOT "fix">
- If `architecture/architecture.md` records a decision, treat it as authoritative unless I say otherwise.
```

The "What not to do" section is critical for projects where the pipeline made deliberate trade-offs (sync vs async, chosen UI framework, no-build-step constraints, etc.). Local Qwen tends to "improve" toward defaults; this is where you stop it.

---

## 2. Project-scoped slash commands

Drop these in `<project-root>/.opencode/command/` (singular `command`, not `commands` — same gotcha as global agent/command dirs). They make the common "pull the pipeline artifact for file X" flow one keystroke and route to the right model/agent automatically.

### `/bug <file>` — load a bug report and propose fixes

```markdown
---
description: Load the pipeline's bug report for a file and propose fixes
agent: review
---
Read `bug_reports/src/$ARGUMENTS.md` and the current source at `src/<package>/$ARGUMENTS`. For each bug listed that's still present, propose a minimal fix citing file:line. Use serena to confirm the symbol is still where the report says it is — the source may have moved since the report was generated.
```

### `/gaps <file>` — coverage gaps to new tests

```markdown
---
description: Load pipeline's test-gap report and propose concrete tests
model: ollama/qwen3:32b
---
Read `test_gaps/src/$ARGUMENTS.gap.md` and `src/<package>/$ARGUMENTS`. For each gap listed, draft a pytest test that would close it. Follow the `tests/conftest.py` fixture patterns — don't invent a new fixture style.
```

### `/iface <symbol>` — contract lookup

```markdown
---
description: Look up a symbol's declared interface from the pipeline
---
Search `architecture/INTERFACES.md` and `architecture/interfaces/*.iface.md` for `$ARGUMENTS`. Report its signature, preconditions, postconditions, and declared callers. If serena disagrees with the pipeline doc (file moved, signature drifted), say so.
```

### `/changes` — recent auto-applied fixes

```markdown
---
description: Show recent debug-pipeline auto-fixes
---
Read `.debug_changes.md`. Summarize the 10 most recent entries by file: what was reported, what was changed, whether the change looks sensible given the current code. Flag any that look like over-corrections.
```

### `/dataflow <module>` — trace data through the system

```markdown
---
description: Show how data flows through a module per the pipeline analysis
model: ollama/qwen3:32b
---
Read `architecture/DATA_FLOW.md`. Identify the section(s) referencing `$ARGUMENTS` and summarize the read/write paths and downstream consumers. Cross-check against current code with serena's `find_referencing_symbols`. Report any divergence.
```

### Pattern

Each command is a thin wrapper that:

1. Reads one specific pipeline artifact (no Grep — full Read).
2. Cross-references against the live code via serena, so stale doc claims get flagged rather than acted on.
3. Routes to the appropriate model — `qwen3:32b` for analysis-heavy tasks, the default `qwen3-coder:30b` for direct fix proposals.

Add more as patterns emerge in your debugging sessions.

---

## 3. What's already live (no action needed)

If you've followed the opencode setup guide:

- **Serena MCP** reads `architecture/.serena_context/` automatically at session start (via `--project-from-cwd`). `find_symbol` / `find_referencing_symbols` work against the pipeline's symbol index with zero per-project configuration. The pipeline's Stage 0 (analysis mode) is what populates this — running serena standalone would re-extract everything.
- **context7 MCP** handles any library-API question with current docs. Your global AGENTS.md should already steer toward it for "how do I use X" questions.
- **Global commands** (`/commit`, `/diff-review`, `/test-changed`, `/explain`) work in this project too and now compose with the project-level commands above.

---

## A typical debugging session

```powershell
cd <project-root>
opencode
```

Then inside the TUI:

1. `/bug crud.py` — pulls the bug report, proposes a fix grounded in the current code state.
2. Review the proposed fix; accept, push back, or ask for an alternative.
3. `/test-changed` — runs the tests covering whatever you just modified.
4. `/commit` — drafts a one-line commit message for the staged diff.
5. Move to the next file; `/changes` at the end of the session to reconcile what you did with what the debug pipeline already auto-applied.

For coverage work, swap step 1 for `/gaps <file>` and step 4 for `/pr` after batching several test additions into one branch.

---

## What about the bigger artifacts?

Some pipeline outputs (`architecture/architecture.md`, full `INTERFACES.md`) can be 50KB+ — large enough that auto-loading them into every session would eat 10-20% of a 32K context window. The pattern above keeps them out of the default load and only reads them when a slash command or explicit prompt asks for them.

If you find yourself referencing the same architecture section in every session, copy the *relevant excerpt* (not the whole file) into the project `AGENTS.md` under a "Background" section. That gets it into context for free without dragging in the 50KB the agent doesn't need.

---

## Batch auditing with `opencode run`

`opencode run --command <name> <args>` invokes any project-scoped slash command headlessly — so you can sweep `/bug` or `/gaps` over every artifact without opening the TUI per file. Useful as a **second-pass audit** of what debug-pipeline Step 5 already auto-applied: the `/bug` prompt explicitly checks whether each bug is still present and skips the ones Step 5 fixed.

### Which commands fit the pattern

- `/bug` and `/gaps` are **file-keyed** — each takes a relative source path as `$ARGUMENTS` and reads exactly one artifact. Batching them is a simple loop over the `.md` / `.gap.md` files under `bug_reports/src/` and `test_gaps/src/`.
- `/iface` is **symbol-keyed**, not file-keyed — there's no natural file iteration. The consolidated catalog already lives at `architecture/INTERFACES.md`; invoke `/iface <symbol>` interactively on demand instead.

### PowerShell loop

Run from the project root. Replace `<pkg>` with your repo's top-level source package (e.g. `phonebook` for rust_phonebook):

```powershell
$ErrorActionPreference = "Stop"
$opencode = "C:\Coding\Opencode\opencode.exe"
$outRoot  = "opencode_reviews"
New-Item -ItemType Directory -Force -Path "$outRoot\bug","$outRoot\gaps" | Out-Null

function Invoke-Slash {
    param($Command, $Arg, $OutFile)
    if (Test-Path $OutFile) { Write-Host "skip (exists): $Arg"; return }
    Write-Host "/$Command $Arg"
    & $opencode run --command $Command $Arg 2>&1 | Tee-Object -FilePath $OutFile | Out-Null
}

# /bug over every bug_reports artifact
$base = Resolve-Path "bug_reports\src\<pkg>"
Get-ChildItem $base -Recurse -Filter "*.md" |
    Where-Object { $_.Name -notmatch '^__init__' } |
    ForEach-Object {
        $rel  = $_.FullName.Substring($base.Path.Length + 1)
        $arg  = $rel -replace '\.md$',''
        $safe = $arg -replace '[\\/]','__'
        Invoke-Slash -Command bug -Arg $arg -OutFile "$outRoot\bug\$safe.md"
    }

# /gaps over every test_gaps artifact
$base = Resolve-Path "test_gaps\src\<pkg>"
Get-ChildItem $base -Recurse -Filter "*.gap.md" |
    Where-Object { $_.Name -notmatch '^__init__' } |
    ForEach-Object {
        $rel  = $_.FullName.Substring($base.Path.Length + 1)
        $arg  = $rel -replace '\.gap\.md$',''
        $safe = $arg -replace '[\\/]','__'
        Invoke-Slash -Command gaps -Arg $arg -OutFile "$outRoot\gaps\$safe.md"
    }
```

Outputs land in `opencode_reviews/bug/*.md` and `opencode_reviews/gaps/*.md`. The `Test-Path` skip makes the loop resumable — Ctrl+C mid-sweep is safe; re-running picks up where it stopped. `__init__.py` reports are filtered out because they're usually empty stubs.

### Runtime cost

Each `opencode run` call is an independent session, so serena re-indexes on every start (~30s on a warm cache, longer cold). ~10 files × 2 commands × 2–5 min per call on `qwen3:32b` is roughly **1–1.5 hr** over a LAN Ollama.

To amortize the MCP bootstrap, run `opencode serve` once in the background and pass `--attach http://localhost:<port>` to every `run` call — all invocations then share a warm serena. More complex to orchestrate; skip unless the per-call startup is a real blocker.

### Smoke-test one file first

Before committing to the full loop, run one artifact and read the output:

```powershell
& "C:\Coding\Opencode\opencode.exe" run --command bug crud.py
```

If it surfaces real second-pass findings, run the full loop. If the review is mostly "already fixed by Step 5," Step 5 did its job and the sweep will be redundant — skip it.

---

## Maintenance

- The pipeline-generated docs reflect the codebase at the time the pipeline ran. As you fix bugs in opencode, the docs **drift**. Don't auto-trust a 2-week-old `bug_reports/src/crud.py.md` — the bug may already be fixed.
- Re-run the relevant pipeline stage when drift becomes a problem. For just bug reports: `ArchPipeline.py debug --target-dir <pkg> --restart` and let it regenerate. For just architecture: `ArchPipeline.py analysis --skip-lsp` (the LSP setup doesn't need to redo).
- Commit the regenerated docs with their own commit so it's clear what came from the pipeline and what came from your hands.

---

## File locations cheat sheet

For the rustdeck_phonebook reference setup:

| Artifact | Path |
|---|---|
| Project AGENTS.md | `C:\Coding\WorkFolder\rustdeck_phonebook\AGENTS.md` |
| Project slash commands | `C:\Coding\WorkFolder\rustdeck_phonebook\.opencode\command\*.md` |
| Project agents | `C:\Coding\WorkFolder\rustdeck_phonebook\.opencode\agent\*.md` |
| Architecture docs | `C:\Coding\WorkFolder\rustdeck_phonebook\architecture\` |
| Bug reports | `C:\Coding\WorkFolder\rustdeck_phonebook\bug_reports\` |
| Test gaps | `C:\Coding\WorkFolder\rustdeck_phonebook\test_gaps\` |
| Debug change log | `C:\Coding\WorkFolder\rustdeck_phonebook\.debug_changes.md` |
| Serena context | `C:\Coding\WorkFolder\rustdeck_phonebook\architecture\.serena_context\` |

---

## See also

- `Common/Documentation/ArchPipeline.md` — pipeline orchestration reference (modes, stages, .env keys, resume semantics).
- `LocalLLMCoding/Documentation/run_aider.md` — Stage 4 internals (sanity check, runaway defenses, aider command construction).
- `C:\Coding\Opencode\opencode setup.md` — opencode + local Qwen setup tutorial (MCP servers, agents, commands).
- `C:\Coding\Opencode\opencode configuration.md` — exact configuration installed on this machine, with the errors hit and fixes applied.
