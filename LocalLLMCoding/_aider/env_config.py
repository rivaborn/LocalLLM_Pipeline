"""Common/.env discovery + endpoint/model resolution for Ollama-via-aider."""
from __future__ import annotations

import os
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
TOOLKIT_ROOT = _SCRIPT_DIR.parent.parent   # e.g. C:\Coding\LocalLLM_Pipeline\

DEFAULT_LOCAL_ENDPOINT = "http://192.168.1.126:11434"
DEFAULT_LOCAL_MODEL = "qwen3.5:27b"


def _find_common_env() -> Path:
    """Locate Common/.env, tolerating legacy layouts where the toolkit
    was named 'nmonLocalLLM' (sibling or nested)."""
    candidates = [
        TOOLKIT_ROOT         / "Common" / ".env",                          # self-contained
        TOOLKIT_ROOT.parent  / "LocalLLM_Pipeline" / "Common" / ".env",    # sibling
        TOOLKIT_ROOT.parent  / "nmonLocalLLM"      / "Common" / ".env",    # legacy sibling
        TOOLKIT_ROOT         / "nmonLocalLLM"      / "Common" / ".env",    # legacy nested
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]  # canonical expected path, for error messages


COMMON_ENV = _find_common_env()


def read_env_file(path: Path) -> dict[str, str]:
    """Minimal .env parser mirroring the PowerShell Read-EnvFile helper."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip().strip('"').strip("'")
    return out


def resolve_local_config() -> tuple[str, str]:
    """Return (endpoint, model). Precedence for endpoint:
    OLLAMA_API_BASE env > LLM_ENDPOINT env > .env LLM_ENDPOINT
    > .env LLM_HOST+LLM_PORT > hardcoded default.

    Model: .env LLM_AIDER_MODEL > hardcoded default."""
    env = read_env_file(COMMON_ENV)

    if os.environ.get("OLLAMA_API_BASE"):
        endpoint = os.environ["OLLAMA_API_BASE"]
    elif os.environ.get("LLM_ENDPOINT"):
        endpoint = os.environ["LLM_ENDPOINT"]
    elif env.get("LLM_ENDPOINT"):
        endpoint = env["LLM_ENDPOINT"]
    elif env.get("LLM_HOST"):
        endpoint = f"http://{env['LLM_HOST']}:{env.get('LLM_PORT', '11434')}"
    else:
        endpoint = DEFAULT_LOCAL_ENDPOINT
    endpoint = endpoint.rstrip("/")

    model = env.get("LLM_AIDER_MODEL", DEFAULT_LOCAL_MODEL)
    return endpoint, model
