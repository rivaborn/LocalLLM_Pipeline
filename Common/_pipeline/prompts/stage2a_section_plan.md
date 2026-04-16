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

LAYOUT: The Project Structure section must use the Python src-layout:
package source lives under `src/<package>/`, tests under `tests/`, config
files (pyproject.toml, .env.example) at the repo root.

Here is the planning prompt:

