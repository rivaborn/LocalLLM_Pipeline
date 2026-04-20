"""argparse wiring + orchestration for run_aider."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from _pipeline import config as cfg

from .parser import parse_steps
from .runner import run_step

# Optional pyright integration; fail soft if _pipeline package missing.
try:
    from _pipeline.lsp_pyright import PyrightClient, pyright_available  # type: ignore
except Exception:  # noqa: BLE001
    PyrightClient = None                 # type: ignore
    pyright_available = lambda: False    # type: ignore


TOOLKIT_ROOT: Path = cfg.toolkit_root()
_SCRIPT_DIR = TOOLKIT_ROOT / "LocalLLMCoding"
_DEFAULT_LOCAL_MODEL = "qwen3-coder:30b"


def resolve_local_config() -> tuple[str, str]:
    """Return (endpoint, model) from Common/.env via shared config.
    Model resolution chains LLM_AIDER_MODEL -> LLM_DEFAULT_MODEL -> fallback."""
    env = cfg.load_env()
    endpoint = cfg.resolve_ollama_endpoint(env)
    model = cfg.resolve_model(env, "LLM_AIDER_MODEL", _DEFAULT_LOCAL_MODEL)
    return endpoint, model


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run aider steps from aidercommands.md")
    parser.add_argument("file", nargs="?", default=None,
                        help="Markdown file to process (default: aidercommands.md next to this script)")
    parser.add_argument("--from-step", type=int, default=1, metavar="N",
                        help="Start from step N (useful for resuming after a failure)")
    parser.add_argument("--only-step", type=int, metavar="N",
                        help="Run only step N")
    parser.add_argument("--model", default=None,
                        help="Aider model override (passed to aider verbatim). "
                             "Default: ollama_chat/<LLM_AIDER_MODEL from Common/.env>.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse and preview steps without running aider")
    parser.add_argument("--no-symbols", action="store_true",
                        help="Disable ctags symbol-inventory injection into prompts")
    parser.add_argument("--no-planned", action="store_true",
                        help="Disable forward-looking step-plan injection into prompts")
    parser.add_argument("--no-strict-outputs", action="store_true",
                        help="Don't fail a step when its declared output files "
                             "are missing or empty after aider exits 0")
    parser.add_argument("--empty-retries", type=int, default=2, metavar="N",
                        help="Retry up to N times if aider exits 0 but the "
                             "declared output files are empty (default: 2, "
                             "i.e. 3 total attempts). Set to 0 to disable.")
    parser.add_argument("--pyright", action="store_true",
                        help="Start a pyright-langserver and resolve CamelCase "
                             "symbol names in each step prompt against installed "
                             "packages + the workspace (helps with PyQt6 etc. that "
                             "ctags cannot index)")
    return parser


def _resolve_md_path(cli_file: str | None) -> Path:
    """Resolve aidercommands.md. Legacy locations tried when no absolute
    path is given."""
    if cli_file is None:
        candidates = [
            Path.cwd() / "LocalLLMCodePrompts" / "aidercommands.md",
            Path.cwd() / "aidercommands.md",
            _SCRIPT_DIR / "aidercommands.md",
        ]
        return next((c for c in candidates if c.exists()), candidates[0])
    md_path = Path(cli_file)
    if not md_path.is_absolute() and not md_path.exists():
        for base in (Path.cwd() / "LocalLLMCodePrompts", Path.cwd(), _SCRIPT_DIR):
            candidate = base / md_path
            if candidate.exists():
                return candidate
    return md_path


def _start_pyright(enabled: bool) -> object | None:
    if not enabled:
        return None
    if not pyright_available():
        print("[pyright] pyright-langserver not found on PATH. "
              "Install with: pip install pyright. Continuing without it.")
        return None
    if PyrightClient is None:
        print("[pyright] lsp_pyright module failed to import. Continuing without.")
        return None
    print("[pyright] starting pyright-langserver (workspace indexing takes a few seconds)...")
    client = PyrightClient(Path.cwd())
    try:
        client.start()
    except Exception as exc:  # noqa: BLE001
        print(f"[pyright] failed to start: {exc}. Continuing without.")
        return None
    print("[pyright] ready")
    return client


def main() -> None:
    args = _build_parser().parse_args()

    endpoint, local_model = resolve_local_config()
    os.environ["OLLAMA_API_BASE"] = endpoint
    if args.model is None:
        args.model = f"ollama_chat/{local_model}"
    print(f"[local] OLLAMA_API_BASE={endpoint}")
    print(f"[local] model={args.model}")

    md_path = _resolve_md_path(args.file)
    steps = parse_steps(str(md_path))
    print(f"Parsed {len(steps)} steps from {md_path}")

    if args.dry_run:
        for s in steps:
            print(f"\n  {s['title']}")
            print(f"    cmd:    {s['command']}")
            print(f"    prompt: {s['prompt'][:100].splitlines()[0]}...")
        return

    pyright_client = _start_pyright(args.pyright)
    failed_at = None
    try:
        for step in steps:
            n = step["number"]
            if args.only_step is not None and n != args.only_step:
                continue
            if n < args.from_step:
                print(f"  Skipping step {n} (--from-step {args.from_step})")
                continue
            future = [s for s in steps if s["number"] > n] if not args.no_planned else []
            ok = run_step(step, args.model, args.dry_run,
                          inject_symbols=not args.no_symbols,
                          future_steps=future,
                          strict_outputs=not args.no_strict_outputs,
                          pyright_client=pyright_client,
                          max_empty_retries=args.empty_retries)
            if not ok:
                failed_at = n
                break
    finally:
        if pyright_client is not None:
            pyright_client.shutdown()

    if failed_at:
        print(f"\n[STOPPED] Failed at step {failed_at}.")
        print(f"  Fix the issue then resume with: "
              f"python {Path(sys.argv[0]).as_posix()} --from-step {failed_at}")
        sys.exit(1)
    print(f"\n{'='*60}")
    print("  All steps completed.")
    print(f"{'='*60}")
