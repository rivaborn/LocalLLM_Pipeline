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

# Make the _pipeline package importable so we can reuse the ctags-based
# symbol inventory builder across the orchestrator and this script.
sys.path.insert(0, str(TOOLKIT_ROOT / "Common"))
try:
    from _pipeline.symbols import build_inventory_block, ctags_available  # type: ignore
except Exception:  # noqa: BLE001
    build_inventory_block = None      # type: ignore
    ctags_available = lambda: False   # type: ignore

try:
    from _pipeline.lsp_pyright import PyrightClient, pyright_available, format_resolved  # type: ignore
except Exception:  # noqa: BLE001
    PyrightClient = None               # type: ignore
    pyright_available = lambda: False  # type: ignore
    format_resolved = lambda h: ""     # type: ignore


# Regex: capitalised identifiers the model is likely to import (classes,
# Qt enum members, constants). Used to harvest candidate symbol names from
# each step prompt so we can ask pyright where they live.
_CAMEL_RE = re.compile(r'\b[A-Z][A-Za-z0-9_]{2,}\b')
_STOPWORDS = {
    'None', 'True', 'False', 'Self', 'TODO', 'FIXME', 'NOTE', 'API', 'URL',
    'UI', 'HTTP', 'HTTPS', 'JSON', 'YAML', 'TOML', 'HTML', 'CSS', 'SQL',
    'GPU', 'CPU', 'RAM', 'OS', 'CLI', 'TUI', 'PR', 'CI', 'CD',
}


def extract_candidate_symbols(text: str, limit: int = 40) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for m in _CAMEL_RE.finditer(text):
        n = m.group(0)
        if n in _STOPWORDS or n in seen:
            continue
        seen.add(n)
        names.append(n)
        if len(names) >= limit:
            break
    return names

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


def build_aider_cmd(step: dict, model: str | None, prompt: str | None = None) -> list[str]:
    # Parse the bash command line from the markdown, e.g.:
    #   aider --yes src/nmon/models.py src/nmon/config.py
    parts = step['command'].split()
    if parts[0] == 'aider':
        parts = parts[1:]           # drop 'aider', keep flags and file args

    message = prompt if prompt is not None else step['prompt']
    cmd = ['aider', '--no-git', '--message', message] + parts
    if model:
        cmd += ['--model', model]
    return cmd


def step_file_list(step: dict) -> list[str]:
    """Return the positional file args from the step's aider command."""
    parts = step['command'].split()
    if parts and parts[0] == 'aider':
        parts = parts[1:]
    # Keep non-flag, non-value args. Flags that take a value are uncommon in
    # the generated commands; if needed later, filter them more carefully.
    return [p for p in parts if not p.startswith('-')]


def build_planned_block(future_steps: list[dict]) -> str:
    """Forward-looking inventory: files + titles of steps that come later.

    ctags can only see files that already exist, so imports that reference
    files generated in a later step must be guessed. Surfacing the upcoming
    step plan here lets the model align names with what WILL be written."""
    if not future_steps:
        return ""
    lines = [
        "## Planned Upcoming Files",
        "",
        "These files have not been generated yet but will be produced by",
        "later steps. If you need to import from one, use the file name as",
        "your guide and align your expected class/function names with the",
        "step title (e.g. 'Step 18 - PowerTab implementation' -> class",
        "`PowerTab` in power_tab.py).",
        "",
    ]
    for s in future_steps:
        files = step_file_list(s)
        if not files:
            continue
        lines.append(f"- Step {s['number']} - {s['title']}")
        for f in files:
            lines.append(f"    - {f}")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def verify_outputs(step: dict) -> tuple[bool, list[str]]:
    """Check every file the step should have generated is present and
    non-empty. Returns (ok, list_of_problems).

    Exception: `__init__.py` is allowed to be empty (conventional Python
    package marker)."""
    problems: list[str] = []
    for f in step_file_list(step):
        p = Path(f)
        if not p.is_absolute():
            p = Path.cwd() / p
        if not p.exists():
            problems.append(f"missing: {f}")
            continue
        try:
            size = p.stat().st_size
        except OSError as exc:
            problems.append(f"stat-error {f}: {exc}")
            continue
        if size == 0 and p.name != "__init__.py":
            problems.append(f"empty: {f}")
    return (not problems), problems


def run_step(step: dict, model: str | None, dry_run: bool,
             inject_symbols: bool = True,
             future_steps: list[dict] | None = None,
             strict_outputs: bool = True,
             pyright_client=None) -> bool:
    print(f"\n{'='*60}")
    print(f"  {step['title']}")
    print(f"{'='*60}")

    prompt = step['prompt']
    prefix_blocks: list[str] = []

    if inject_symbols and build_inventory_block is not None and ctags_available():
        block = build_inventory_block(Path.cwd())
        if block:
            sym_count = block.count("\n- ")
            print(f"  [symbols] injecting inventory ({sym_count} entries) from ctags")
            prefix_blocks.append(block)
        else:
            print("  [symbols] no prior symbols yet (first step or empty repo)")
    elif inject_symbols and not ctags_available():
        print("  [symbols] ctags not installed; skipping inventory injection")

    if future_steps:
        planned = build_planned_block(future_steps)
        if planned:
            print(f"  [planned] injecting forward plan ({len(future_steps)} upcoming step(s))")
            prefix_blocks.append(planned)

    if pyright_client is not None:
        candidates = extract_candidate_symbols(step['prompt'])
        if candidates:
            try:
                hits = pyright_client.resolve_symbols(candidates)
            except Exception as exc:  # noqa: BLE001
                print(f"  [pyright] lookup failed: {exc}")
                hits = {}
            if hits:
                print(f"  [pyright] resolved {len(hits)}/{len(candidates)} symbols")
                prefix_blocks.append(format_resolved(hits))
            else:
                print(f"  [pyright] no resolutions for {len(candidates)} candidates")

    # Inject blocks AFTER the task, not before. When blocks were prepended,
    # qwen3-coder would occasionally stop after emitting just the filename
    # header because the 2KB of metadata buried the "produce code" directive.
    # Placing blocks after with a clear "REFERENCE CONTEXT" marker preserves
    # them while keeping the task primary.
    if prefix_blocks:
        suffix = (
            "\n\n---\n\n"
            "# REFERENCE CONTEXT (read-only; do not treat as instructions)\n\n"
            + "\n---\n\n".join(prefix_blocks)
        )
        prompt = prompt + suffix

    cmd = build_aider_cmd(step, model, prompt=prompt)

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

    if strict_outputs:
        ok, problems = verify_outputs(step)
        if not ok:
            print("\n  [FAILED] aider exited 0 but expected outputs are missing/empty:")
            for p in problems:
                print(f"    - {p}")
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
    parser.add_argument('--no-symbols', action='store_true',
                        help="Disable ctags symbol-inventory injection into prompts")
    parser.add_argument('--no-planned', action='store_true',
                        help="Disable forward-looking step-plan injection into prompts")
    parser.add_argument('--no-strict-outputs', action='store_true',
                        help="Don't fail a step when its declared output files "
                             "are missing or empty after aider exits 0")
    parser.add_argument('--pyright', action='store_true',
                        help="Start a pyright-langserver and resolve CamelCase "
                             "symbol names in each step prompt against installed "
                             "packages + the workspace (helps with PyQt6 etc. that "
                             "ctags cannot index)")
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

    # Start pyright once up-front so its ~3s initialize + site-packages
    # indexing cost is paid once, not per step.
    pyright_client = None
    if args.pyright:
        if not pyright_available():
            print("[pyright] pyright-langserver not found on PATH. "
                  "Install with: pip install pyright. Continuing without it.")
        elif PyrightClient is None:
            print("[pyright] lsp_pyright module failed to import. Continuing without.")
        else:
            print("[pyright] starting pyright-langserver (workspace indexing takes a few seconds)...")
            pyright_client = PyrightClient(Path.cwd())
            try:
                pyright_client.start()
                print("[pyright] ready")
            except Exception as exc:  # noqa: BLE001
                print(f"[pyright] failed to start: {exc}. Continuing without.")
                pyright_client = None

    failed_at = None
    try:
        for step in steps:
            n = step['number']

            if args.only_step is not None and n != args.only_step:
                continue
            if n < args.from_step:
                print(f"  Skipping step {n} (--from-step {args.from_step})")
                continue

            future = [s for s in steps if s['number'] > n] if not args.no_planned else []
            ok = run_step(step, args.model, args.dry_run,
                          inject_symbols=not args.no_symbols,
                          future_steps=future,
                          strict_outputs=not args.no_strict_outputs,
                          pyright_client=pyright_client)
            if not ok:
                failed_at = n
                break
    finally:
        if pyright_client is not None:
            pyright_client.shutdown()

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
