
===ARCHITECTURE_PLAN_END===

Now produce the step list. Rules:
- Order steps so dependencies are created before dependents
- Include test file steps alongside or immediately after the module they test
- Step 1 should be pyproject.toml + config files
- Final step should be the entry point that wires everything together

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
