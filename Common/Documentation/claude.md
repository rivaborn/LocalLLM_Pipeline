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

| Name      | Type   | Description                                         |
| --------- | ------ | --------------------------------------------------- |
| `account` | `str`  | Account identifier, e.g. `"Claude1"` or `"claude2"` |

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
    permission_mode: str | None = None,
    stream_to: Path | None = None,
) -> str
```

Runs the `claude` CLI with the given prompt piped via stdin and returns the
captured stdout output.

**Parameters:**

| Name              | Type             | Default     | Description                                                                                                                                                                                                                                                        |
| ----------------- | ---------------- | ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `prompt`          | `str`            | (required)  | The full prompt text, piped to stdin                                                                                                                                                                                                                               |
| `model`           | `str`            | (required)  | Claude model tag (e.g. `"sonnet"`, `"opus"`)                                                                                                                                                                                                                       |
| `account`         | `str`            | `"Claude1"` | Account to use for config directory                                                                                                                                                                                                                                |
| `output_format`   | `str`            | `"text"`    | Output format flag passed to CLI                                                                                                                                                                                                                                   |
| `permission_mode` | `str \| None`    | `None`      | When set, passed to the CLI as `--permission-mode <value>`. Use `"acceptEdits"` for Stage 2c / 3c review stages that need Claude to auto-approve Edit tool calls — the pipeline runs non-interactively, so there's no human available to answer permission prompts. |
| `stream_to`       | `Path \| None`   | `None`      | When set, Claude's stdout is streamed line-by-line to that file (in append mode) AND echoed to the parent's stdout for live console feedback. Partial output is preserved on CLI failure, enabling Stage 2c / 3c resume-on-rate-limit.                              |

**Returns:** `str` -- the trimmed stdout from the `claude` process. When streaming, returns only the text captured during the current invocation (the `stream_to` file is the source of truth for accumulated output across resume runs).

**Raises:** `ClaudeError` -- if the `claude` CLI exits non-zero. The error
message includes the exit code and stderr output. In streaming mode, the
error also names the `stream_to` path so the caller knows where partial
output is preserved.

**How it works:**

Two code paths:

*Non-streaming (when `stream_to is None` — default):*

1. Copies `os.environ` and sets `CLAUDE_CONFIG_DIR` to the resolved account directory.
2. Builds the command: `claude --model <model> --output-format <format>` + optional `--permission-mode <mode>`.
3. Runs via `subprocess.run` with `input=prompt`, capturing both stdout and stderr.
4. On non-zero exit, raises `ClaudeError` with stderr details.
5. Returns `stdout.strip()`.

*Streaming (when `stream_to` is a `Path`):*

1. Copies `os.environ` + sets `CLAUDE_CONFIG_DIR` as above.
2. Builds the command identically.
3. Launches via `subprocess.Popen` with line-buffered pipes.
4. Writes the prompt to stdin, then closes stdin.
5. Opens `stream_to` in **append mode** — so existing partial content from a prior interrupted run is preserved — and reads stdout line-by-line, writing each line to the file AND echoing to the parent's stdout.
6. On process exit, reads final stderr and checks the return code.
7. On non-zero exit, raises `ClaudeError` naming the preserved file; the caller (Stage 2c / 3c) detects the partial state on its next invocation and appends a resume-suffix to the prompt so Claude continues where it stopped.
8. On success, returns the collected text from this invocation (caller typically reads `stream_to` for the full accumulated audit).

**Example:**

```python
from _pipeline.claude import invoke_claude
from pathlib import Path

# Standard text-producing call (Stage 0, Stage 1)
result = invoke_claude(
    "Review this code and suggest improvements: ...",
    model="sonnet",
    account="Claude2",
    output_format="text",
)
print(result)

# Review-stage call with auto-apply permissions + streaming (Stage 2c / 3c)
result = invoke_claude(
    audit_prompt,
    model="sonnet",
    account="Claude1",
    permission_mode="acceptEdits",
    stream_to=Path("target_dir") / "Architecture Plan.review.md",
)
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
