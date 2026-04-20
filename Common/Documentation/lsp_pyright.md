# lsp_pyright.py -- Pyright LSP Client

## Overview

`Common/_pipeline/lsp_pyright.py` provides a minimal LSP client for
pyright-langserver, used to resolve symbol locations in installed packages
(PyQt6, pynvml, pyqtgraph, etc.) that ctags cannot index because they are
compiled extensions or shipped as `.pyi` type stubs.

Pyright consults the active venv's site-packages and typeshed, giving
semantically accurate answers to "which module does symbol X live in?"

**Location:** `LocalLLM_Pipeline/Common/_pipeline/lsp_pyright.py`

---

## Constants

### `_SYMBOL_KIND`

Dictionary mapping LSP `SymbolKind` integer codes to human-readable strings:

```python
{1: "file", 2: "module", 3: "namespace", 4: "package", 5: "class",
 6: "method", 7: "property", 8: "field", 9: "constructor", 10: "enum",
 11: "interface", 12: "function", 13: "variable", 14: "constant", ...}
```

---

## Functions

### `pyright_available`

```python
def pyright_available() -> bool
```

Returns `True` if `pyright-langserver` is on PATH.

---

### `build_external_symbol_block`

```python
def build_external_symbol_block(
    names: list[str],
    workspace_root: Path,
) -> str
```

Convenience wrapper for one-shot use. Starts pyright, resolves all names,
formats the result as a prompt block, and shuts down. Returns an empty
string if pyright is not available or no symbols were found.

For multi-step runs, reuse a `PyrightClient` instance instead to avoid
the ~3-second startup cost on each call.

**Parameters:**

| Name             | Type        | Description                              |
| ---------------- | ----------- | ---------------------------------------- |
| `names`          | `list[str]` | Symbol names to resolve                  |
| `workspace_root` | `Path`      | Project root (used as the LSP workspace) |

**Returns:** `str` -- formatted markdown block, or `""`.

**Example:**

```python
from _pipeline.lsp_pyright import build_external_symbol_block

block = build_external_symbol_block(
    ["QShowEvent", "QCloseEvent", "nvmlInit"],
    Path("/home/user/myproject"),
)
if block:
    prompt = block + "\n\n" + step_prompt
```

---

### `format_resolved`

```python
def format_resolved(hits: dict[str, list[tuple[str, str]]]) -> str
```

Formats resolved symbol hits into a markdown block for prompt injection.

**Output format:**

```markdown
## Verified Symbol Locations (from pyright)

These symbols exist in installed packages / the current workspace.
Use the exact container shown when writing imports:

- `QShowEvent` (class) -> QtWidgets
- `nvmlInit` (function) -> pynvml
```

**Returns:** `str` -- markdown block, or `""` if `hits` is empty.

---

## Classes

### `LSPError`

```python
class LSPError(RuntimeError)
```

Raised on LSP protocol errors, timeouts, or when pyright is not started.

---

### `PyrightClient`

```python
class PyrightClient:
    def __init__(self, workspace_root: Path, timeout: float = 30.0) -> None
```

Thin LSP client tailored to pyright-langserver's symbol lookup. Not a
general-purpose LSP client -- only the methods needed for `resolve_symbols`
are implemented.

**Constructor parameters:**

| Name             | Type    | Default    | Description                            |
| ---------------- | ------- | ---------- | -------------------------------------- |
| `workspace_root` | `Path`  | (required) | Project root directory                 |
| `timeout`        | `float` | `30.0`     | Maximum seconds to wait for a response |

#### Lifecycle Methods

##### `start()`

```python
def start(self) -> None
```

Spawns `pyright-langserver --stdio`, starts a background reader thread, and
sends the LSP `initialize` / `initialized` handshake.

**Raises:** `LSPError` if `pyright-langserver` is not found on PATH.

##### `shutdown()`

```python
def shutdown(self) -> None
```

Sends `shutdown` and `exit` LSP messages, then waits up to 3 seconds for the
process to exit. Kills the process if it does not exit in time. Safe to call
multiple times.

#### Query Methods

##### `workspace_symbol`

```python
def workspace_symbol(self, query: str) -> list[dict]
```

Sends an LSP `workspace/symbol` request with a substring query. Returns raw
`SymbolInformation[]` entries. Pyright includes symbols from installed
third-party packages since they are on the import path.

**Parameters:**

| Name    | Type   | Description             |
| ------- | ------ | ----------------------- |
| `query` | `str`  | Substring to search for |

**Returns:** `list[dict]` -- raw LSP symbol information entries.

##### `resolve_symbols`

```python
def resolve_symbols(
    self,
    names: list[str],
) -> dict[str, list[tuple[str, str]]]
```

For each name, returns a list of `(container_or_file, kind)` tuples.
Filters to exact-name matches only (LSP `workspace/symbol` does substring
matching, which would flood results with near-misses).

Names shorter than 2 characters are skipped. If a symbol has no
`containerName`, the module name is derived from the file URI.

**Parameters:**

| Name    | Type        | Description             |
| ------- | ----------- | ----------------------- |
| `names` | `list[str]` | Symbol names to look up |

**Returns:** `dict[str, list[tuple[str, str]]]` -- e.g.
`{"QShowEvent": [("QtWidgets", "class")]}`.

#### Internal / Protocol Methods

##### `_send` (private)

Serializes a JSON-RPC 2.0 message with `Content-Length` framing and writes
it to pyright's stdin. Assigns an auto-incrementing message ID for requests
(not for notifications).

##### `_reader_loop` (private)

Background thread that reads `Content-Length`-framed JSON-RPC messages from
pyright's stdout. Stores responses (messages with `id` and `result`/`error`)
in `_responses`; ignores server notifications (diagnostics, progress).

##### `_await_response` (private)

Blocks until the response for a given message ID arrives or the timeout
expires.

##### `_request` (private)

Combines `_send` and `_await_response` into a single synchronous
request/response call. Raises `LSPError` if the response contains an error.

---

## Lifecycle Example

```python
from pathlib import Path
from _pipeline.lsp_pyright import PyrightClient

client = PyrightClient(Path("/home/user/myproject"))
client.start()
try:
    hits = client.resolve_symbols(["QShowEvent", "QCloseEvent"])
    for name, locations in hits.items():
        for container, kind in locations:
            print(f"{name} ({kind}) -> {container}")
finally:
    client.shutdown()
```

---

## Dependencies

- `json`, `queue`, `shutil`, `subprocess`, `threading`, `time` (stdlib)
- `pathlib` (stdlib)
- **External:** `pyright-langserver` must be installed (`pip install pyright`)
