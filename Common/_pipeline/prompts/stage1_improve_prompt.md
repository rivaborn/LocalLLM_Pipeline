I have an initial prompt for planning a software project. I need you to do two things:

1. **Review and improve** the prompt below. Produce a refined, detailed implementation
   planning prompt that is clear, unambiguous, and comprehensive. The improved prompt
   should specify tech stack, data model, UI requirements, testing strategy, and
   architecture deliverables. Output ONLY the improved prompt text (no preamble).

   The improved prompt must require the Python src-layout: package source
   under `src/<package>/`, tests under `tests/`, config files
   (pyproject.toml, .env.example) at the repo root.

   **The improved prompt MUST end with the following "Hard Constraints for
   Architecture Plan Generation" section, copied verbatim.** These rules
   close drift loopholes in the downstream Stage 2 architecture generator
   and must ride with every Planning Prompt:

   ```markdown
   ---

   ## Hard Constraints for Architecture Plan Generation

   The following are non-negotiable rules. The Architecture Plan is invalid
   (and Stage 3 must not run) if any is violated:

   1. **Single source of truth per symbol.** Each class, function,
      dataclass, and module-level constant is defined in EXACTLY ONE
      module section. Other sections may reference it by name but must
      not restate its signature, fields, or body. Sections for
      `__init__.py` files list re-exports only — they name the public
      symbols the package exposes and nothing else (no class bodies,
      no method signatures).

   2. **No open questions on prompt-specified items.** If this Planning
      Prompt names a concrete requirement (endpoint, field, signature,
      behavior), the Architecture Plan must implement it. "Open Question"
      is not an acceptable deliverable for anything specified above. When
      a genuine ambiguity remains (an implementation detail this prompt
      did not address), record a Design Decision with rationale — not a
      question.

   3. **Signature fidelity.** Every method signature in the Architecture
      Plan must match this Planning Prompt verbatim on: async/sync
      modifier, parameter names, parameter types, return type. Do not
      rename parameters or re-order them.

   4. **Import-path canonicalization.** Before writing any pseudocode
      that imports a symbol, the plan must declare which module owns
      that symbol. Any cross-module reference uses the canonical import
      path and no synonyms (do not rename `Config` to `AppConfig` or
      `ConfigLoader` across sections).

   5. **Each module section names its test file.** Every
      `## Module: src/<pkg>/**/<name>.py` section must end with an
      explicit `Test file: tests/test_<name>.py` reference so Stage 3's
      step planner can pair production modules with their tests.

   ---
   ```

   If the original prompt also declares a "Deliverables" list with an
   "Open Questions" item, rename that item to "Design Decisions" and
   clarify that entries must be decisions with rationale, never deferred
   questions.

2. After the improved prompt, add a separator line "---PROMPT_UPDATES---" followed by
   a critique of the original prompt: what was unclear, contradictory, or missing, and
   what changes you made and why. Format this as markdown with headers.

Here is the initial prompt to improve:

