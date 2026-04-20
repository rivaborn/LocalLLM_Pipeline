# config.py -- Shared Configuration

## Overview

`Common/_pipeline/config.py` handles `.env` file loading, subsection parsing,
endpoint resolution, and toolkit path discovery. It is the Python counterpart
to `Read-EnvFile` and `Cfg` from the PowerShell library.

**Location:** `LocalLLM_Pipeline/Common/_pipeline/config.py`

---

## Constants

| Name                      | Value                                    | Description                              |
| ------------------------- | ---------------------------------------- | ---------------------------------------- |
| `BEGIN_MARKER`            | `"#Subsections begin"`                   | Start of the subsections block in `.env` |
| `END_MARKER`              | `"#Subsections end"`                     | End of the subsections block in `.env`   |
| `TOOLKIT_COMMON`          | `Path(__file__).resolve().parent.parent` | Absolute path to `Common/`               |
| `ENV_PATH`                | `TOOLKIT_COMMON / ".env"`                | Default `.env` file path                 |
| `DEFAULT_OLLAMA_ENDPOINT` | `"http://192.168.1.126:11434"`           | Fallback Ollama URL                      |

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

### `resolve_ollama_endpoint`

```python
def resolve_ollama_endpoint(
    env: dict[str, str] | None = None,
    explicit: str | None = None,
    read_env_vars: bool = True,
) -> str
```

Resolves the Ollama API base URL with a multi-level precedence chain.

**Resolution order:**

1. `explicit` argument (e.g. CLI `--local-endpoint`)
2. `os.environ["OLLAMA_API_BASE"]` (if `read_env_vars` is True)
3. `os.environ["LLM_ENDPOINT"]` (if `read_env_vars` is True)
4. `.env` key `LLM_ENDPOINT`
5. `.env` keys `LLM_HOST` + `LLM_PORT`
6. `DEFAULT_OLLAMA_ENDPOINT` (`http://192.168.1.126:11434`)

All returned values have trailing slashes stripped.

**Parameters:**

| Name            | Type             | Default   | Description                               |
| --------------- | ---------------- | --------- | ----------------------------------------- |
| `env`           | `dict` or `None` | `None`    | Pre-loaded .env dict; loads fresh if None |
| `explicit`      | `str` or `None`  | `None`    | Explicit endpoint from CLI args           |
| `read_env_vars` | `bool`           | `True`    | Whether to check `os.environ`             |

**Returns:** `str` -- the resolved endpoint URL.

**Example:**

```python
from _pipeline.config import load_env, resolve_ollama_endpoint

env = load_env()
endpoint = resolve_ollama_endpoint(env=env)
# -> "http://192.168.1.126:11434"

# CLI override:
endpoint = resolve_ollama_endpoint(explicit="http://localhost:11434")
```

---

## .env Keys Consumed

| Key               | Used by                   | Description                         |
| ----------------- | ------------------------- | ----------------------------------- |
| `LLM_ENDPOINT`    | `resolve_ollama_endpoint` | Full Ollama API URL                 |
| `LLM_HOST`        | `resolve_ollama_endpoint` | Ollama server hostname              |
| `LLM_PORT`        | `resolve_ollama_endpoint` | Ollama server port (default: 11434) |
| `OLLAMA_API_BASE` | `resolve_ollama_endpoint` | Environment variable override       |

---

## Dependencies

- `os` (stdlib)
- `pathlib.Path` (stdlib)
- No third-party packages

---

## Usage by Other Modules

```python
# In ollama.py
from _pipeline.config import load_env, resolve_ollama_endpoint

env = load_env()
endpoint = resolve_ollama_endpoint(env=env)

# In the orchestrator (ArchPipeline.py)
from _pipeline.config import load_env, parse_subsections, toolkit_root

env = load_env()
subsections = parse_subsections()
root = toolkit_root()
```
