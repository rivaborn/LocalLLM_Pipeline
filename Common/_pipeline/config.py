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


DEFAULT_OLLAMA_ENDPOINT = "http://192.168.1.126:11434"


def resolve_ollama_endpoint(
    env: dict[str, str] | None = None,
    explicit: str | None = None,
    read_env_vars: bool = True,
) -> str:
    """Return the Ollama API base URL. Precedence:
        1. *explicit* arg (CLI --local-endpoint or similar)
        2. os.environ OLLAMA_API_BASE
        3. os.environ LLM_ENDPOINT
        4. .env LLM_ENDPOINT
        5. .env LLM_HOST + LLM_PORT
        6. DEFAULT_OLLAMA_ENDPOINT
    """
    if explicit:
        return explicit.rstrip("/")
    if env is None:
        env = load_env()
    if read_env_vars:
        if os.environ.get("OLLAMA_API_BASE"):
            return os.environ["OLLAMA_API_BASE"].rstrip("/")
        if os.environ.get("LLM_ENDPOINT"):
            return os.environ["LLM_ENDPOINT"].rstrip("/")
    if env.get("LLM_ENDPOINT"):
        return env["LLM_ENDPOINT"].rstrip("/")
    if env.get("LLM_HOST"):
        port = env.get("LLM_PORT", "11434")
        return f"http://{env['LLM_HOST']}:{port}"
    return DEFAULT_OLLAMA_ENDPOINT
