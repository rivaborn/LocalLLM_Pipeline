# symbols.py -- ctags-Based Symbol Inventory

## Overview

`Common/_pipeline/symbols.py` uses universal-ctags to build a compact symbol
inventory of a repository. The inventory is formatted as a markdown block and
prepended to LLM prompts so the model knows which classes, functions, and
methods already exist -- reducing cross-file import drift when generating
files one at a time.

Works on any language ctags supports: Python, C, C++, C#, Go, Rust, Java,
TypeScript, Ruby, and more.

**Location:** `LocalLLM_Pipeline/Common/_pipeline/symbols.py`

---

## Constants

### `_IMPORTABLE_KINDS`

```python
_IMPORTABLE_KINDS = {
    "class", "struct", "interface", "enum", "function", "method",
    "namespace", "module", "typedef", "type", "trait", "constant",
    "variable",
    # short forms: "c", "s", "i", "g", "f", "m", "n", "t", "v"
}
```

Set of ctags `kind` values that are considered "importable" symbols. Both
full names and short-form single-letter codes are included. Intentionally
permissive to favour false-positives over missed symbols.

### `_EXCLUDE_GLOBS`

```python
_EXCLUDE_GLOBS = [
    ".git", ".venv", "venv", "__pycache__", "node_modules", "build",
    "dist", ".cache", ".pytest_cache", ".mypy_cache", "architecture",
    "LocalLLMCodePrompts", "Implemented Plans", "tests", "test",
]
```

Directories excluded from the ctags scan.

---

## Functions

### `ctags_available`

```python
def ctags_available() -> bool
```

Returns `True` if `ctags` is found on `PATH`. All other functions in this
module short-circuit gracefully (returning empty results) if ctags is not
installed.

---

### `build_inventory`

```python
def build_inventory(
    repo_root: Path,
    max_per_file: int = 40,
) -> dict[str, list[str]]
```

Runs ctags recursively on `repo_root` and returns a dictionary mapping
relative file paths to lists of formatted symbol strings.

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `repo_root` | `Path` | (required) | Root directory to scan |
| `max_per_file` | `int` | `40` | Maximum symbols to include per file |

**Returns:** `dict[str, list[str]]` -- keys are relative paths, values are
lists like `["class MyWidget", "function main(argc, argv)", ...]`.

If more than `max_per_file` symbols exist in a file, a
`"... (N more)"` trailer is appended.

**Filtering:**

- Only symbols with kinds in `_IMPORTABLE_KINDS` are included.
- Symbols whose `scopeKind` is `"function"` (i.e., locals inside functions)
  are excluded.

---

### `build_inventory_block`

```python
def build_inventory_block(
    repo_root: Path,
    max_per_file: int = 40,
) -> str
```

High-level entry point. Runs `build_inventory` and formats the result as a
markdown block ready to prepend to an LLM prompt. Returns an empty string if
ctags is unavailable or no symbols were found.

**Output format:**

```markdown
## Existing Symbol Inventory

The following symbols already exist in the repo. When writing new
code, import from these paths using these exact names; do not
invent new names for symbols that already exist here.

### src/main.py
- class Application
- function main()
- function setup_logging(path)

### src/utils.py
- function hash_file(path)
```

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `repo_root` | `Path` | (required) | Root directory to scan |
| `max_per_file` | `int` | `40` | Maximum symbols per file |

**Returns:** `str` -- markdown block, or `""` if empty.

**Example:**

```python
from _pipeline.symbols import build_inventory_block

block = build_inventory_block(repo_root)
if block:
    full_prompt = block + "\n\n" + step_prompt
```

---

### `_run_ctags` (private)

```python
def _run_ctags(repo_root: Path) -> list[dict]
```

Invokes `ctags --output-format=json --fields=+nKs --languages=all -R`
with all `_EXCLUDE_GLOBS` applied. Parses the JSON-per-line output into
a list of dictionaries. Returns an empty list on any error (missing binary,
timeout after 60 seconds, non-zero exit).

---

### `_group_by_file` (private)

```python
def _group_by_file(entries: list[dict]) -> dict[str, list[dict]]
```

Groups raw ctags entries by file path, filtering to importable kinds only
and excluding function-scoped locals.

---

### `_format_entry` (private)

```python
def _format_entry(e: dict) -> str
```

Formats a single ctags entry as `"kind name"` or `"kind name(signature)"`
if a signature is available.

---

## Dependencies

- `json` (stdlib)
- `shutil` (stdlib)
- `subprocess` (stdlib)
- `pathlib` (stdlib)
- **External:** `ctags` (universal-ctags) must be installed and on PATH

---

## Usage by Other Modules

```python
# In the coding mode orchestrator (run_aider.py)
from _pipeline.symbols import build_inventory_block

block = build_inventory_block(Path(target_dir))
if block:
    prompt = block + "\n\n" + coding_prompt
else:
    prompt = coding_prompt
```
