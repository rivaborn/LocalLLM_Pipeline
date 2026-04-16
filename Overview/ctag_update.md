# ctags Symbol-Inventory Integration

The coding pipeline's Stage 4 (`run_aider.py`) can prepend a
language-agnostic "symbol inventory" to each aider prompt so the local
LLM stops inventing imports for symbols that don't exist yet. The
inventory is generated with **universal-ctags** and works on any
language ctags supports (Python, C, C++, C#, Go, Rust, Java,
TypeScript, …).

## Install ctags (Windows)

Easiest on Windows — either works:

```bash
# Chocolatey
choco install universal-ctags

# winget
winget install universal-ctags.ctags
```

Verify:

```bash
ctags --version   # should print "Universal Ctags ..."
```

## How it works

Before each Stage 4 step, `run_aider.py`:

1. Calls `ctags -R --output-format=json` on the current repo (cwd).
2. Filters to importable kinds (class, function, method, interface,
   enum, namespace, module-level variable, typedef, trait, …).
3. Groups by file, formats each symbol with its signature where
   available.
4. Prepends a `## Existing Symbol Inventory` markdown block to the step
   prompt, then invokes aider with the enlarged prompt.

Step 1 has no prior symbols and runs with an empty inventory (the block
is omitted). Step 2 sees the exports from step 1's files. Step N sees
everything generated through step N-1.

## Opt out

Add `--no-symbols` to `run_aider.py` to disable injection:

```bash
python C:\Coding\LocalLLM_Pipeline\LocalLLMCoding\run_aider.py --no-symbols
```

If ctags is not installed, the pipeline prints
`[symbols] ctags not installed; skipping inventory injection` and
continues without injection (graceful no-op).

## Files involved

- `Common/_pipeline/symbols.py` — ctags wrapper + inventory formatter.
- `LocalLLMCoding/run_aider.py` — imports `build_inventory_block` and
  prepends the block to each step's prompt.

## Tuning

In `symbols.py`:

- `_IMPORTABLE_KINDS` — set of ctags kind letters to include. Permissive
  by default; trim if the inventory is too long for the context window.
- `_EXCLUDE_GLOBS` — directories skipped during the ctags scan (tests,
  venv, build output, etc.).
- `max_per_file` (default 40) — cap symbols shown per file; the rest
  are summarised as `... (N more)`.

## Upgrade path

ctags is lexical, not semantic. If drift from missing type information
becomes a problem, swap the extractor in `symbols.py` for tree-sitter
(per-language grammars) or a real LSP multiplexer (pyright / clangd /
roslyn / rust-analyzer) — the prompt-injection plumbing in
`run_aider.py` stays the same.
