# config.py -- Shared Configuration

## Overview

`Common/_pipeline/config.py` handles `.env` file loading, subsection parsing,
backend selection, endpoint resolution, and toolkit path discovery. It is the
Python counterpart to `Read-EnvFile`, `Cfg`, `Get-LLMBackend`, and
`Get-LLMEndpoint` from the PowerShell library.

**Location:** `LocalLLM_Pipeline/Common/_pipeline/config.py`

---

## Constants

| Name                      | Value                                    | Description                              |
| ------------------------- | ---------------------------------------- | ---------------------------------------- |
| `BEGIN_MARKER`            | `"#Subsections begin"`                   | Start of the subsections block in `.env` |
| `END_MARKER`              | `"#Subsections end"`                     | End of the subsections block in `.env`   |
| `TOOLKIT_COMMON`          | `Path(__file__).resolve().parent.parent` | Absolute path to `Common/`               |
| `ENV_PATH`                | `TOOLKIT_COMMON / ".env"`                | Default `.env` file path                 |
| `DEFAULT_HOST`            | `"192.168.1.40"`                        | Fallback host (both backends)            |
| `GATEWAY_PORT`            | `"11430"`                                | LLMConfig /v1 gateway port (vllm)        |
| `OLLAMA_PORT`             | `"11434"`                                | Native Ollama port (ollama)              |
| `VALID_BACKENDS`          | `("vllm", "ollama")`                     | Accepted `LLM_BACKEND` values            |

---

## Functions

### `load_env`

```python
def load_env(path: Path = ENV_PATH) -> dict[str, str]
```

Parses a `.env` file into a dictionary. Reads with UTF-8 encoding to handle
special characters (em-dash, etc.) that would break cp1252 on Windows.

**Parsing rules:**

- Blank lines and lines starting with `#` are skipped.
- Lines without `=` are skipped.
- Keys and values are stripped of whitespace.
- Values are stripped of surrounding single or double quotes.

**Parameters:**

| Name   | Type   | Default    | Description             |
| ------ | ------ | ---------- | ----------------------- |
| `path` | `Path` | `ENV_PATH` | Path to the `.env` file |

**Returns:** `dict[str, str]` -- key-value pairs from the file.

**Example:**

```python
from _pipeline.config import load_env

env = load_env()
model = env.get("LLM_MODEL", "qwen2.5-coder:14b")
```

---

### `parse_subsections`

```python
def parse_subsections(path: Path = ENV_PATH) -> list[str]
```

Extracts subsection paths from the `.env` file. Subsections are declared
between `#Subsections begin` and `#Subsections end` markers:

```
#Subsections begin
Code/GeneralsModBuilder
Code/GeneralsModBuilder/GeneralsMD
#Subsections end
```

Non-blank, non-comment lines inside the block are returned in declaration
order.

**Parameters:**

| Name   | Type   | Default    | Description             |
| ------ | ------ | ---------- | ----------------------- |
| `path` | `Path` | `ENV_PATH` | Path to the `.env` file |

**Returns:** `list[str]` -- subsection paths in order.

**Example:**

```python
from _pipeline.config import parse_subsections

for sub in parse_subsections():
    print(f"Processing subsection: {sub}")
```

---

### `sanitize_subsection_name`

```python
def sanitize_subsection_name(subsection: str) -> str
```

Converts a subsection path to a filesystem-safe name by replacing both
forward and back slashes with underscores.

**Example:**

```python
sanitize_subsection_name("Code/GeneralsModBuilder")
# -> "Code_GeneralsModBuilder"
```

---

### `toolkit_root`

```python
def toolkit_root() -> Path
```

Returns the root of the LocalLLM_Pipeline toolkit (the directory containing
`Common/`, `LocalLLMAnalysis/`, `LocalLLMDebug/`, `LocalLLMCoding/`).

**Returns:** `Path` -- one level above `TOOLKIT_COMMON`.

---

### `resolve_backend`

```python
def resolve_backend(env: dict[str, str], explicit: str | None = None) -> str
```

Returns the local LLM backend, `"vllm"` (default) or `"ollama"`. Precedence:
`explicit` arg -> `os.environ["LLM_BACKEND"]` -> `.env` `LLM_BACKEND` -> `"vllm"`.
Raises `ValueError` on any other (non-empty) value.

---

### `resolve_endpoint`

```python
def resolve_endpoint(
    env: dict[str, str] | None = None,
    *,
    backend: str = "vllm",
    explicit: str | None = None,
    read_env_vars: bool = True,
) -> str
```

Resolves the base URL for the chosen backend:

- `vllm`   -> the LLMConfig OpenAI `/v1` gateway (default port `11430`).
- `ollama` -> the native Ollama server (default port `11434`).

**Resolution order:**

1. `explicit` argument (e.g. CLI `--local-endpoint`)
2. `os.environ["OLLAMA_API_BASE"]` (ollama backend only, if `read_env_vars`)
3. `os.environ["LLM_ENDPOINT"]` (if `read_env_vars`)
4. `.env` key `LLM_ENDPOINT`
5. `http://<LLM_HOST or DEFAULT_HOST>:<gateway|ollama port>`

`LLM_PORT` applies to the ollama backend only; the vllm gateway always uses
`11430` unless an explicit `LLM_ENDPOINT` overrides it. All returned values have
trailing slashes stripped.

**Example:**

```python
from _pipeline.config import load_env, resolve_backend, resolve_endpoint

env = load_env()
backend = resolve_backend(env)                       # "vllm"
endpoint = resolve_endpoint(env, backend=backend)    # "http://192.168.1.15:11430"

# Ollama fallback:
resolve_endpoint(env, backend="ollama")              # "http://192.168.1.40:11434"
```

---

## .env Keys Consumed

| Key               | Used by            | Description                                 |
| ----------------- | ------------------ | ------------------------------------------- |
| `LLM_BACKEND`     | `resolve_backend`  | `vllm` (default) or `ollama`                |
| `LLM_ENDPOINT`    | `resolve_endpoint` | Full backend base URL (overrides host/port) |
| `LLM_HOST`        | `resolve_endpoint` | Backend hostname                            |
| `LLM_PORT`        | `resolve_endpoint` | Ollama server port (ollama backend only)    |
| `OLLAMA_API_BASE` | `resolve_endpoint` | os.environ override (ollama backend only)   |

---

## Dependencies

- `os` (stdlib)
- `pathlib.Path` (stdlib)
- No third-party packages

---

## Usage by Other Modules

```python
# In ollama.py
from _pipeline.config import load_env, resolve_backend, resolve_endpoint

env = load_env()
backend = resolve_backend(env)
endpoint = resolve_endpoint(env, backend=backend)

# In the orchestrator (ArchPipeline.py)
from _pipeline.config import load_env, parse_subsections, toolkit_root

env = load_env()
subsections = parse_subsections()
root = toolkit_root()
```
