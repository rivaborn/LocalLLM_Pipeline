You are performing a pre-execution review of a generated aidercommands.md
file against its source Architecture Plan.md. The aidercommands.md file
drives an automated aider coding pipeline — each step becomes one aider
invocation that spends 3-20 minutes of local-LLM compute and can corrupt
unrelated files if the prompt drifted. Catch problems NOW, before Stage 4
runs.

## Files to read

- TARGET_DIR/Architecture Plan.md          — the canonical spec
- TARGET_DIR/aidercommands.md              — the generated step list
- REPO_ROOT/Implemented Plans/Codebase Summary.md (if present) — additional
                                             invariants the new plan must
                                             not conflict with

## Step format in aidercommands.md

Each step is a markdown block:

    ## Step N - <title>

    aider --yes <file_path>

    ```python
    <prompt body that tells a local LLM what to produce>
    ```

## Failure classes to audit

Report every instance — not just the first per class. Match patterns
mechanically, not by general impression. False positives waste the user's
time more than missed drift, because every flag prompts a manual review.

**A. COVERAGE_GAP** — Every file in the Architecture Plan's Project
Structure section must have exactly one step that creates it. Explicitly
check:
  - Every `src/**/*.py` including every `__init__.py`
    (sub-package __init__.py files are the most commonly missed).
  - Every `tests/test_*.py` the plan's Project Structure or Testing
    Strategy sections call for.
  - Root config files (`pyproject.toml`, `.env.example`, etc.).
Also flag the opposite: steps that create files NOT in Project Structure.

**B. TEST_DRIFT** — For every step whose target is under `tests/` or
named `test_*.py`:
  - Body MUST contain at least one of: `pytest`, `assert `, `def test_`,
    `monkeypatch`, `mock`.
  - Body MUST instruct the LLM to import the module under test — NOT
    redefine or re-implement it.
  - Body MUST NOT contain production-code-describing phrases like
    "Implement the X class in src/...", which cause the local LLM to
    write the production module instead of its tests.

**C. STUB_SKELETON** — A step's prompt body with ≥3 of these patterns
reads as "already done" to local LLMs and produces an empty output file:
  - `# Placeholder implementation` / `# Placeholder`
  - Method bodies that are only `pass`
  - Method bodies that are only `...` (ellipsis)
  - `# TODO` markers
Count matches; flag when ≥3. Note exception-handler `pass` (inside
`except ...:`) does not count.

**D. PIPELINE_OUTPUT_STEP** — Steps whose target file is one of:
`Architecture Plan.md`, `aidercommands.md`, `Implementation Planning
Prompt.md`, `PromptUpdates.md`, `Codebase Summary.md`. These are pipeline
outputs, not project files — Stage 3 must not regenerate them.

**E. TITLE_AMBIGUITY** — When a production step and a test step both
concern the same class/module, titles must be distinguishable. Rule:
test-step titles MUST end with the literal word " tests"
(e.g. "GpuMonitor tests"); production-step titles MUST NOT end with
"tests". Identical or near-identical titles on a (prod, test) pair are
the root cause of test-step prompt drift.

**F. PROMPT_FILE_MISMATCH** — Every step's prompt body must mention its
target file path (or at least the basename) and must not describe
implementing code that lives at a different path. A body describing
`src/nmon/gpu/monitor.py` when the aider command targets
`tests/test_gpu_monitor.py` is a mismatch — the root-cause pattern for
wasted LLM runs and corrupted production files.

**G. SYMBOL_DRIFT** — Class names, function names, method names,
parameter names, and module import paths must be identical across steps
and match the Architecture Plan verbatim. If Step 5 says
`from nmon.config import Config` but Step 8 says
`from nmon.configuration import AppConfig`, flag both along with the
plan's canonical form.

**H. SIGNATURE_DRIFT** — If a step's prompt body asks the LLM to
implement a method with a signature (parameter order, types, defaults,
return type) different from the Architecture Plan, flag it. Test-step
bodies that assert against a signature the plan doesn't actually define
are also a drift.

**I. ORDER_VIOLATION** — Flag when a later step's target file is
imported by an earlier step's body. aider can create placeholder files,
so this is advisory unless the later step would overwrite earlier work.

## Patching

After emitting SUMMARY and FINDINGS, apply safe, mechanical fixes
directly to TARGET_DIR/aidercommands.md using your Edit tool. A fix is
"safe" when the needed change is unambiguous and does not require
architectural judgement.

**Fix these classes automatically:**

- **D. PIPELINE_OUTPUT_STEP** — Delete the entire step block: the `## Step N`
  heading, the `aider --yes ...` line, the fenced prompt body, and the
  trailing `---` separator. Do NOT renumber following steps; gaps in the
  sequence are fine.

- **E. TITLE_AMBIGUITY** — Rename the test-step heading to end with
  " tests" (e.g. `## Step 16 - GpuMonitor` → `## Step 16 - GpuMonitor tests`
  when the aider command targets tests/).

- **B. TEST_DRIFT** (and **F** when the target is under tests/) — Replace
  the prompt body with a concrete TEST-WRITING directive:
    * Name the test file path in a comment (`# Test file: tests/test_*.py`).
    * Instruct: "Write tests for <module>. Do NOT redefine it; import it
      from <canonical import path>."
    * List the mocks, fixtures, and 3-8 concrete behaviors to assert.
      Extract bullets verbatim from the Architecture Plan's
      `### Testing strategy` subsection for the module if present.
    * The rewritten body MUST mention `pytest`, `assert`, and the target
      test file path.
  Do not write the test code itself — write a prompt that tells the
  downstream LLM what tests to write.

- **C. STUB_SKELETON** (and **F** when the target is under src/) — Replace
  the stub-laden body with an implementation DIRECTIVE:
    * Name the target file path in a comment.
    * List required imports.
    * For each method the plan specifies, write a prose-comment block
      describing behavior (inputs, outputs, error handling) — NOT a
      `def ... pass` stub.
    * End the body with: "Write the COMPLETE file. No `pass`, no `...`,
      no `# Placeholder implementation`, no `# TODO`."

- **G. SYMBOL_DRIFT** — When the Architecture Plan names a canonical
  symbol (e.g. `Config`, not `ConfigLoader`), rewrite drifted references
  across EVERY affected step to match the plan. If the plan itself is
  ambiguous about the canonical form, leave the finding for MANUAL_REMAINING.

**Do NOT patch these classes** (leave for MANUAL_REMAINING):

- **A. COVERAGE_GAP** — Adding a new step requires judgement about
  numbering, ordering, prompt structure. List what's missing and a
  concrete suggestion for where it should go.
- **H. SIGNATURE_DRIFT** — Requires close comparison of plan signatures
  to step bodies; risk of making it worse.
- **I. ORDER_VIOLATION** — Reordering can cascade; advisory only.

## Output format

Produce exactly five sections in order. No preamble, no commentary
between sections, no emoji.

### SUMMARY

Per-class counts BEFORE patching, aligned:

    A COVERAGE_GAP:       <n>
    B TEST_DRIFT:         <n>
    C STUB_SKELETON:      <n>
    D PIPELINE_OUTPUT:    <n>
    E TITLE_AMBIGUITY:    <n>
    F PROMPT_MISMATCH:    <n>
    G SYMBOL_DRIFT:       <n>
    H SIGNATURE_DRIFT:    <n>
    I ORDER_VIOLATION:    <n>
    TOTAL:                <sum>

### FINDINGS

One subsection per non-zero class, in order A → I. Under each, one bullet
per instance — precise step number + one-sentence description. Do not
dump file content.

### PATCHES_APPLIED

One bullet per patch applied, with the failure class code(s) and a
one-sentence description of the change. Example:

    - Step 16 [B, F]: replaced stub body with test-writing directive for
      src/nmon/gpu/monitor.py (mock pynvml, assert _poll() sentinel).
    - Step 20 [D]: deleted — targeted Architecture Plan.md (pipeline output).
    - Steps 23, 27 [G]: renamed `ConfigLoader` → `Config` to match plan.

If no patches applied, write one line: `(none)`.

### MANUAL_REMAINING

One bullet per finding you chose NOT to patch (classes A, H, I, or
ambiguous cases), with step number and a concrete fix suggestion.

If nothing remains manual, write: `(none)`.

### VERDICT

A single final line, reflecting post-patch state.

Blocking classes (post-patch): A, B, C, F, H (and D only if a pipeline-
output step somehow remains — patches should have removed them all).
Advisory: E, G, I.

If no blocking findings remain after patches:

    VERDICT: PASS

Else:

    VERDICT: BLOCK — <blocking_count> blocking, <advisory_count> advisory
    Steps needing manual rewrite: <comma-separated list>

## Guidelines

- Read the ENTIRE aidercommands.md. No sampling.
- For each step, extract the target file path from its `aider --yes ...`
  line, then check the body against classes B, C, E, F.
- For cross-step checks (A, G, H, I), build a map of step_num → target
  file → declared symbols before you start reporting.
- Apply each rule mechanically. A rule either matches or it doesn't.
- Be precise — "Step 16" not "one of the test steps".
- When patching with Edit, use enough surrounding context in `old_string`
  that the match is unique to the intended step. A safe pattern is to
  anchor on the `## Step N - ` heading.
- Do the audit BEFORE patching; FINDINGS reflects pre-patch state, so
  the user can see what was wrong even after fixes land.

## Handling a RESUMING FROM PRIOR PARTIAL AUDIT block

If the text injected below the Guidelines section contains a `## RESUMING FROM PRIOR PARTIAL AUDIT` heading, your previous audit was interrupted (rate limit or CLI crash). Rules for continuing:

1. Read the PRIOR PARTIAL OUTPUT block (verbatim between triple-backtick fences). Note which sections were fully written and which were mid-production.
2. Read the current state of `aidercommands.md` — some Edit calls you made in the prior run may have already landed on disk.
3. Do NOT re-emit sections that appear complete in the partial output. Pick up at the next unfinished section and continue.
4. Before applying any Edit, confirm `old_string` still matches the file's current content. If it doesn't match, that Edit has already landed — record it in PATCHES_APPLIED (noting it was from the prior run) and move on.
5. Finish the audit ending with a single `VERDICT: PASS` or `VERDICT: BLOCK ...` line.

The streaming wrapper will APPEND your continuation output to the partial file, so the accumulated review reads as a single coherent audit.
