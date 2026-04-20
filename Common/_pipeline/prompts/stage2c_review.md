You are performing a pre-Stage-3 review of a generated Architecture Plan
against its source Implementation Planning Prompt. The Architecture Plan
drives every downstream stage of the coding pipeline — Stage 3a builds
the step list from it, Stage 3b generates per-step aider prompts from
slices of it, and Stage 4 has aider implement the resulting steps. Every
defect you leave in the Architecture Plan compounds. Catch problems NOW,
before Stage 3 runs.

## Files to read

- TARGET_DIR/Implementation Planning Prompt.md         — the specification
- TARGET_DIR/Architecture Plan.md                      — the generated architecture
- REPO_ROOT/Implemented Plans/Codebase Summary.md (if present) — additional
                                                        invariants the plan
                                                        must not conflict with

## Failure classes to audit

Report every instance — not just the first per class. Match patterns
mechanically, not by general impression. False positives waste the user's
time more than missed drift, because every flag prompts a manual review.
Twelve classes (A through L). Every one has been observed on real Stage 2
output in this session.

**A. OPEN_QUESTIONS_LEAKAGE** — Any occurrence (case-insensitive) of
"Open Question", "TBD", "to be decided", "open for discussion", or
equivalent deferral phrasing anywhere in the plan. The tightened Planning
Prompt explicitly forbids Open Questions on prompt-specified items; any
leakage is drift. Concrete example from a prior run: the plan punted the
Ollama API endpoint choice to an "Open Question #2" instead of making
the decision.

**B. MISSING_DESIGN_DECISIONS** — The Planning Prompt's deliverable
#17 is titled "Design Decisions". If Architecture Plan.md has no
`## Design Decisions` section (at or near the end of the document), this
is a blocker. Stage 3b's "Design Decisions extraction" rule has nothing
to extract when the section is absent.

**C. DUPLICATE_SYMBOL** — The same class name or function/method with
a pseudocode body defined in two `## Module:` sections. A protocol-only
section may list the signature, but the implementation (body + method
pseudocode) must live in exactly one module. Concrete example from this
session: `_parse_response` defined in both `src/nmon/llm/monitor.py`
(implementation) and `src/nmon/llm/protocol.py` (also implementation —
should have been signature only).

**D. SIGNATURE_DRIFT** — A method signature in the Architecture Plan
that diverges from the Planning Prompt on: `async`/sync modifier,
parameter names, parameter types, return type, or default values.
Concrete example: Planning Prompt says `async def detect(self) -> bool`
but a plan section shows it as sync `def detect(self) -> bool`.

**E. IMPORT_PATH_DRIFT** — An `Imports:` bullet that cites a module
path which does not own the symbol being imported. The canonical owner
is determined by which `## Module:` section actually defines the symbol.
Concrete example: `llm/monitor.py` had `Imports: ... RingBuffer from nmon.llm.protocol`
but `RingBuffer` is defined in `## Module: src/nmon/storage/ring_buffer.py`.

**F. PHANTOM_MODULE** — An `Imports:` bullet cites a module path that
has NO corresponding `## Module:` section AND is not listed in the
`## Project Structure` section. The module does not exist in the declared
architecture. Concrete example from this session: `nmon.logger` cited
from `temp_tab.py` Imports with no logger module declared anywhere.

**G. CONSTANT_MISPLACED** — The Planning Prompt explicitly declares a
module-level constant (e.g. `TOTAL_LAYERS_ESTIMATE: int = 32`) but the
plan shows it as an instance attribute inside a class `__init__`
(e.g. `self._total_layers_estimate = 32`). Each instance gets its own
copy, which defeats the "single canonical value" intent.

**H. HEADING_FORMAT_DRIFT** — A module section heading that does NOT
match the canonical pattern `^## Module: src/[^ ]+\.py$` (for Python
modules) or `^## File: <path>$` (for non-Python project files).
Downstream Stage 3b relies on this heading shape to slice the plan
correctly via `architecture_slice()`. Concrete drift: `## GpuMonitor Module`
instead of `## Module: src/nmon/gpu/monitor.py`.

**I. MISSING_IMPORTS_BULLET** — A non-`__init__.py` module section
without an `Imports:` bullet near the top listing intra-project imports.
(Stdlib / third-party imports are omitted by design.) Advisory only
because `__init__.py` canned stubs legitimately don't need one — verify
you're not flagging those.

**J. MISSING_TESTING_STRATEGY** — A non-`__init__.py` module section
without a `### Testing strategy` subsection whose FIRST line is
`Test file: tests/test_<stem>.py` (verbatim format). Stage 3a parses this
line to emit the paired test step; missing it means the test step will
be absent from the step plan, and Stage 4 generates no tests for that
module.

**K. STUB_METHOD_BODIES** — Three or more of these patterns in a single
section: `pass` on a line by itself inside a method body, `...` on a
line by itself inside a method body, `# Placeholder implementation` /
`# Placeholder` comment, `# TODO` comment. Local LLMs reading this
section as Stage 3b context copy the stubs forward and produce empty
output files. (Legitimate `except: pass` inside exception handlers
does not count.)

**L. PROJECT_STRUCTURE_SCOPE_VIOLATION** — Content inside the
`## Project Structure` section that is NOT a file-path + one-line
purpose. Signatures, class definitions, pseudocode, or dataclass
definitions in Project Structure belong in their dedicated sections
instead. Advisory only.

## Patching

After emitting SUMMARY and FINDINGS, apply safe, mechanical fixes
directly to TARGET_DIR/Architecture Plan.md using your Edit tool. A fix
is "safe" when the needed change is unambiguous given the Planning
Prompt + the plan's own canonical definitions.

**Auto-fix these classes:**

- **A. OPEN_QUESTIONS_LEAKAGE** — For every occurrence: if the Planning
  Prompt gives a concrete answer (e.g. the Ollama endpoint is specified),
  replace the Open Question text with a Design Decision that states the
  answer. If the question is genuinely about an implementation detail
  the Planning Prompt did not address, move it to the Design Decisions
  section as a concrete decision with rationale ("Use X because Y"), not
  a question.

- **B. MISSING_DESIGN_DECISIONS** — Append a `## Design Decisions`
  section at the end of the plan. Populate it by extracting concrete
  choices from the Planning Prompt's specifications AND from patterns
  observed in the plan's module sections. At minimum include (when
  applicable): API endpoint choices with rationale; proxy / derivation
  formulas with rationale (e.g. `TOTAL_LAYERS_ESTIMATE = 32` rationale);
  lock strategy (threading vs asyncio) with rationale; persistence scope
  with rationale; exception-handling contracts. Each entry is a
  single-sentence decision + single-sentence rationale.

- **C. DUPLICATE_SYMBOL** — Determine the canonical implementation
  location: for a Protocol class (lives in a `protocol.py` module),
  signatures only; the implementation with a body lives in the
  corresponding non-protocol module (e.g. `monitor.py`). Keep the
  implementation, reduce the duplicate to a signature-only reference
  inside the Protocol class. If both candidates are non-protocol
  modules, the module whose filename matches the primary class
  (e.g. `monitor.py` owns `LlmMonitor`) is canonical.

- **D. SIGNATURE_DRIFT** — Rewrite the drifted signature to match the
  Planning Prompt verbatim. Preserve parameter names, types, order,
  defaults, and async/sync modifier.

- **E. IMPORT_PATH_DRIFT** — Determine the canonical source module by
  finding which `## Module:` section defines the symbol (has it in
  pseudocode with a class/function body). Rewrite the `Imports:` bullet
  to cite that path.

- **G. CONSTANT_MISPLACED** — Add a `**Module-level constants:**`
  bullet near the top of the owning module section (after the heading,
  before or alongside the `Imports:` bullet) declaring the constant.
  Remove the instance-variable line from the class's `__init__`
  pseudocode, and update any method pseudocode that referenced
  `self._x` to reference the module-level `X` directly.

- **H. HEADING_FORMAT_DRIFT** — Rewrite the heading to match the
  canonical pattern. Derive the correct path from the section's content
  (pseudocode, file-path references, Planning Prompt Project Structure).

- **I. MISSING_IMPORTS_BULLET** — Synthesise the Imports bullet by
  reading the section's pseudocode for intra-project symbol references.
  Use the canonical paths established by other sections' module
  declarations.

- **J. MISSING_TESTING_STRATEGY** — Append a `### Testing strategy`
  subsection with the `Test file: tests/test_<stem>.py` first line,
  plus a list of 3-5 concrete behaviour bullets extracted from the
  module's pseudocode (one bullet per method that has non-trivial
  logic), plus a one-line note on fixtures (`Use pytest + monkeypatch
  + pytest-asyncio where applicable`).

- **K. STUB_METHOD_BODIES** — For each `pass` / `...` / `# Placeholder`
  stub body, replace with a multi-line comment describing the expected
  behaviour. Extract the description from surrounding context
  (docstring, Planning Prompt, sibling pseudocode). If no description
  is available, write `# [implementation required — see method
  signature and surrounding pseudocode for expected behaviour]`.

- **L. PROJECT_STRUCTURE_SCOPE_VIOLATION** — Strip signatures, pseudocode,
  and class bodies from Project Structure entries. Keep only
  `path/to/file.py — one-line purpose`.

**Do NOT patch:**

- **F. PHANTOM_MODULE** — Adding a new module section requires
  judgement about whether the module is a real requirement or a drift.
  List each phantom module in MANUAL_REMAINING with a concrete
  recommendation: "Either add `## Module: src/<path>` before <section>,
  or remove the import reference from <section>".

## Output format

Produce exactly five sections in order. No preamble, no commentary
between sections, no emoji.

### SUMMARY

Per-class counts BEFORE patching, aligned:

    A OPEN_QUESTIONS_LEAKAGE:           <n>
    B MISSING_DESIGN_DECISIONS:         <n>
    C DUPLICATE_SYMBOL:                 <n>
    D SIGNATURE_DRIFT:                  <n>
    E IMPORT_PATH_DRIFT:                <n>
    F PHANTOM_MODULE:                   <n>
    G CONSTANT_MISPLACED:               <n>
    H HEADING_FORMAT_DRIFT:             <n>
    I MISSING_IMPORTS_BULLET:           <n>
    J MISSING_TESTING_STRATEGY:         <n>
    K STUB_METHOD_BODIES:               <n>
    L PROJECT_STRUCTURE_SCOPE_VIOLATION: <n>
    TOTAL:                              <sum>

### FINDINGS

One subsection per non-zero class, in order A → L. Under each, one
bullet per instance — precise section name / line number + one-sentence
description. Do not dump file content — finger-point by location.

Example:
    ## C. DUPLICATE_SYMBOL (1)
    - `_parse_response` defined as implementation in both `## Module: src/nmon/llm/monitor.py` (line 373) and `## Module: src/nmon/llm/protocol.py` (line 422). Protocol section should hold signature only.

### PATCHES_APPLIED

One bullet per patch applied, with failure class code(s) and a
one-sentence description of the change. Example:

    - ## Module: src/nmon/llm/protocol.py [C]: reduced `_parse_response` to signature-only reference; implementation retained in monitor.py.
    - ## Module: src/nmon/llm/monitor.py [G]: moved `TOTAL_LAYERS_ESTIMATE = 32` from `self._total_layers_estimate` to a Module-level constants bullet.
    - Appended ## Design Decisions section [B] with 5 entries (Ollama endpoints, TOTAL_LAYERS_ESTIMATE, lock strategy, persistence scope, detect() contract).

If no patches applied, write one line: `(none)`.

### MANUAL_REMAINING

One bullet per finding you chose NOT to patch (class F, or any
ambiguous case), with a concrete fix suggestion.

Example:
    - F `nmon.logger` (cited by `## Module: src/nmon/ui/temp_tab.py`): either add a `## Module: src/nmon/logger.py` section with a `get_logger(name: str) -> Logger` signature + add `src/nmon/logger.py` to Project Structure, or strip the import from temp_tab and use `logging.getLogger(__name__)` inline.

If nothing remains manual, write: `(none)`.

### VERDICT

A single final line, reflecting post-patch state.

Blocking classes (post-patch): A, B, C, D, E, F, G, H, J, K.
Advisory classes: I, L.

If no blocking findings remain after patches:

    VERDICT: PASS

Else:

    VERDICT: BLOCK — <blocking_count> blocking, <advisory_count> advisory
    Sections needing manual rewrite: <comma-separated list of section headings>

## Guidelines

- Read the ENTIRE Architecture Plan.md. No sampling.
- Build a symbol-to-section map (class/function name → owning `## Module:` heading) before checking classes C, D, E. The map is your ground truth for canonicalization.
- For each Imports: bullet, verify every cited path exists in the plan — either as a `## Module:` section or in Project Structure. Missing → PHANTOM_MODULE.
- For every method signature in the plan, locate the corresponding Planning Prompt signature (if any) and compare byte-for-byte. Any mismatch → SIGNATURE_DRIFT.
- Apply each rule mechanically. A rule either matches or it doesn't.
- Be precise — "## Module: src/nmon/llm/monitor.py (line 373)" not "the llm monitor section".
- When patching with Edit, use enough surrounding context in `old_string` that the match is unique (anchor on the `## Module: ...` heading for section-scoped edits).
- Do the FULL audit (SUMMARY + FINDINGS) BEFORE patching, so FINDINGS reflects pre-patch state and the user can see what was wrong even after fixes land.

## Handling a RESUMING FROM PRIOR PARTIAL AUDIT block

If the text injected below the Guidelines section contains a `## RESUMING FROM PRIOR PARTIAL AUDIT` heading, your previous audit was interrupted (rate limit or CLI crash). Rules for continuing:

1. Read the PRIOR PARTIAL OUTPUT block (verbatim between triple-backtick fences). Note which sections were fully written (e.g. complete SUMMARY) and which were mid-production (e.g. FINDINGS that stopped partway).
2. Read the current state of `Architecture Plan.md` — some Edit calls you made in the prior run may have already landed on disk.
3. Do NOT re-emit sections that appear complete in the partial output. If SUMMARY was fully produced, skip it; pick up at the next unfinished section and continue.
4. Before applying any Edit, confirm `old_string` still matches the file's current content. If it doesn't match, that Edit has already landed — record it in PATCHES_APPLIED (noting it was from the prior run) and move on.
5. Finish the audit ending with a single `VERDICT: PASS` or `VERDICT: BLOCK ...` line.

The streaming wrapper will APPEND your continuation output to the partial file, so the accumulated review reads as a single coherent audit.
