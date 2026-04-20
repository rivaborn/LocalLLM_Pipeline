# Using the analysis + debug outputs in a debugging session

## What each file is for

| File                  | Produced by                          | Scale         | Use it to answer...                                                  |
|-----------------------|--------------------------------------|---------------|----------------------------------------------------------------------|
| `DATA_FLOW.md`        | debug step 1 (`dataflow_local.ps1`)  | whole target  | "Where does this value get transformed? Which boundary corrupts it?" |
| `INTERFACES.md`       | debug step 2 (`interfaces_local.ps1`)| whole target  | "What does this function promise? What are its silent failure modes?"|
| `xref_index.md`       | analysis step 2 (`archxref.ps1`)     | whole repo    | "Where is symbol X defined? Who calls it? Who owns this global?"     |
| `callgraph.md`        | analysis step 3 (`archgraph.ps1`)    | whole repo    | "What's the call-path into/out of this function, in text form?"      |
| `callgraph.mermaid`   | analysis step 3                      | whole repo    | Visual version of callgraph.md -- paste into a Mermaid viewer        |
| `subsystems.mermaid`  | analysis step 3                      | whole repo    | High-level subsystem dependency map -- "does this bug cross layers?" |

DATA_FLOW and INTERFACES are **debug-pipeline** outputs; the four xref/graph files are **analysis-pipeline** outputs. You generally want both pipelines to have run before you start a serious debug session.

## Concrete debugging workflow

Given a bug report ("value X is wrong on screen" / "module Y silently returns empty"):

1. **Locate the symbol.** Open `xref_index.md`, search for the function / class / variable name. It gives you the defining file and every caller -- that's the scope of the bug.

2. **Understand the contract.** Open `INTERFACES.md`, jump to the "All Silent Failure Modes" and "Cross-Module Obligations" sections for the defining file. Contract violations (wrong return shape, missing side-effect, swallowed exception) are usually already named here.

3. **Trace the data.** Open `DATA_FLOW.md`, find the "Handoff Points" section for the boundary where the value crosses (I/O, subsystem boundary, thread boundary). Bugs that express as "wrong value displayed" almost always live at a handoff.

4. **Walk the call graph.** Open `callgraph.md` (or `callgraph.mermaid` in a viewer) and follow edges upward from the suspect function to find every caller path. Use this to decide where the fix applies -- is it one caller misusing the API, or is the callee broken for everyone?

5. **Check blast radius.** Open `subsystems.mermaid` to confirm whether the bug is contained to one subsystem or crosses a boundary. Cross-boundary bugs imply both the caller AND callee tests may need updating.

6. **Drill into per-file reports.** Once steps 1-5 narrow the file, load `architecture/interfaces/<file>.iface.md` and `bug_reports/<file>.md` alongside the source in Claude Code. This is the pattern `Overview/How to use with Debug data Claude.md` already describes.

## Which file to load first, by symptom

| Symptom                                    | Load first                                         |
|--------------------------------------------|----------------------------------------------------|
| Wrong value displayed                      | `DATA_FLOW.md` -> Handoff Points                   |
| Silent failure / no error, wrong behaviour | `INTERFACES.md` -> Silent Failure Modes            |
| Changed one module, broke another          | `INTERFACES.md` -> Cross-Module Obligations        |
| "Where is this even used?"                 | `xref_index.md`                                    |
| Tracing a deep call path                   | `callgraph.md` (text) or `callgraph.mermaid` (viz) |
| Architectural question / layering concern  | `subsystems.mermaid`                               |
| Bug slipped past tests                     | `test_gaps/GAP_REPORT.md` (pairs with the six)     |

## Rules of thumb

- **Summaries first, per-file reports second.** The six files above all fit in a Claude context window; per-file `*.iface.md` / `*.md` reports are for drilling in after you've localized.
- **Mermaid files are for your eyes, not Claude's.** Paste `subsystems.mermaid` or `callgraph.mermaid` into a viewer (VS Code Mermaid preview, mermaid.live) to eyeball cycles. For LLM reasoning, the text equivalents (`callgraph.md`, `xref_index.md`) are denser and cheaper to ingest.
- **Regenerate after fixes.** Both pipelines are SHA-cached per file -- re-running only refreshes what changed. Keep the reports current as you fix things, otherwise your cross-reference drifts from the code.
- **Claude Code loading pattern** -- when handing off to Claude:

  ```
  Read architecture/INTERFACES.md
  Read architecture/DATA_FLOW.md
  Read bug_reports/SUMMARY.md
  Read test_gaps/GAP_REPORT.md
  ```

  Then describe the symptom. This is the "general debugging session" loadout from the existing Overview doc.

## Using with Cline (VS Code extension)

Cline treats its chat as the context window and the repo as its workspace, so the workflow hinges on three Cline-native mechanisms: **@-mentions**, **Plan Mode**, and **`.clinerules`**. None of that works unless the workspace is opened correctly -- do the setup section first.

### Setup: open the repo root in VS Code

Cline treats the folder you open with **File -> Open Folder** as the workspace root. `.clinerules`, `architecture/`, `bug_reports/`, `test_gaps/`, and `pyproject.toml` all live at the repo root -- so open the repo root, not `src/` or any subfolder.

1. **File -> Open Folder...** (or `Ctrl+K Ctrl+O`)
2. Select the target project's repo root (e.g. `C:\Coding\WorkFolder\nmonLocalLLM`)
3. "Do you trust the authors?" -> Yes (needed for `.venv` activation and extensions)
4. Confirm the Explorer sidebar shows `.clinerules` and `architecture/` at the top level

**Pick the Python interpreter.** `Ctrl+Shift+P` -> **Python: Select Interpreter** -> `.\.venv\Scripts\python.exe`. Cline runs tests and mypy via the integrated terminal, which auto-activates `.venv`, so editor feedback and Cline feedback stay consistent.

**Verify `.clinerules` is active.** Start a new Cline task and ask:

```
What rules are loaded from .clinerules for this workspace?
```

Cline should echo rules back (e.g. the src-layout import rule, Textual event-loop rule). If it replies that no rules are loaded, you opened the wrong folder.

**Companion extensions to install once.** These reinforce what `.clinerules` declares, so the editor flags violations before Cline commits them:

| Extension                            | Why                                                |
|--------------------------------------|----------------------------------------------------|
| **Python** (Microsoft)               | Interpreter selection, debugging                   |
| **Pylance**                          | Type checking + autocomplete                       |
| **Ruff**                             | Matches the linter declared in `pyproject.toml`    |
| **Markdown Preview Mermaid Support** | Renders `callgraph.mermaid` / `subsystems.mermaid` |
| **Mypy Type Checker** (Microsoft)    | Live `mypy --strict` feedback                      |

### Sanity checklist before starting a debug task

- [ ] VS Code title bar shows the correct repo root
- [ ] Explorer shows `.clinerules` at the top level
- [ ] `architecture/` folder exists (pipeline artifacts have been generated)
- [ ] Python interpreter (bottom-left status bar) points at the repo's `.venv`
- [ ] Cline panel loads without "no workspace" warnings
- [ ] Cline echoes back a rule from `.clinerules` when asked

### 1. @-mention the summary files at task start

In Cline's chat, type `@` to open the file picker and attach:

```
@/architecture/INTERFACES.md
@/architecture/DATA_FLOW.md
@/architecture/xref_index.md
@/architecture/callgraph.md
```

Then describe the symptom. These pin to the task context so Cline doesn't have to grep-walk for them.

### 2. Use Plan Mode first, Act Mode second

The Plan/Act toggle is at the bottom of the Cline chat panel. Plan Mode reads only -- it'll consult INTERFACES/DATA_FLOW, propose a root cause, and wait for your OK. Switch to Act Mode after you agree on the fix. This stops Cline from editing source before it's actually understood the contract.

### 3. Automate the ritual with `.clinerules`

Drop a `.clinerules` file (or `.clinerules/debug.md`) at the repo root with standing guidance -- Cline loads it automatically on every task:

```
# Debug-task rules
Before proposing any fix:
- Locate the symbol in architecture/xref_index.md
- Check INTERFACES.md (Silent Failure Modes, Cross-Module Obligations) for the defining file
- For wrong-value bugs, check DATA_FLOW.md Handoff Points
- Walk callgraph.md to find affected callers
Never propose a fix that contradicts INTERFACES.md without flagging it.
```

### 4. Mermaid files -> VS Code preview, not Cline

Install the *Markdown Preview Mermaid Support* extension, then right-click `subsystems.mermaid` / `callgraph.mermaid` -> Open Preview. Feeding raw Mermaid syntax to Cline wastes tokens -- use the `.md` equivalents (`callgraph.md`, `xref_index.md`) for LLM reasoning.

### 5. Stage your context loads

Cline's context-window bar shows live usage. All six files at once can run 10k+ tokens on a large codebase. Start with `xref_index.md` + `INTERFACES.md`; add `DATA_FLOW.md` and per-file `.iface.md` only after you've localized the bug.

### 6. Seed a New Task with a shaped prompt

When you hit **+** for a fresh task, use a loadout like:

```
Symptom: <describe>

@/architecture/xref_index.md   -- locate symbol <X>, list callers
@/architecture/INTERFACES.md   -- check contract on the defining file
@/architecture/DATA_FLOW.md    -- trace the value at the handoff
Propose root cause BEFORE edits. Stay in Plan Mode.
```
