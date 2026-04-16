You are decomposing an architecture plan into discrete implementation steps
for aider (an AI coding tool).

STRICT RULE: each step creates or modifies EXACTLY ONE file. Multi-file
steps are not allowed — aider frequently writes only the first file in
a multi-file prompt and silently skips the rest, so we enforce a
one-file-per-step contract. If the architecture plan describes two
closely-coupled files, emit two adjacent steps (e.g. Step 1 creates
pyproject.toml, Step 2 creates .env.example).

Architecture plan below:

===ARCHITECTURE_PLAN_START===

