# ollama.py -- Ollama HTTP Client

## Overview

`Common/_pipeline/ollama.py` is the Python port of `Invoke-LocalLLM` from
the PowerShell library. It provides an HTTP client for the Ollama chat API
with retry logic, thinking-model support, and response validation.

**Location:** `LocalLLM_Pipeline/Common/_pipeline/ollama.py`

---

## Classes

### `LLMError`

```python
class LLMError(RuntimeError)
```

Raised when an Ollama call exhausts all retries or returns unparseable/empty
content. Inherits from `RuntimeError`.

---

### `LLMResult`

```python
@dataclass
class LLMResult:
    content: str
    thinking: str | None = None
```

Data class for structured LLM responses. Currently used internally; the
public `invoke_local_llm` function returns only the content string.

---

## Functions

### `invoke_local_llm`

```python
def invoke_local_llm(
    user_prompt: str,
    *,
    env: dict[str, str],
    system_prompt: str | None = None,
    endpoint: str | None = None,
    model: str = "qwen2.5-coder:14b",
    temperature: float = 0.1,
    max_tokens: int = 800,
    num_ctx: int = -1,
    timeout: int = 120,
    max_retries: int = 3,
    retry_delay: int = 5,
    think: bool = False,
    thinking_file: Path | None = None,
) -> str
```

Sends a chat completion request to Ollama and returns the generated text.

**Two API modes:**

| Condition | Endpoint | Request shape |
|-----------|----------|---------------|
| `num_ctx > 0` | `/api/chat` (native Ollama) | `options.num_ctx`, `options.temperature`, `options.num_predict` |
| `num_ctx == 0` | `/v1/chat/completions` (OpenAI compat) | `temperature`, `max_tokens` |

When `num_ctx` is -1 (default), the value is read from `env["LLM_NUM_CTX"]`,
defaulting to `0` if absent.

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `user_prompt` | `str` | (required) | The user message content |
| `env` | `dict[str, str]` | (required) | Loaded .env configuration |
| `system_prompt` | `str` or `None` | `None` | Optional system message |
| `endpoint` | `str` or `None` | `None` | Explicit endpoint URL; auto-resolved if None |
| `model` | `str` | `"qwen2.5-coder:14b"` | Ollama model name |
| `temperature` | `float` | `0.1` | Sampling temperature |
| `max_tokens` | `int` | `800` | Maximum output tokens (`num_predict` in native mode) |
| `num_ctx` | `int` | `-1` | Context window size; -1 reads from env |
| `timeout` | `int` | `120` | HTTP request timeout in seconds |
| `max_retries` | `int` | `3` | Maximum retry attempts |
| `retry_delay` | `int` | `5` | Seconds between retries |
| `think` | `bool` | `False` | Enable thinking/reasoning mode |
| `thinking_file` | `Path` or `None` | `None` | File to write reasoning trace |

**Returns:** `str` -- the trimmed response content.

**Raises:** `LLMError` -- after exhausting retries, or on garbled/empty output.

**Thinking model support:**

When `think=True` and using native mode (`num_ctx > 0`), the request includes
`"think": true`. If the response contains a `thinking` field and
`thinking_file` is provided, the reasoning trace is written to disk as a
sidecar file.

**Retry logic:**

Catches `URLError`, `HTTPError`, `JSONDecodeError`, and `LLMError`. Retries
up to `max_retries` times with `retry_delay` seconds between each attempt.
Prints a `[retry N/M]` message to stdout on each retry.

**Sanity checks (same as PowerShell version):**

- Empty/whitespace-only content is rejected.
- Content shorter than 20 characters is rejected.
- Content lacking any ASCII alphanumeric characters is rejected.
- If thinking content exists but main content is empty, suggests raising
  `LLM_PLANNING_MAX_TOKENS`.

**Example:**

```python
from _pipeline.config import load_env
from _pipeline.ollama import invoke_local_llm

env = load_env()
result = invoke_local_llm(
    "Explain this function: ...",
    env=env,
    system_prompt="You are a code documentation writer.",
    model="qwen2.5-coder:14b",
    max_tokens=1200,
)
print(result)
```

---

### `_resolve_endpoint` (private)

```python
def _resolve_endpoint(env: dict[str, str], explicit: str | None) -> str
```

Internal endpoint resolution. Checks `explicit`, then `env["LLM_ENDPOINT"]`,
then `env["LLM_HOST"]` + `env["LLM_PORT"]`, then falls back to the default.
This is a simplified version of `config.resolve_ollama_endpoint` that does
not check `os.environ`.

---

## .env Keys Consumed

| Key | Default | Description |
|-----|---------|-------------|
| `LLM_ENDPOINT` | (none) | Full Ollama API URL |
| `LLM_HOST` | `192.168.1.126` | Ollama server hostname |
| `LLM_PORT` | `11434` | Ollama server port |
| `LLM_NUM_CTX` | `0` | Context window; 0 = OpenAI compat mode |

---

## Dependencies

- `json` (stdlib)
- `time` (stdlib)
- `urllib.request`, `urllib.error` (stdlib)
- `dataclasses` (stdlib)
- `pathlib` (stdlib)
- No third-party packages (uses only stdlib `urllib`, not `requests`)

---

## Usage by Other Modules

```python
# In the orchestrator or mode scripts
from _pipeline.config import load_env
from _pipeline.ollama import invoke_local_llm, LLMError

env = load_env()
try:
    doc = invoke_local_llm(
        prompt,
        env=env,
        model=env.get("LLM_MODEL", "qwen2.5-coder:14b"),
        max_tokens=int(env.get("LLM_MAX_TOKENS", "800")),
        think=True,
        thinking_file=Path("output/step3_thinking.txt"),
    )
except LLMError as e:
    logger.error(f"LLM failed: {e}")
```
