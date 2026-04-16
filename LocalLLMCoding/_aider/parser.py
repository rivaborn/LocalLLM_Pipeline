"""Parse aidercommands.md into a list of step dicts."""
from __future__ import annotations

import re
import sys
from pathlib import Path


def parse_steps(md_path: str) -> list[dict]:
    """Return [{number, title, command, prompt}, ...] from aidercommands.md.

    Each step in the markdown is introduced by `## Step N`. Within a section
    the parser expects a bash fence containing the `aider ...` command plus
    one other fenced block containing the prompt body. Missing a bash fence,
    a bare `aider ...` line inside the section is used as a fallback."""
    content = Path(md_path).read_text(encoding="utf-8")
    step_re = re.compile(r"^## Step (\d+)", re.MULTILINE)
    matches = list(step_re.finditer(content))
    if not matches:
        sys.exit(f"No steps found in {md_path}")

    steps: list[dict] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        section = content[start:end]

        title_match = re.match(r"## (Step \d+ — .+)", section)
        title = title_match.group(1).strip() if title_match else f"Step {m.group(1)}"

        blocks = re.findall(r"```(\w*)\n(.*?)```", section, re.DOTALL)
        bash_cmd: str | None = None
        prompt: str | None = None
        for lang, body in blocks:
            body = body.strip()
            if lang == "bash" and bash_cmd is None:
                bash_cmd = body
            elif lang != "bash" and prompt is None:
                prompt = body

        # Some generators leave the aider command as a bare line; scan for it.
        if bash_cmd is None:
            outside = re.sub(r"```.*?```", "", section, flags=re.DOTALL)
            for ln in outside.splitlines():
                ln = ln.strip()
                if ln.startswith("aider "):
                    bash_cmd = ln
                    break

        if not bash_cmd or not prompt:
            print(f"  Warning: skipping unparseable section: {title}")
            continue

        steps.append({"number": int(m.group(1)), "title": title,
                      "command": bash_cmd, "prompt": prompt})
    return steps


def step_file_list(step: dict) -> list[str]:
    """Return the positional file args from the step's aider command line."""
    parts = step["command"].split()
    if parts and parts[0] == "aider":
        parts = parts[1:]
    return [p for p in parts if not p.startswith("-")]
