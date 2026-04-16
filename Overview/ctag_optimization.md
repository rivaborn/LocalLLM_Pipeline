# ctags Symbol-Inventory Optimization for Large Codebases

For a very large codebase the bottlenecks are: scan cost (CPU),
inventory size (LLM context window), and redundancy (most symbols
aren't relevant to the current step). Optimizations ordered by impact.

## 1. Cache + incremental scan (biggest CPU win)

Persist a `.symbols_cache.json` at repo root. On each call:

- Walk source files, compare `mtime` to cache.
- Re-run ctags only on changed / new files (`ctags -f - <paths>`
  accepts explicit file lists).
- Merge results into the cache, drop entries for deleted files.

ctags on 10k files drops from ~5s to ~50ms for a typical step (one or
two files changed).

## 2. Step-relevant filtering (biggest context win)

The step description in `aidercommands.md` names the files it'll edit
(e.g. `aider --yes src/nmon/models.py src/nmon/config.py`). Before
injecting the inventory:

- Parse target file paths from the step's `aider` command.
- Keep symbols from:
  - Sibling files in the same package directory.
  - Files that currently import (or are imported by) the target files.
  - Any symbol whose *name* substring-matches text in the step prompt.
- Drop everything else.

Cuts a 10k-symbol inventory to ~200 relevant ones.

## 3. Symbol kind pruning

- Skip `_private` / anonymous-namespace symbols.
- Skip locals / parameters (double-check `scopeKind`).
- C++: skip template instantiations and forward decls; keep the primary
  definition only.
- C#: skip compiler-generated `<>`-mangled names.

## 4. Per-prompt size cap + summarization

- Hard cap the block at e.g. 4000 tokens (configurable).
- If over the cap: compact each entry (drop signatures, keep
  `class Foo`, `fn bar`), then collapse members under class headings.
- Last resort: drop least-relevant files first (by relevance score
  from #2).

## 5. Inverted-index lookup

Once the cache exists, build `{symbol_name: [(file, kind), ...]}` once.
The generator can be given a compact "importable names per file" list
instead of full signatures — most drift is *wrong name*, not *wrong
signature*.

## 6. Parallel ctags

For cold-start on huge repos: shard by top-level directory, run ctags
in parallel (`concurrent.futures`), merge. 2–4× speedup on SSDs.

## 7. Swap to tree-sitter for incremental parsing

Tree-sitter is designed for incremental re-parsing — edit byte ranges
and only re-parse those nodes. For codebases with millions of lines it
beats ctags even with caching. Bigger lift; only worth it if #1 stops
being fast enough.

---

## Recommended implementation order

For the current scale: **#1 + #2** together solve 90% of the problem.
Add #3 and #4 when inventory-size starts hitting context limits. Defer
#5–#7 until actual measurement shows they're needed.

## Files likely to change

- `Common/_pipeline/symbols.py` — add cache + incremental logic (#1),
  relevance filter (#2), size-cap summarizer (#4).
- `LocalLLMCoding/run_aider.py` — pass the current step's file list
  into `build_inventory_block()` so #2 has the context it needs.
