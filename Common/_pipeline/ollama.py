"""Ollama HTTP client. Python port of Invoke-LocalLLM from llm_common.ps1.

Two modes:
    num_ctx == 0  -> OpenAI-compat /v1/chat/completions (legacy).
    num_ctx  > 0  -> native /api/chat with options.num_ctx + optional think.

Keeps behavioural parity with the PowerShell helper: retry loop, empty-
content detection with thinking-length diagnostic, sanity check that
rejects stray single-token outputs, thinking sidecar writing.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


class LLMError(RuntimeError):
    """Raised when an Ollama call exhausts retries or returns unparseable content."""


@dataclass
class LLMResult:
    content: str
    thinking: str | None = None


def _resolve_endpoint(env: dict[str, str], explicit: str | None) -> str:
    if explicit:
        return explicit.rstrip("/")
    if env.get("LLM_ENDPOINT"):
        return env["LLM_ENDPOINT"].rstrip("/")
    host = env.get("LLM_HOST")
    if host:
        port = env.get("LLM_PORT", "11434")
        return f"http://{host}:{port}".rstrip("/")
    return "http://192.168.1.126:11434"


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
) -> str:
    """Invoke Ollama and return generated content. Writes reasoning to
    `thinking_file` if provided and the response contained thinking tokens."""
    if num_ctx < 0:
        num_ctx = int(env.get("LLM_NUM_CTX", "0"))

    endpoint = _resolve_endpoint(env, endpoint)

    messages: list[dict[str, str]] = []
    if system_prompt and system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})

    if num_ctx > 0:
        uri = f"{endpoint}/api/chat"
        body: dict = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "num_ctx": num_ctx,
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        if think:
            body["think"] = True
    else:
        uri = f"{endpoint}/v1/chat/completions"
        body = {
            "model": model,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

    payload = json.dumps(body).encode("utf-8")

    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(
                uri,
                data=payload,
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp_body = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(resp_body)

            thinking: str | None = None
            if num_ctx > 0:
                message = parsed.get("message", {}) or {}
                content = message.get("content", "") or ""
                thinking = message.get("thinking")
                if thinking_file and thinking and thinking.strip():
                    try:
                        thinking_file.write_text(thinking, encoding="utf-8")
                    except OSError as exc:
                        print(f"  [warn] Could not write thinking sidecar "
                              f"'{thinking_file}': {exc}")
            else:
                choices = parsed.get("choices") or [{}]
                content = choices[0].get("message", {}).get("content", "") or ""

            if not content or not content.strip():
                if num_ctx > 0 and thinking and thinking.strip():
                    raise LLMError(
                        f"Model exhausted budget inside <thinking> "
                        f"(thinking={len(thinking)} chars, num_predict={max_tokens}). "
                        "Raise LLM_PLANNING_MAX_TOKENS."
                    )
                raise LLMError("Empty response from LLM")

            trimmed = content.strip()
            # Same sanity check as the PowerShell version: reject stray
            # stop-token outputs where the thinking consumed the whole budget.
            has_ascii = any(c.isascii() and c.isalnum() for c in trimmed)
            if len(trimmed) < 20 or not has_ascii:
                preview = trimmed[:60]
                extra = (
                    f" -- thinking={len(thinking)} chars suggests budget "
                    "exhaustion during reasoning."
                    if (num_ctx > 0 and thinking and thinking.strip())
                    else ""
                )
                raise LLMError(
                    f"LLM returned suspiciously short/garbled content "
                    f"({len(trimmed)} chars: '{preview}'){extra}"
                )
            return trimmed

        except TimeoutError:
            # socket.timeout (aliased to TimeoutError on 3.10+) is NOT a URLError
            # subclass, so we catch it separately and surface a caller-friendly
            # hint pointing at LLM_PLANNING_TIMEOUT.
            last_err = LLMError(
                f"LLM request timed out after {timeout}s "
                f"(model={model}, num_ctx={num_ctx}). "
                "Raise LLM_PLANNING_TIMEOUT (or LLM_TIMEOUT) in .env if the model "
                "legitimately needs longer, or lower num_ctx / max_tokens."
            )
            if attempt >= max_retries:
                break
            print(f"  [retry {attempt}/{max_retries}] {last_err}")
            time.sleep(retry_delay)
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, LLMError) as exc:
            last_err = exc
            if attempt >= max_retries:
                break
            print(f"  [retry {attempt}/{max_retries}] {exc}")
            time.sleep(retry_delay)

    raise LLMError(f"LLM call failed after {max_retries} attempts: {last_err}")
