
===ARCHITECTURE_CONTEXT_END===

PRECONDITION CHECK (before producing any output, verify the context is
usable):
- If the architecture context above does NOT mention the target file path
  (neither as a heading nor inline in another section's text), respond with
  a SINGLE line reading exactly:
      ERROR: target file absent from architecture context
  and NOTHING else. Do not fabricate a prompt body from unrelated sections.
  The pipeline detects this sentinel and fails the step cleanly, signalling
  a Stage 3a / Stage 2b inconsistency to the user.

Now produce the step output. It MUST consist of exactly these four blocks in
order, and nothing else (no preamble, no trailing commentary):

1. A markdown H2 heading line: ## Step STEPNUM -- STEPTITLE
2. A blank line
3. A bash fenced code block containing exactly one line: aider --yes AIDERFILES
4. A plain (unfenced-language) triple-backtick code block whose BODY is a
   detailed implementation prompt you write for the local LLM. The body must
   contain real content -- concrete type signatures, imports, dataclass
   definitions, pseudocode, error-handling guidance -- extracted from the
   architecture context above, sized so a single local LLM call can implement
   the listed files end-to-end. Placeholder strings or angle-bracket stubs are
   NOT acceptable; only real generated prose and code.

   TARGET-FILE CONSISTENCY (mandatory): the file path(s) you emit in
   `aider --yes AIDERFILES` MUST be mentioned by their full path at least
   once in the implementation prompt body. If the target is
   `tests/test_x.py`, open the body with something like
   `Write tests for <module> into tests/test_x.py`. If the target is
   `src/<pkg>/<name>.py`, open the body with
   `Implement <Class> in src/<pkg>/<name>.py`. A prompt body that
   describes files other than `AIDERFILES` is a fatal error — runtime drift
   detection will halt Stage 4 on the mismatch.

   NO STUB METHOD BODIES. Describe method behavior in prose comments or
   pseudocode the downstream LLM must expand into real code. NEVER emit method
   bodies whose content is only `pass`, `...`, `# Placeholder implementation`,
   or `# TODO`. Local models (qwen3-coder, deepseek-coder) read those as
   "already done" and respond with an empty file. If you show a class
   skeleton, every method body must be either (a) a real implementation or
   (b) a multi-line comment describing what the LLM must implement — never
   the `pass` / `...` one-liner.

   NO LITERAL COMPLETE FILE BODIES. The opposite failure is just as bad:
   embedding the entire finished file as a ready-to-use code block inside
   the implementation prompt. Worker models (qwen3-coder, deepseek-coder)
   echo that block verbatim without aider's filename-line + fence wrapper,
   and aider then parses the block's leading character (`/**`, `//`, `#`,
   `<!--`, etc.) as a filename and writes an empty file. Describe the
   required types, function signatures, behavior, and edge cases in prose
   or pseudocode — let the downstream LLM synthesize the full file from
   your description. A few illustrative snippets of critical logic are
   fine; a head-to-toe reproduction of the target file is not.

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
