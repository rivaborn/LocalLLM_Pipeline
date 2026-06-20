"""Shared configuration: .env loading, subsection parsing, endpoint
resolution.

The .env format mirrors LocalLLM_Pipeline/Common/.env. Subsections live
between '#Subsections begin' / '#Subsections end' markers; non-comment
non-blank lines inside the block are treated as subsection paths.
"""
from __future__ import annotations

import os
from pathlib import Path

BEGIN_MARKER = "#Subsections begin"
END_MARKER = "#Subsections end"

# The toolkit and this orchestrator live at a fixed path; .env sits next
# to this file (Common/.env) so we resolve it relative to the package.
TOOLKIT_COMMON = Path(__file__).resolve().parent.parent
ENV_PATH = TOOLKIT_COMMON / ".env"


def load_env(path: Path = ENV_PATH) -> dict[str, str]:
    """Parse a .env file into a dict. UTF-8 safe (the shared .env uses
    characters like em-dash in descriptions that cp1252 chokes on)."""
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


def parse_subsections(path: Path = ENV_PATH) -> list[str]:
    """Return subsection paths in declaration order."""
    if not path.exists():
        return []
    subsections: list[str] = []
    in_block = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line == BEGIN_MARKER:
            in_block = True
            continue
        if line == END_MARKER:
            break
        if in_block and line and not line.startswith("#"):
            subsections.append(line)
    return subsections


def sanitize_subsection_name(subsection: str) -> str:
    """Return a filesystem-safe rendering of a subsection path."""
    return subsection.strip().replace("\\", "_").replace("/", "_")


def toolkit_root() -> Path:
    """Return the root of the LocalLLM_Pipeline toolkit (the directory
    that contains Common/, LocalLLMAnalysis/, LocalLLMDebug/, LocalLLMCoding/)."""
    return TOOLKIT_COMMON.parent


DEFAULT_HOST = "192.168.1.126"
GATEWAY_PORT = "11430"   # LLMConfig OpenAI /v1 gateway (vllm backend)
OLLAMA_PORT = "11434"    # native Ollama server (ollama backend)
VALID_BACKENDS = ("vllm", "ollama")


def resolve_backend(env: dict[str, str], explicit: str | None = None) -> str:
    """Return the local LLM backend: 'vllm' (default) or 'ollama'.

    Precedence: explicit arg -> os.environ LLM_BACKEND -> .env LLM_BACKEND -> 'vllm'.
    """
    raw = explicit or os.environ.get("LLM_BACKEND") or env.get("LLM_BACKEND") or "vllm"
    backend = raw.strip().lower()
    if backend not in VALID_BACKENDS:
        raise ValueError(f"LLM_BACKEND must be one of {VALID_BACKENDS}, got {raw!r}")
    return backend


def resolve_endpoint(
    env: dict[str, str] | None = None,
    *,
    backend: str = "vllm",
    explicit: str | None = None,
    read_env_vars: bool = True,
) -> str:
    """Return the base URL for the local LLM backend.

    vllm   -> the LLMConfig OpenAI /v1 gateway (default port 11430).
    ollama -> the native Ollama server (default port 11434).

    Precedence:
        1. *explicit* arg (CLI --local-endpoint or similar)
        2. os.environ OLLAMA_API_BASE  (ollama backend only)
        3. os.environ LLM_ENDPOINT
        4. .env LLM_ENDPOINT
        5. http://<LLM_HOST or default>:<gateway|ollama port>

    LLM_PORT applies to the ollama backend only; the vllm gateway always uses
    11430 unless an explicit LLM_ENDPOINT overrides it (the gateway and the raw
    Ollama server share a host but listen on different ports).
    """
    if explicit:
        return explicit.rstrip("/")
    if env is None:
        env = load_env()
    if read_env_vars:
        if backend == "ollama" and os.environ.get("OLLAMA_API_BASE"):
            return os.environ["OLLAMA_API_BASE"].rstrip("/")
        if os.environ.get("LLM_ENDPOINT"):
            return os.environ["LLM_ENDPOINT"].rstrip("/")
    if env.get("LLM_ENDPOINT"):
        return env["LLM_ENDPOINT"].rstrip("/")
    host = env.get("LLM_HOST", DEFAULT_HOST)
    if backend == "vllm":
        return f"http://{host}:{GATEWAY_PORT}"
    return f"http://{host}:{env.get('LLM_PORT', OLLAMA_PORT)}"


def resolve_model(env: dict[str, str], role_key: str, fallback: str) -> str:
    """Return the LLM model name for a pipeline role, following the chain:
    role-specific key (if set and non-empty) ->
    LLM_DEFAULT_MODEL (if set and non-empty) ->
    fallback.

    Blank strings count as unset so an operator can leave `LLM_MODEL=` in
    their .env to document the key without overriding the default."""
    role = env.get(role_key, "").strip()
    if role:
        return role
    default = env.get("LLM_DEFAULT_MODEL", "").strip()
    if default:
        return default
    return fallback
