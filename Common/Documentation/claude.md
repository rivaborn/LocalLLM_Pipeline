# claude.py -- Claude Code CLI Wrapper

## Overview

`Common/_pipeline/claude.py` wraps the `claude` CLI tool, handling account
switching via `CLAUDE_CONFIG_DIR` and model routing. It is used by the coding
mode of the pipeline; the debug mode uses local LLMs exclusively.

**Location:** `LocalLLM_Pipeline/Common/_pipeline/claude.py`

---

## Constants

### `ACCOUNT_CONFIG_DIRS`

```python
ACCOUNT_CONFIG_DIRS: dict[str, str] = {
    "claude1": str(Path.home() / ".clauderivalon"),
    "claude2": str(Path.home() / ".claudefksogbetun"),
}
```

Maps account identifiers to their `CLAUDE_CONFIG_DIR` filesystem locations.
Add new entries to support additional Claude accounts.

---

## Classes

### `ClaudeError`

```python
class ClaudeError(RuntimeError)
```

Raised when the `claude` CLI exits with a non-zero return code, or when an
unknown account identifier is requested.

---

## Functions

### `resolve_account_dir`

```python
def resolve_account_dir(account: str) -> str
```

Looks up the `CLAUDE_CONFIG_DIR` path for a named account. The lookup is
case-insensitive (the key is lowercased before matching).

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `account` | `str` | Account identifier, e.g. `"Claude1"` or `"claude2"` |

**Returns:** `str` -- absolute path to the config directory.

**Raises:** `ClaudeError` -- if the account name is not in `ACCOUNT_CONFIG_DIRS`.

**Example:**

```python
resolve_account_dir("Claude1")
# -> "C:\\Users\\folar\\.clauderivalon"
```

---

### `invoke_claude`

```python
def invoke_claude(
    prompt: str,
    *,
    model: str,
    account: str = "Claude1",
    output_format: str = "text",
) -> str
```

Runs the `claude` CLI with the given prompt piped via stdin and returns the
captured stdout output.

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `prompt` | `str` | (required) | The full prompt text, piped to stdin |
| `model` | `str` | (required) | Claude model tag (e.g. `"sonnet"`, `"opus"`) |
| `account` | `str` | `"Claude1"` | Account to use for config directory |
| `output_format` | `str` | `"text"` | Output format flag passed to CLI |

**Returns:** `str` -- the trimmed stdout from the `claude` process.

**Raises:** `ClaudeError` -- if the `claude` CLI exits non-zero. The error
message includes the exit code and stderr output.

**How it works:**

1. Copies `os.environ` and sets `CLAUDE_CONFIG_DIR` to the resolved account
   directory.
2. Builds the command: `claude --model <model> --output-format <format>`.
3. Runs via `subprocess.run` with `input=prompt`, capturing both stdout and
   stderr.
4. On non-zero exit, raises `ClaudeError` with stderr details.
5. Returns `stdout.strip()`.

**Example:**

```python
from _pipeline.claude import invoke_claude

result = invoke_claude(
    "Review this code and suggest improvements: ...",
    model="sonnet",
    account="Claude2",
    output_format="text",
)
print(result)
```

---

## Dependencies

- `os` (stdlib)
- `subprocess` (stdlib)
- `pathlib` (stdlib)
- **External:** `claude` CLI must be installed and on PATH

---

## Usage by Other Modules

```python
# In the coding mode orchestrator
from _pipeline.claude import invoke_claude, ClaudeError

try:
    code = invoke_claude(
        full_prompt,
        model="sonnet",
        account=env.get("CLAUDE_ACCOUNT", "Claude1"),
    )
except ClaudeError as e:
    logger.error(f"Claude invocation failed: {e}")
    raise
```
