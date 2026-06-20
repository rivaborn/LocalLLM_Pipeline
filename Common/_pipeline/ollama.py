"""Local-LLM HTTP client. Python port of Invoke-LocalLLM from llm_common.ps1.

Backend selects the wire protocol (see config.resolve_backend):
    vllm   -> OpenAI /v1/chat/completions through the LLMConfig gateway
              (auto-loads the model; context is fixed per vLLM alias, so
              num_ctx / think are not sent).
    ollama -> native /api/chat with options.num_ctx + optional think
              (num_ctx == 0 falls back to Ollama's own /v1 endpoint).

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

from . import config as cfg


class LLMError(RuntimeError):
    """Raised when a local-LLM call exhausts retries or returns unparseable content."""


@dataclass
class LLMResult:
    content: str
    thinking: str | None = None


def invoke_local_llm(
    user_prompt: str,
    *,
    env: dict[str, str],
    system_prompt: str | None = None,
    endpoint: str | None = None,
    backend: str | None = None,
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
    """Invoke the local LLM and return generated content. Writes reasoning to
    `thinking_file` if provided and the response contained thinking tokens
    (ollama backend only)."""
    if num_ctx < 0:
        num_ctx = int(env.get("LLM_NUM_CTX", "0"))
    if backend is None:
        backend = cfg.resolve_backend(env)
    endpoint = cfg.resolve_endpoint(env, backend=backend, explicit=endpoint)

    messages: list[dict[str, str]] = []
    if system_prompt and system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})

    # Native /api/chat (num_ctx + think) is an Ollama-only path; the vLLM
    # gateway is OpenAI-compatible and fixes context per alias at serve time.
    native = backend == "ollama" and num_ctx > 0
    if native:
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
            if native:
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
                if native and thinking and thinking.strip():
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
                    if (native and thinking and thinking.strip())
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
                f"(model={model}, backend={backend}). "
                "Raise LLM_PLANNING_TIMEOUT (or LLM_TIMEOUT) in .env if the model "
                "legitimately needs longer, or lower max_tokens."
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
