
===ARCHITECTURE_CONTEXT_END===

Now produce the step output. It MUST consist of exactly these four blocks in
order, and nothing else (no preamble, no trailing commentary):

1. A markdown H2 heading line: ## Step STEPNUM - STEPTITLE
2. A blank line
3. A bash fenced code block containing exactly one line: aider --yes AIDERFILES
4. A plain (unfenced-language) triple-backtick code block whose BODY is a
   detailed implementation prompt you write for the local LLM. The body must
   contain real content -- concrete type signatures, imports, dataclass
   definitions, pseudocode, error-handling guidance -- extracted from the
   architecture context above, sized so a single local LLM call can implement
   the listed files end-to-end. Placeholder strings or angle-bracket stubs are
   NOT acceptable; only real generated prose and code.

CROSS-FILE CONSISTENCY (critical -- the aider steps are generated
independently and one step cannot see what another produced, so symbol names
must match the architecture context verbatim):
- Use the EXACT class names, function names, method names, attribute names,
  parameter names, and module paths that appear in the architecture context
  above. Do not invent synonyms (e.g. if the plan defines "Config", do not
  emit "ConfigLoader" or "AppConfig").
- Preserve the exact signatures shown in the plan -- same parameter order,
  same parameter types, same return type, same default values.
- Import paths must match the project structure in the plan (e.g. if the
  plan shows "from nmon.config import Config", use that exact path, not
  "from nmon.configuration import Config").
- If the plan is ambiguous about a name, choose the simplest literal form
  from the plan text and stick to it; do not elaborate.

Begin your response with the "## Step" heading.
