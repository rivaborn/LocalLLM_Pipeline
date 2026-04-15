#!/usr/bin/env python3
"""
Parses aidercommands.md and runs aider --message for each step automatically.

The markdown file is expected to live alongside this script (e.g. in
``LocalLLMCoding/``). Aider itself is invoked from the current working
directory — so run this from the repo root you want aider to edit, e.g.::

    cd C:\\Coding\\nmonClaude
    python .\\LocalLLMCoding\\run_aider.py

Usage:
    python run_aider.py                                # run all steps against local Ollama
    python run_aider.py --from-step 5                  # resume from step 5
    python run_aider.py --dry-run                      # preview without running
    python run_aider.py --model ollama_chat/other:tag  # override model string passed to aider

By default, endpoint + model are read from Common/.env:
    endpoint = LLM_ENDPOINT or LLM_HOST:LLM_PORT
    model    = ollama_chat/<LLM_AIDER_MODEL>
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
TOOLKIT_ROOT = SCRIPT_DIR.parent   # e.g. C:\Coding\LocalLLM_Pipeline\

# Shared config lives alongside the toolkit, at LocalLLM_Pipeline/Common/.env.
# Legacy candidates cover earlier layouts where the toolkit was named
# 'nmonLocalLLM' (either sibling-of-project or nested-inside-project).
def _find_common_env() -> Path:
    candidates = [
        TOOLKIT_ROOT          / 'Common' / '.env',                       # self-contained toolkit
        TOOLKIT_ROOT.parent   / 'LocalLLM_Pipeline' / 'Common' / '.env', # toolkit as sibling of project
        TOOLKIT_ROOT.parent   / 'nmonLocalLLM'      / 'Common' / '.env', # legacy sibling name
        TOOLKIT_ROOT          / 'nmonLocalLLM'      / 'Common' / '.env', # legacy nested name
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]  # canonical expected location for error messages

COMMON_ENV = _find_common_env()

DEFAULT_LOCAL_ENDPOINT = 'http://192.168.1.126:11434'
DEFAULT_LOCAL_MODEL    = 'qwen3.5:27b'


def read_env_file(path: Path) -> dict:
    """Minimal .env parser mirroring the PowerShell Read-EnvFile helper."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, val = line.partition('=')
        out[key.strip()] = val.strip().strip('"').strip("'")
    return out


def resolve_local_config() -> tuple[str, str]:
    """
    Resolve (endpoint, model) from environment and Common/.env.
        endpoint: OLLAMA_API_BASE env > LLM_ENDPOINT env > .env LLM_ENDPOINT
                  > .env LLM_HOST+LLM_PORT > hardcoded default
        model:    .env LLM_AIDER_MODEL > hardcoded default
    """
    env = read_env_file(COMMON_ENV)

    if os.environ.get('OLLAMA_API_BASE'):
        endpoint = os.environ['OLLAMA_API_BASE']
    elif os.environ.get('LLM_ENDPOINT'):
        endpoint = os.environ['LLM_ENDPOINT']
    elif env.get('LLM_ENDPOINT'):
        endpoint = env['LLM_ENDPOINT']
    elif env.get('LLM_HOST'):
        endpoint = f"http://{env['LLM_HOST']}:{env.get('LLM_PORT', '11434')}"
    else:
        endpoint = DEFAULT_LOCAL_ENDPOINT
    endpoint = endpoint.rstrip('/')

    model = env.get('LLM_AIDER_MODEL', DEFAULT_LOCAL_MODEL)
    return endpoint, model


def parse_steps(md_path: str) -> list[dict]:
    content = Path(md_path).read_text(encoding="utf-8")

    # Find where each ## Step section begins
    step_re = re.compile(r'^## Step (\d+)', re.MULTILINE)
    matches = list(step_re.finditer(content))
    if not matches:
        sys.exit(f"No steps found in {md_path}")

    steps = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        section = content[start:end]

        # Title from the header line
        title_match = re.match(r'## (Step \d+ — .+)', section)
        title = title_match.group(1).strip() if title_match else f"Step {m.group(1)}"

        # Extract all fenced code blocks: (language, body)
        blocks = re.findall(r'```(\w*)\n(.*?)```', section, re.DOTALL)

        # First bash fence is the aider command; first non-bash fence is the
        # prompt body. Accept any non-bash language (or none) -- some coder
        # models annotate the prompt block with 'python' or similar.
        bash_cmd = None
        prompt = None
        for lang, body in blocks:
            body = body.strip()
            if lang == 'bash' and bash_cmd is None:
                bash_cmd = body
            elif lang != 'bash' and prompt is None:
                prompt = body

        # Fallback: some model outputs don't wrap the aider command in a bash
        # fence, leaving it as a bare line between the ## heading and the
        # prompt block. Find the first line in the section that starts with
        # 'aider ' (outside fenced content) and treat it as the command.
        if bash_cmd is None:
            outside = re.sub(r'```.*?```', '', section, flags=re.DOTALL)
            for ln in outside.splitlines():
                ln = ln.strip()
                if ln.startswith('aider '):
                    bash_cmd = ln
                    break

        if not bash_cmd or not prompt:
            print(f"  Warning: skipping unparseable section: {title}")
            continue

        steps.append({'number': int(m.group(1)), 'title': title,
                      'command': bash_cmd, 'prompt': prompt})

    return steps


def build_aider_cmd(step: dict, model: str | None) -> list[str]:
    # Parse the bash command line from the markdown, e.g.:
    #   aider --yes src/nmon/models.py src/nmon/config.py
    parts = step['command'].split()
    if parts[0] == 'aider':
        parts = parts[1:]           # drop 'aider', keep flags and file args

    cmd = ['aider', '--message', step['prompt']] + parts
    if model:
        cmd += ['--model', model]
    return cmd


def run_step(step: dict, model: str | None, dry_run: bool) -> bool:
    print(f"\n{'='*60}")
    print(f"  {step['title']}")
    print(f"{'='*60}")

    cmd = build_aider_cmd(step, model)

    # Show a readable preview (omit the long prompt body)
    preview = ' '.join(cmd[:cmd.index('--message')])
    print(f"  aider --message <prompt> {' '.join(cmd[cmd.index('--message')+2:])}")

    if dry_run:
        print(f"  [DRY RUN] prompt preview: {step['prompt'][:120].splitlines()[0]}...")
        return True

    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"\n  [FAILED] exit code {result.returncode}")
        return False

    print(f"\n  [DONE] {step['title']}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Run aider steps from aidercommands.md")
    parser.add_argument('file', nargs='?', default=None,
                        help="Markdown file to process (default: aidercommands.md next to this script)")
    parser.add_argument('--from-step', type=int, default=1, metavar='N',
                        help="Start from step N (useful for resuming after a failure)")
    parser.add_argument('--only-step', type=int, metavar='N',
                        help="Run only step N")
    parser.add_argument('--model', default=None,
                        help="Aider model override (passed to aider verbatim). "
                             "Default: ollama_chat/<LLM_AIDER_MODEL from Common/.env>.")
    parser.add_argument('--dry-run', action='store_true',
                        help="Parse and preview steps without running aider")
    args = parser.parse_args()

    # Always route through the local Ollama server. Endpoint and model come
    # from Common/.env; --model overrides the model string verbatim.
    endpoint, local_model = resolve_local_config()
    os.environ['OLLAMA_API_BASE'] = endpoint
    if args.model is None:
        args.model = f'ollama_chat/{local_model}'
    print(f"[local] OLLAMA_API_BASE={endpoint}")
    print(f"[local] model={args.model}")

    # Resolve the markdown path. Arch_Coding_Pipeline.ps1 writes to
    # <cwd>/LocalLLMCodePrompts/aidercommands.md when invoked from the project
    # root; prefer that, fall back to <cwd>/aidercommands.md, then the legacy
    # location next to this script.
    if args.file is None:
        candidates = [
            Path.cwd() / 'LocalLLMCodePrompts' / 'aidercommands.md',
            Path.cwd() / 'aidercommands.md',
            SCRIPT_DIR / 'aidercommands.md',
        ]
        md_path = next((c for c in candidates if c.exists()), candidates[0])
    else:
        md_path = Path(args.file)
        if not md_path.is_absolute() and not md_path.exists():
            for base in (Path.cwd() / 'LocalLLMCodePrompts', Path.cwd(), SCRIPT_DIR):
                candidate = base / md_path
                if candidate.exists():
                    md_path = candidate
                    break

    steps = parse_steps(str(md_path))
    print(f"Parsed {len(steps)} steps from {md_path}")

    if args.dry_run:
        for s in steps:
            print(f"\n  {s['title']}")
            print(f"    cmd:    {s['command']}")
            print(f"    prompt: {s['prompt'][:100].splitlines()[0]}...")
        return

    failed_at = None
    for step in steps:
        n = step['number']

        if args.only_step is not None and n != args.only_step:
            continue
        if n < args.from_step:
            print(f"  Skipping step {n} (--from-step {args.from_step})")
            continue

        ok = run_step(step, args.model, args.dry_run)
        if not ok:
            failed_at = n
            break

    if failed_at:
        print(f"\n[STOPPED] Failed at step {failed_at}.")
        print(
            f"  Fix the issue then resume with: "
            f"python {Path(sys.argv[0]).as_posix()} --from-step {failed_at}"
        )
        sys.exit(1)
    else:
        print(f"\n{'='*60}")
        print("  All steps completed.")
        print(f"{'='*60}")


if __name__ == '__main__':
    main()
