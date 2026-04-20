
===ARCHITECTURE_PLAN_END===

Now produce the step list. Rules:
- Order steps so dependencies are created before dependents
- Include test file steps alongside or immediately after the module they test
- Step 1 should be pyproject.toml + config files
- Final step should be the entry point that wires everything together

PACKAGE `__init__.py` COVERAGE (mandatory — missing ones break imports at
runtime and aider will not auto-create them):
- EVERY directory under `src/` that contains Python modules MUST have its
  OWN step for its `__init__.py` file, even when the file is one line or
  empty. Never fold `__init__.py` creation into another step's file list.
- These steps come FIRST in dependency order: create `src/<pkg>/__init__.py`,
  then `src/<pkg>/<sub>/__init__.py`, then any module that imports from
  those packages.
- Example: if the architecture plan has `src/pkg/sub/x.py` and
  `tests/test_x.py`, your step list includes four steps in this order:
  `src/pkg/__init__.py`, `src/pkg/sub/__init__.py`, `src/pkg/sub/x.py`,
  `tests/test_x.py`.

`tests/conftest.py` SPECIAL-CASE:
- If any `tests/test_*.py` file in the plan needs shared pytest fixtures,
  `tests/conftest.py` gets ITS OWN step, emitted immediately after the
  production modules it references and BEFORE any `tests/test_*.py` step.
- Later test steps assume the fixtures declared in the conftest step
  already exist; ordering matters.

TITLE DISAMBIGUATION (critical — prevents downstream prompt drift):
- If a step's file path is under `tests/` (test-only step), the title MUST
  end with the literal word " tests". Example: "GpuMonitor tests".
- Production-code steps MUST NOT end with "tests". Example: "GpuMonitor".
- A production step and its test step MUST have clearly distinct titles so
  the per-step prompt generator can tell them apart.

LAYOUT: Use the Python src-layout. ALL package source files must live
under `src/<package>/...` (e.g. `src/nmon2/models.py`, not `nmon2/models.py`).
Test files stay under `tests/...` at the repo root. Config files
(pyproject.toml, .env.example, etc.) also stay at the repo root.

Output format: one step per line, pipe-delimited, with EXACTLY this shape:

STEP <n> | <title> | <single file path>

The third column MUST be a single file path. No commas, no "and", no
"+". If you're tempted to list two files, split into two steps.

Do NOT output anything else. No markdown headers (`#`), no code fences, no
explanations, no blank lines between steps. Every output line must begin with
the literal word "STEP " followed by a number.

Begin your response with "STEP 1 |" and continue through every file in the
architecture plan. First line of your response MUST match the regex
^STEP \d+ \| .+ \| .+$
