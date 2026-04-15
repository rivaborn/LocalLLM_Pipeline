"""Shared configuration: .env loading and subsection parsing.

The .env format mirrors LocalLLM_Pipeline/Common/.env. Subsections live
between '#Subsections begin' / '#Subsections end' markers; non-comment
non-blank lines inside the block are treated as subsection paths.
"""
from __future__ import annotations

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
