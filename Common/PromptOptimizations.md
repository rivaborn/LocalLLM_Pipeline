# Prompt Optimizations

Living document tracking prompt-engineering changes to the `coding`-mode pipeline templates in `Common/_pipeline/prompts/`. Part 1 records what has already shipped and *why* (the failure it was designed to prevent). Part 2 lists outstanding recommendations to further reduce Stage 2/3 drift.

Use this as the single source of truth when deciding whether a given rule is in place, or when implementing a new recommendation and needing to move its entry into Part 1.

---

## Table of contents

1. [Context](#context)
2. [Part 1 — implemented prompt improvements](#part-1--implemented-prompt-improvements)
   - [Stage 1 template](#stage-1--implementation-planning-prompt-generator)
   - [Stage 2a template](#stage-2a--section-plan-generator)
   - [Stage 2b template](#stage-2b--per-section-architecture-generator)
   - [Stage 3a template](#stage-3a--step-plan-generator)
   - [Stage 2c template](#stage-2c--review--auto-fix-of-architecture-planmd-claude)
   - [Stage 3b templates](#stage-3b--per-step-aider-prompt-generator)
   - [Stage 3c template](#stage-3c--review--auto-fix-claude)
   - [Code-side infrastructure that pairs with these rules](#code-side-infrastructure-that-pairs-with-these-rules)
3. [Part 2 — recommendations (open)](#part-2--recommendations-open)
4. [Maintenance notes](#maintenance-notes)

---

## Context

The `coding` pipeline generates an entire Python project from an `InitialPrompt.md` via five LLM-driven stages. Every stage can introduce **drift** — output that is plausible-looking but diverges from the user's intent, from what a sibling stage produced, or from what the next stage needs. Because each stage's errors compound downstream, prompt-level prevention is always cheaper than runtime detection or post-hoc cleanup.

The improvements below came out of real failures observed while building the `nmonLocalLLM` project end-to-end. Every rule in Part 1 corresponds to at least one concrete failure class we hit and had to recover from. Every recommendation in Part 2 either closes a remaining gap or adds redundancy for a failure class we've already seen.

Related code-side infrastructure (runtime drift detection, empty-retry, context sanitisation, Claude-driven review) is summarized at the end of Part 1 but documented fully in `Documentation/ArchPipeline.md`.

---

## Part 1 — implemented prompt improvements

Changes are listed per-template, oldest to newest within each template, with the specific failure class they address.

### Stage 1 — implementation planning prompt generator

**File:** `_pipeline/prompts/stage1_improve_prompt.md`

**Change 1 — Embedded Hard Constraints block.** The template now instructs Stage 1 to append a 5-rule `## Hard Constraints for Architecture Plan Generation` section, verbatim, to every refined Planning Prompt it produces. Rules: (a) single source of truth per symbol; (b) no Open Questions on prompt-specified items; (c) signature fidelity; (d) import-path canonicalization; (e) each module section names its test file.

*Prevents:* Stage 2 punting prompt-specified requirements to "Open Question" deliverables; duplicate symbol definitions across sibling module sections; signature drift between Planning Prompt and Architecture Plan; import-path synonyms (`Config` → `AppConfig` / `ConfigLoader`) across steps; missing `Test file:` pairing lines that Stage 3a needs to emit test steps.

**Change 2 — Deliverables renaming rule.** Stage 1 is also told to rename any pre-existing "Open Questions" deliverable in the original prompt to "Design Decisions", with the requirement that entries must be decisions with rationale, not deferred questions.

*Prevents:* Carrying forward the original prompt's loopholes into the refined prompt.

### Stage 2a — section plan generator

**File:** `_pipeline/prompts/stage2a_section_plan.md`

**Change 1 — CANONICAL MODULE TITLES block.** Every module-section title MUST follow the exact form `Module: src/<pkg>/<path>.py` (producing the heading `## Module: src/<pkg>/<path>.py` downstream). Non-Python project files use `File: <path>`. Non-module sections keep their canonical names (Project Structure, Data Model, etc.). Stage 3b slices the architecture plan by these `##` headings and matches file basenames against them — heading drift silently breaks context routing.

*Prevents:* Stage 3b `architecture_slice` returning empty / wrong context when headings are free-form (e.g. `## GpuMonitor Module` vs `## Module: src/nmon/gpu/monitor.py`); Stage 3c PROMPT_FILE_MISMATCH false negatives.

**Change 2 — SIZE BUDGET block.** Descriptions capped at ≤ 20 words; each section should produce ≤ 300 lines of Stage 2b output. Oversized modules must be split into multiple sections (e.g. `Module: src/nmon/ui/app.py (lifecycle)` + `Module: src/nmon/ui/app.py (event handlers)`), which Stage 3b re-merges via file-path matching.

*Prevents:* Stage 2b output blowing the downstream context window (Stage 3b saw 500+ line module specs in `nmonLocalLLM` that pushed prompts toward token limits).

### Stage 2b — per-section architecture generator

**File:** `_pipeline/prompts/stage2b_section.md`

**Change 1 — Rule 2 (tightened): NO CROSS-SECTION DUPLICATION.** Each class / function / dataclass / module-level constant is defined in exactly one section; `__init__.py` sections list re-exports only (no class bodies, no method signatures, no dataclass fields).

*Prevents:* `LlmMonitor` defined twice with contradictory signatures — once in `src/nmon/llm/__init__.py`, once in `src/nmon/llm/monitor.py`. Production code wouldn't compile.

**Change 2 — Rule 5 (new): Testing strategy subsection required.** Module sections MUST end with a `### Testing strategy` subsection containing the test file path, external deps to mock, 3-8 concrete behaviour bullets, pytest fixtures, and coverage notes. Non-module sections (Project Structure, Data Model, Configuration, Dependencies, Build/Run) are excluded.

*Prevents:* Stage 3b test steps having no test-specific content to extract from the architecture, leading to production-code-describing prompts written into test files (Steps 16 / 18 / 20 in `nmonLocalLLM`).

**Change 3 — Rule 6 (new): NO OPEN QUESTIONS on prompt-specified items.** If the Planning Prompt names a requirement (endpoint, field, signature, behaviour), the section implements it; never restates it as a question. Genuine implementation-detail ambiguities are recorded as `Design decision: <decision> because <rationale>` lines, not questions.

*Prevents:* Stage 2 deferring the Ollama API endpoint choice to "Open Question #2" and then internally contradicting itself (`/api/generate` vs `/api/tags`).

**Change 4 — Rule 7 (new): Signature fidelity.** Method signatures declared in the Planning Prompt are copied verbatim — same async/sync modifier, parameter names, parameter types, return type, defaults. `async def detect(self) -> bool` stays async everywhere.

*Prevents:* Sync/async drift in `LlmMonitor.detect()`.

**Change 5 — Rule 8 (new): Import-path canonicalization.** Every cross-module reference uses the canonical import path; no synonyms.

*Prevents:* `Config` → `AppConfig` / `ConfigLoader` renaming across sections.

**Change 6 — Rule 1 (tightened): No Python fences for pseudocode.** Pseudocode goes inside ` ```pseudocode ` fences OR as numbered prose steps. NEVER inside ` ```python ` fences — those signal runnable Python to Stage 3b, which then treats the content as authoritative code rather than a pseudocode sketch.

*Prevents:* Stage 3b importing Stage 2b's pseudocode stubs verbatim because they were wrapped in `python` fences.

**Change 7 — Rule 5 (tightened): `Test file:` line as the MANDATORY first line of Testing strategy.** The `### Testing strategy` subsection must begin with the literal pattern `Test file: tests/test_<stem>.py` — Stage 3a parses this to emit the paired test step.

*Prevents:* Missing test-step entries in the `.step_plan.md` because Stage 3a can't locate a test-file anchor (earlier `nmonLocalLLM` audits found 5 test files that should have been paired but weren't).

**Change 8 — Rule 9 (new): Canonical heading format.** Module section headings must match `## Module: src/<pkg>/<path>.py` exactly; non-Python project files use `## File: <path>`; non-module sections keep canonical names.

*Prevents:* Heading-format drift breaking `architecture_slice`, `sanitize_arch_context`, and Stage 3c PROMPT_FILE_MISMATCH detection.

**Change 9 — Rule 10 (new): Imports declaration per module section.** Every module section includes an `Imports:` bullet listing every intra-project symbol this module imports with its canonical import path (e.g. `Imports: GpuSample from nmon.gpu.protocol; RingBuffer from nmon.storage.ring_buffer`). Stdlib / third-party imports are omitted — only intra-project imports need declaring.

*Prevents:* Per-step LLMs inventing import paths that `fix_imports.py` can't rescue (historically a major source of Stage 5 churn).

**Change 10 — Self-check tail.** A numbered five-point self-verification block ("Is my heading canonical? Does any symbol duplicate? Any stub bodies? Test file line present? Imports bullet present?") appears immediately before the planning-prompt injection, forcing the model to re-read its own rules before emitting.

*Prevents:* Individual rule violations slipping through despite each rule being stated earlier in the prompt — capable LLMs honour self-check instructions when structured as a concrete before-emit verification.

### Stage 3a — step plan generator

**File:** `_pipeline/prompts/stage3a_step_plan_tail.md`

**Change 1 — TITLE DISAMBIGUATION block.** Step titles whose file is under `tests/` (or `test_*.py`) MUST end with the literal word " tests" (e.g. `GpuMonitor tests`). Production-step titles MUST NOT end with "tests". Forces a distinguishing signal when a module and its test are adjacent steps.

*Prevents:* Step 15 (`GpuMonitor`, production) and Step 16 (`GpuMonitor`, test) having identical titles, which the Stage 3b LLM then read as "same thing, different file" and produced a production-code prompt into the test file.

**Change 2 — PACKAGE `__init__.py` COVERAGE block.** Every directory under `src/` that contains Python modules MUST have its OWN step for its `__init__.py` file, even if the file is one line or empty. These steps come first in dependency order (before any module that imports from that package). Explicit example given in the template: `src/pkg/__init__.py` → `src/pkg/sub/__init__.py` → `src/pkg/sub/x.py` → `tests/test_x.py`.

*Prevents:* Missing `__init__.py` files breaking imports at runtime. The `nmonLocalLLM` step plan had 5 such missing files that required hand-creation post-Stage 4.

**Change 3 — `tests/conftest.py` SPECIAL-CASE block.** If any test module needs shared pytest fixtures, `tests/conftest.py` gets its OWN step, emitted immediately after the production modules it references and BEFORE any `tests/test_*.py` step. Later test steps assume conftest fixtures exist.

*Prevents:* Early test steps running before their conftest fixtures are defined (order violation aider can't recover from — the test file is written against fixtures the runner can't locate).

### Stage 3b — per-step aider prompt generator

**Files:** `stage3b_step_head.md`, `stage3b_step_tail.md`, **new** `stage3b_step_test_head.md`

**Change 1 (`stage3b_step_tail.md`) — NO STUB METHOD BODIES directive.** Never emit method bodies whose content is only `pass`, `...`, `# Placeholder implementation`, or `# TODO` — local models read those as "already done" and reply with an empty file. If a class skeleton appears, every method body is either a real implementation or a multi-line prose comment describing the required behaviour.

*Prevents:* Step 21 (DashboardTab) producing 40-token preambles followed by silence because its prompt body was a stub-laden skeleton.

**Change 2 (`stage3b_step_tail.md`) — CROSS-FILE CONSISTENCY block.** Use the exact class names, function names, attribute names, parameter names, and import paths that appear in the architecture context — no synonyms; no signature variations; if the plan is ambiguous, take the simplest literal form.

*Prevents:* Independent Stage 3b calls producing inconsistent symbol names / imports across steps that downstream aider then cannot reconcile.

**Change 3 (new `stage3b_step_test_head.md`) — specialized test-step template.** Loaded automatically when all files in a step are under `tests/` or start with `test_`. Explicitly states "THIS STEP GENERATES TESTS, NOT PRODUCTION CODE"; instructs the generated prompt to (a) IMPORT the module under test, never redefine it; (b) use pytest conventions + fixtures; (c) mock all filesystem / network / hardware hits; (d) extract the module's `### Testing strategy` subsection verbatim into the prompt body.

*Prevents:* Test-step prompts describing class implementations instead of tests (Steps 16 / 18 / 20).

**Change 4 (`stage3b_step_head.md`) — DESIGN DECISIONS extraction.** If the architecture context contains a `## Design Decisions` section, any decision whose rationale mentions the current target file (or a class/function it imports) must be included VERBATIM in the implementation prompt body.

*Prevents:* Per-step local LLMs re-deciding the same architectural question inconsistently across steps (e.g. one step picks `/api/ps`, another picks `/api/generate` even though the plan declared a canonical choice).

**Change 5 (`stage3b_step_head.md`) — NON-PYTHON TARGET FILES guard.** If the target file is not `.py` (pyproject.toml, .env.example, README.md, *.yaml, *.ini, *.toml), the NO STUB METHOD BODIES rule does not apply — those files have no methods. The implementation prompt must instead specify the exact file contents (TOML tables, ENV key/value lines, YAML structure).

*Prevents:* Stage 3b awkwardly applying Python-method rules to TOML/YAML/ENV files and producing confusing prompts that downstream aider mis-renders.

**Change 6 (`stage3b_step_test_head.md`) — DESIGN DECISIONS extraction (test variant).** Same rule as Change 4 but framed for test-writing: decisions whose rationale affects test behaviour (e.g. "Use /api/ps because it reports size_vram" → tests mock /api/ps) are included verbatim in the test-prompt body.

*Prevents:* Tests asserting against behaviour inconsistent with the production Design Decision (tests mock a different endpoint than the module polls).

**Change 7 (`stage3b_step_tail.md`) — PRECONDITION CHECK / ERROR circuit-breaker.** Before producing any output, the LLM verifies the architecture context mentions the target file. If not, it responds with the single line `ERROR: target file absent from architecture context` and nothing else. `_detect_stage3b_drift` parses this sentinel and surfaces it as a drift reason, avoiding a fabricated body.

*Prevents:* Stage 3b hallucinating an implementation prompt from unrelated always-included sections when Stage 3a and Stage 2b disagree about a file's existence. Paired code change in `stages_llm.py::_detect_stage3b_drift`.

**Change 8 (`stage3b_step_tail.md`) — TARGET-FILE CONSISTENCY rule.** The file path emitted in `aider --yes AIDERFILES` must be mentioned by its full path at least once in the implementation prompt body. A body that describes files other than `AIDERFILES` is a fatal error.

*Prevents:* Prompt-body / target-file mismatches at generation time (Steps 16 / 18 / 20 pattern) — complements runtime drift detection but catches the issue earlier and cheaper.

### Stage 2c — review + auto-fix of Architecture Plan.md (Claude)

**File:** **new** `_pipeline/prompts/stage2c_review.md`

Opt-in via the same `--review` CLI flag that controls Stage 3c. Runs immediately after Stage 2 generation (or, if Stage 2 was skipped in this invocation, whenever `Architecture Plan.md` already exists and `--review` is set). Claude reads `Implementation Planning Prompt.md` and `Architecture Plan.md` via its Read tool, audits for twelve failure classes, applies safe patches in place, and emits a final `VERDICT: PASS | BLOCK` line that gates Stage 3.

| Class | Name                              | Auto-fixed by Claude?                 | Blocking? |
|-------|-----------------------------------|---------------------------------------|-----------|
| A     | OPEN_QUESTIONS_LEAKAGE            | yes (convert to Design Decision or strip) | yes  |
| B     | MISSING_DESIGN_DECISIONS          | yes (append section with inferred decisions) | yes |
| C     | DUPLICATE_SYMBOL                  | yes (keep canonical impl, reduce other to signature) | yes |
| D     | SIGNATURE_DRIFT                   | yes (rewrite to match Planning Prompt verbatim) | yes |
| E     | IMPORT_PATH_DRIFT                 | yes (correct to canonical source module) | yes |
| F     | PHANTOM_MODULE                    | no (flag for MANUAL_REMAINING)        | yes |
| G     | CONSTANT_MISPLACED                | yes (move to module-level constants bullet) | yes |
| H     | HEADING_FORMAT_DRIFT              | yes (rename to `## Module: src/...\.py`) | yes |
| I     | MISSING_IMPORTS_BULLET            | yes (synthesize from pseudocode)      | no (advisory) |
| J     | MISSING_TESTING_STRATEGY          | yes (append stub subsection)          | yes |
| K     | STUB_METHOD_BODIES                | yes (replace stubs with prose)        | yes |
| L     | PROJECT_STRUCTURE_SCOPE_VIOLATION | yes (strip signatures, retain paths)  | no (advisory) |

Pre-patch snapshot saved to `Architecture Plan.md.bak`; audit + patches log to `Architecture Plan.review.md`; rollback is `mv "Architecture Plan.md.bak" "Architecture Plan.md"`. Every failure class catalogued above was observed on real Stage 2 output during this session — the template uses those specific incidents as anchoring examples.

**Code-side pairing:** `stages_llm.py::stage2c_review` — mirrors `stage3c_review` structurally (backup, invoke_stage, parse PATCHES_APPLIED + VERDICT). Routes to Claude in default and allclaude modes via `router.py::STAGE_DEFAULTS["2c"]` + `get_engine` extension. In `--local` mode it runs on the local LLM (weaker audit, respects user intent).

### Stage 3c — review + auto-fix (Claude)

**File:** **new** `_pipeline/prompts/stage3c_review.md`

Opt-in via `--review`. Claude reads `Architecture Plan.md` and `aidercommands.md` directly (via its Read tool — not inlined), audits for nine failure classes, applies safe fixes in place, and emits a final `VERDICT: PASS | BLOCK` line. Covered classes:

| Class   | Name                 | Auto-fixed by Claude?                  | Blocking?   |
| ------- | -------------------- | -------------------------------------- | ----------- |
| A       | COVERAGE_GAP         | no                                     | yes         |
| B       | TEST_DRIFT           | yes                                    | yes         |
| C       | STUB_SKELETON        | yes                                    | yes         |
| D       | PIPELINE_OUTPUT_STEP | yes                                    | yes         |
| E       | TITLE_AMBIGUITY      | yes                                    | no          |
| F       | PROMPT_FILE_MISMATCH | yes                                    | yes         |
| G       | SYMBOL_DRIFT         | yes (if canonical form is unambiguous) | no          |
| H       | SIGNATURE_DRIFT      | no                                     | yes         |
| I       | ORDER_VIOLATION      | no                                     | no          |

Report written to `aidercommands.review.md`; pre-patch backup to `aidercommands.md.bak`.

### Code-side infrastructure that pairs with these rules

Summarized here for completeness — full details in `Documentation/ArchPipeline.md`.

| Mechanism                                                                                                                                                    | Location                                                          | Pairs with prompt rule                                      |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------- | ----------------------------------------------------------- |
| **Empty-output retry** (up to N=2, deletes empty files between attempts, appends `_EMPTY_RETRY_SUFFIX` to prompt)                                            | `LocalLLMCoding/_aider/runner.py`                                 | `stage3b_step_tail.md` NO STUB rule                         |
| **Runtime drift detection** (mtime snapshot → warn / fail-fast when aider edits files outside `--add` list; hard-fail on test-step touching `src/`)          | `LocalLLMCoding/_aider/runner.py::_detect_aider_drift`            | TITLE DISAMBIGUATION + test-step head template              |
| **Stage 3b drift detector + regenerate-once** (heading / target-file / test-keyword / stub-pattern / `ERROR:` prefix checks → regenerate with explicit warning before writing) | `_pipeline/modes/coding/stages_llm.py::_detect_stage3b_drift`     | All Stage 3b tail rules (including the ERROR circuit-breaker) |
| **Pipeline-output step filter** (skips steps whose target is `Architecture Plan.md`, `aidercommands.md`, etc.)                                               | `_pipeline/modes/coding/fileops.py::is_pipeline_output_only_step` | (no direct prompt pairing — pure code-side filter)          |
| **Architecture context sanitizer** (strips `pass` / `...` / `# Placeholder` from the arch slice before Stage 3b sees it)                                     | `_pipeline/modes/coding/fileops.py::sanitize_arch_context`        | `stage3b_step_tail.md` NO STUB rule                         |
| **Architecture slice (test-file de-prefix matching)** (`test_gpu_monitor.py` pulls the section whose heading contains `monitor.py`)                          | `_pipeline/modes/coding/fileops.py::architecture_slice`           | `stage3b_step_test_head.md` "extract Testing strategy" rule |
| **Adaptive per-call timeout** (`300 + prompt_chars/50 + max_tokens/25`, floored at `LLM_PLANNING_TIMEOUT`)                                                   | `_pipeline/modes/coding/router.py::_adaptive_timeout`             | — (pure infra; unrelated to prompt content)                 |
| **Canned `__init__.py` Stage 2b stub** (bypass the LLM for `Module: src/**/__init__.py` section titles; emit a trivial pre-written stub with canonical heading + Imports bullet + Testing-strategy line in milliseconds) | `_pipeline/modes/coding/stages_llm.py::_canned_init_py_section`   | Stage 2b rule 2 (`__init__.py` sections list re-exports only) |

---

## Part 2 — recommendations (open)

All 13 recommendations from the earlier review of this document have been implemented and migrated into Part 1. No open recommendations at this time.

When a new failure pattern is observed in a future pipeline run, add a new entry to this section as a Priority-1 item with: (a) the failure observed, (b) the affected template(s), (c) a concrete proposed rule. Implement after at least one confirmed reproduction, then migrate the entry into the relevant Part 1 subsection.

---

## Maintenance notes

- **When you implement a Part 2 recommendation**, move its entry into the relevant Stage 1/2a/2b/3a/3b/3c subsection of Part 1 with a short description of the rule as shipped, and the failure class(es) it prevents. Delete the Part 2 entry.

- **When a new failure class is observed**, add it to Part 2 first as a Priority-1 recommendation with a concrete proposed rule. Implement after at least one confirmed reproduction.

- **Do not remove a rule from Part 1 without a replacement.** If a rule becomes redundant because a later rule subsumes it, note the subsumption in the cell ("superseded by rule N") rather than deleting.

- **`Documentation/ArchPipeline.md`** is the user-facing reference. Every change that affects CLI flags, output files, or stage semantics should also update that file. Changes that affect prompt content only belong here.

- **`Documentation/CLAUDE.md`** covers the `claude.py` wrapper; update it when `claude.py` changes. Prompt changes do not require a CLAUDE.md update.
