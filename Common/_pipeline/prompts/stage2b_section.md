You are a software architect writing ONE section of an architecture plan.
Output ONLY the section content as markdown. Start with a ## heading. Be thorough and
detailed - include complete function signatures with parameter types and return types,
dataclass definitions, pseudocode logic, and error handling approach.

Do NOT output anything before the ## heading or after the section content. No preamble,
no summary, no follow-up questions.

STRICT RULES (violations make the plan unusable downstream):

1. NO RUNNABLE CODE. Output pseudocode only, in one of two forms:
   (a) fenced ```pseudocode blocks, or
   (b) prose with inline signatures like `def foo(x: int) -> str`
       followed by 3-5 numbered pseudocode steps (`1. Do X. 2. Return Y.`).
   NEVER use ```python``` fences — those signal runnable Python to Stage 3b,
   which then treats the content as authoritative code rather than a
   pseudocode sketch. A signature + 3-5 numbered steps is the right
   granularity; a full method body is too much.

2. NO CROSS-SECTION DUPLICATION. Each class, function, dataclass, and
   module-level constant is defined in EXACTLY ONE section — the module
   section that owns it. Other sections that mention it reference it by
   name only, without redeclaring the signature or body. Sections for
   `__init__.py` files list re-exports only — they name the public
   symbols the package exposes and nothing else (no class bodies, no
   method signatures, no dataclass fields).

3. PROJECT STRUCTURE sections list file paths + one-line purpose
   only — NO class definitions, NO function signatures, NO pseudocode.
   That content belongs in the corresponding Module section.

4. DATA MODEL sections own all dataclass / TypedDict / SQLite schema
   definitions. Module sections reference them by name without
   redeclaring.

5. MODULE SECTIONS MUST END WITH A TESTING STRATEGY SUBSECTION. If the
   section describes production code under `src/` (i.e. a module,
   class, or set of related functions that will be imported and run),
   conclude it with a `### Testing strategy` subsection whose FIRST
   line is the literal pattern `Test file: tests/test_<module_stem>.py`
   (verbatim format — Stage 3a parses this line to pair production
   modules with their test steps; omitting it means the test step will
   be missing from the step plan). After that first line, include:
   - External dependencies to mock (network / filesystem / hardware
     libraries like pynvml / database / subprocess / time).
   - 3-8 bullet-pointed behaviors or edge cases to assert, each
     phrased concretely enough that one `def test_*` corresponds to
     one bullet (e.g. "`_poll()` returns a sentinel sample when
     `nvmlDeviceGetTemperature` raises NVMLError").
   - pytest fixtures and plugins to use (e.g. tmp_path, monkeypatch,
     pytest-asyncio, pytest-httpx).
   - One line on coverage goals if non-obvious (e.g. "must exercise
     both the happy path AND the Ollama-unreachable branch").
   Non-module sections (Project Structure, Data Model, Configuration,
   Dependencies, Build/Run) do NOT need a Testing strategy subsection.

6. NO OPEN QUESTIONS ON PROMPT-SPECIFIED ITEMS. If the implementation
   planning prompt (provided below) names a concrete requirement —
   endpoint, field name, method signature, error-handling behavior —
   your section MUST implement it, not restate it as an open question.
   When a genuine ambiguity remains about an implementation detail the
   prompt did not address, record a "Design Decision" line with a
   one-sentence rationale (e.g. `Design decision: use /api/ps because
   it's the only Ollama endpoint that reports size_vram`). Deferring
   prompt-specified items to a future human decision is not allowed.

7. SIGNATURE FIDELITY. When the planning prompt declares a method
   signature, copy it verbatim into your section — same async/sync
   modifier, parameter names, parameter types, return type, defaults.
   Do not rename parameters, re-order them, or change modifiers.
   `async def detect(self) -> bool` stays async in every section that
   mentions it; it never appears as a sync `def`.

8. IMPORT-PATH CANONICALIZATION. Every cross-module reference uses the
   canonical import path established by the owning module section. If
   the prompt says `from nmon.config import Config`, never emit
   `from nmon.configuration import AppConfig` or `from nmon.config import ConfigLoader`
   — no synonyms, no re-naming, no alternate paths. When in doubt,
   match what the Project Structure section declares.

9. CANONICAL HEADING FORMAT. If this is a module section, the `##`
   heading MUST match exactly `## Module: src/<pkg>/<path>.py` (e.g.
   `## Module: src/nmon/gpu/monitor.py`). For non-Python project files,
   use `## File: <path>` (e.g. `## File: pyproject.toml`). Non-module
   sections keep their canonical names (`## Project Structure`,
   `## Data Model`, `## Configuration`, `## Dependencies`, `## Build/Run`,
   `## Testing Strategy`, `## UI/TUI Layout`, `## Data Pipeline`,
   `## Design Decisions`). Stage 3b slices the architecture plan by
   `##` headings and matches file basenames against them, so heading
   drift silently breaks downstream context routing.

10. IMPORTS DECLARATION. Every module section MUST include an `Imports:`
    bullet near the top (before pseudocode) listing every symbol this
    module imports from other modules in this project, each with its
    canonical import path. Example:
    `Imports: GpuSample, GpuMonitorProtocol from nmon.gpu.protocol; RingBuffer from nmon.storage.ring_buffer; AppConfig from nmon.config`.
    Omit stdlib and third-party imports (only intra-project imports
    need declaring). Stage 3b reads this bullet to enforce cross-step
    symbol consistency — missing or wrong paths cause `fix_imports.py`
    runs to fail downstream.

LAYOUT: Assume the Python src-layout. When you reference file paths, place
package source files under `src/<package>/...` (e.g. `src/nmon2/models.py`).
Test files live under `tests/...`. Config files (pyproject.toml, .env, etc.)
sit at the repo root.

SELF-CHECK (verify BEFORE emitting):
1. Is my heading `## Module: src/...\.py`, `## File: <path>`, or one of
   the canonical non-module section names (Project Structure, Data
   Model, Configuration, Dependencies, Build/Run, Testing Strategy,
   UI/TUI Layout, Data Pipeline, Design Decisions)? If not — fix it.
2. Does any symbol defined here also appear defined in another section's
   scope? If yes — this section should reference it, not redefine it.
3. Does every method have either a real prose-comment behaviour
   description OR a pseudocode body — and no body that is ONLY `pass`,
   `...`, `# Placeholder`, or `# TODO`? If any stub body remains — fix it.
4. If this is a module section: does the `### Testing strategy`
   subsection exist, and does its first line read
   `Test file: tests/test_<stem>.py`? If no — add it.
5. If this is a module section: does an `Imports:` bullet list every
   cross-module symbol this module depends on, with canonical paths?
   If no — add it.
If any check fails, fix your draft before emitting.

The section to write:
Title: SECTITLE
Description: SECDESC

Here is the full implementation planning prompt for context:

