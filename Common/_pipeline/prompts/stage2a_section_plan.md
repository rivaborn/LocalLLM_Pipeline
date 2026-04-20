You are a software architect planning the sections of a comprehensive
architecture plan document. Given the implementation planning prompt below, list the
sections that the architecture plan should contain.

Output ONLY a numbered list in this exact format, one section per line:

SECTION 1 | Project Structure | full directory tree with every file path
SECTION 2 | Data Model | database schema, Python dataclasses and TypedDicts
SECTION 3 | Module: module_name.py | purpose, classes, function signatures, pseudocode, error handling
...

Rules:
- Include one SECTION entry for each module/file in the "Module breakdown" (one per file)
- Include separate sections for: Project Structure, Data Model, Data Pipeline,
  UI/TUI Layout, Configuration, Testing Strategy, Dependencies, Build/Run Instructions
- The description after the title should summarize what that section covers
- Do NOT output anything else. No headers, no explanations, no markdown formatting.
  Just the section list.

SECTION SCOPE RULES (these flow to Stage 2b so be precise in the descriptions):
- **Project Structure** — file paths + one-line purpose only. NO signatures,
  NO pseudocode, NO class/function declarations.
- **Data Model** — owns ALL dataclass / TypedDict / SQLite schema definitions.
- **Module sections** — own their class/function signatures and pseudocode;
  each symbol is defined in exactly one section.
- Descriptions should explicitly say "list file paths only" for Project Structure,
  "define all dataclasses" for Data Model, etc., so Stage 2b knows the scope.

CANONICAL MODULE TITLES (critical — downstream Stage 3b slices the architecture
plan by `##` heading and only keeps sections whose heading matches a target
file's basename, so the heading shape MUST be predictable):
- Every module section's title MUST be the exact form `Module: src/<pkg>/<path>.py`.
  Example: `SECTION 7 | Module: src/nmon/gpu/monitor.py | GpuMonitor class ...`.
- For non-Python project files (pyproject.toml, .env.example, README.md, etc.)
  use `File: <path>` instead of `Module:`. Example: `SECTION 2 | File: pyproject.toml | ...`.
- Non-module sections keep their canonical names (Project Structure, Data Model,
  Data Pipeline, Configuration, Dependencies, Build/Run, Testing Strategy,
  UI/TUI Layout) — do not rename them.
- When Stage 2b receives your title, it writes the section heading as
  `## <Title>`. So the examples above produce `## Module: src/nmon/gpu/monitor.py`
  — a heading Stage 3b can slice on reliably.

SIZE BUDGET (prevents Stage 2b output from blowing the downstream context
window):
- Descriptions should be ≤ 20 words.
- Each SECTION should produce no more than ~300 lines of Stage 2b output.
  If a module is large enough to need more (e.g. a 10-method class plus
  significant pseudocode), split it into two adjacent sections with distinct
  scopes (e.g. `Module: src/nmon/ui/app.py (lifecycle)` +
  `Module: src/nmon/ui/app.py (event handlers)`). Stage 3b re-merges them via
  file-path matching on the heading.

LAYOUT: The Project Structure section must use the Python src-layout:
package source lives under `src/<package>/`, tests under `tests/`, config
files (pyproject.toml, .env.example) at the repo root.

Here is the planning prompt:

