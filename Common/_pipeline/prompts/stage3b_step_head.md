Your task: write one implementation step for aider (an AI coding tool), covering
the files listed below. The output is a markdown block containing (a) an aider
shell command and (b) a SELF-CONTAINED implementation prompt for a local LLM
that has no access to the architecture plan.

Step number: STEPNUM
Step title:  STEPTITLE
Files:       AIDERFILES

Relevant architecture plan context is between the delimiters below. Extract from
it every detail the local LLM needs -- types, function signatures with parameter
and return types, dataclass definitions, imports, pseudocode, error handling --
and put those details into the implementation prompt you write.

DESIGN DECISIONS: if the architecture context contains a `## Design Decisions`
section, include any decision whose rationale mentions the current target file
(or a class/function it imports) VERBATIM in the implementation prompt body.
This prevents the downstream local LLM from re-deciding the same question
inconsistently across independent steps.

NON-PYTHON TARGET FILES: if the target file listed in `AIDERFILES` is NOT a
`.py` file (e.g. `pyproject.toml`, `.env.example`, `README.md`, `*.yaml`,
`*.ini`, `*.toml`), the "NO STUB METHOD BODIES" rule in the tail does not
apply — those files have no methods. Instead, the implementation prompt you
write must specify the exact file contents: every TOML table / key / value,
every ENV `KEY=VALUE` line, every YAML structure the architecture requires.
Comments in config files are encouraged for documentation.

===ARCHITECTURE_CONTEXT_START===
