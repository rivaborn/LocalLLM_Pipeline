#!/usr/bin/env python3
"""
Post-generation import checker. Walks every .py file under a package root,
tries to import it in a fresh Python process, and invokes aider on failures
so the model can reconcile cross-file symbol mismatches (a common drift mode
when each file is generated in its own isolated aider step).

Usage (from project root, with the project venv activated):
    python ..\\LocalLLM_Pipeline\\LocalLLMCoding\\fix_imports.py
    python fix_imports.py --package src/nmon --max-iters 3

On each iteration it collects every failing module, asks aider to fix each
(including a sibling file referenced in the error when identifiable), then
re-checks. Stops when all imports succeed or --max-iters is exhausted.
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
COMMON_ENV = SCRIPT_DIR.parent / "Common" / ".env"

DEFAULT_FIX_MODEL = "qwen3-coder:30b"
DEFAULT_ENDPOINT = "http://192.168.1.126:11434"


def read_env_file(path: Path) -> dict[str, str]:
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


def resolve_fix_config() -> tuple[str, str]:
    """Return (model_string_for_aider, ollama_endpoint)."""
    env = read_env_file(COMMON_ENV)
    model = env.get("LLM_FIX_IMPORTS_MODEL", DEFAULT_FIX_MODEL)
    aider_model = f"ollama_chat/{model}"

    if os.environ.get("OLLAMA_API_BASE"):
        endpoint = os.environ["OLLAMA_API_BASE"]
    elif os.environ.get("LLM_ENDPOINT"):
        endpoint = os.environ["LLM_ENDPOINT"]
    elif env.get("LLM_ENDPOINT"):
        endpoint = env["LLM_ENDPOINT"]
    elif env.get("LLM_HOST"):
        endpoint = f"http://{env['LLM_HOST']}:{env.get('LLM_PORT', '11434')}"
    else:
        endpoint = DEFAULT_ENDPOINT
    return aider_model, endpoint.rstrip("/")


def find_modules(package_dir: Path) -> list[tuple[str, Path]]:
    """Return (dotted_module_name, file_path) for every .py under package_dir.

    The dotted name starts at package_dir.name so the caller's Python
    interpreter can import it directly (assuming the package is installed
    editable via `pip install -e .`).
    """
    root = package_dir.parent
    result: list[tuple[str, Path]] = []
    for py in sorted(package_dir.rglob("*.py")):
        rel = py.relative_to(root).with_suffix("")
        parts = list(rel.parts)
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        if not parts:
            continue
        result.append((".".join(parts), py))
    return result


def try_import(mod_name: str, python_exe: str) -> tuple[bool, str]:
    result = subprocess.run(
        [python_exe, "-c", f"import {mod_name}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return True, ""
    return False, result.stderr.strip()


# Patterns that surface the *other* module involved in a cross-file mismatch.
_RELATED_PATTERNS = [
    # "cannot import name 'X' from 'pkg.mod' (path)"
    re.compile(r"cannot import name '[^']+' from '([\w\.]+)'"),
    # "No module named 'pkg.mod'"
    re.compile(r"No module named '([\w\.]+)'"),
    # "from pkg.mod import X" in the traceback's File line snippet
    re.compile(r"from ([\w\.]+) import "),
]


def locate_related_file(stderr: str, src_root: Path) -> Path | None:
    """Best-effort: find a sibling .py file named in the import error."""
    for pat in _RELATED_PATTERNS:
        m = pat.search(stderr)
        if not m:
            continue
        dotted = m.group(1)
        candidate = src_root / Path(*dotted.split(".")).with_suffix(".py")
        if candidate.exists():
            return candidate
        # Also try as a package __init__.py
        init_candidate = src_root / Path(*dotted.split(".")) / "__init__.py"
        if init_candidate.exists():
            return init_candidate
    return None


def invoke_aider(target: Path, related: Path | None, err: str, model: str) -> int:
    files = [str(target)]
    if related and related != target:
        files.append(str(related))
    message = (
        "The following Python import fails. Resolve it by editing whichever "
        "file(s) need changes so the import succeeds. Align symbol names "
        "(classes, functions, attributes) between files; do not invent new "
        "symbols. Keep any symbol that is already used by other callers.\n\n"
        f"Error output:\n{err}"
    )
    cmd = ["aider", "--yes", "--model", model, "--message", message, *files]
    print(f"    aider --yes --model {model} <message> {' '.join(files)}")
    return subprocess.run(cmd).returncode


def run_iteration(package_dir: Path, python_exe: str) -> list[tuple[str, Path, str]]:
    modules = find_modules(package_dir)
    failures: list[tuple[str, Path, str]] = []
    for mod_name, path in modules:
        ok, err = try_import(mod_name, python_exe)
        status = "OK  " if ok else "FAIL"
        print(f"  [{status}] {mod_name}")
        if not ok:
            failures.append((mod_name, path, err))
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--package",
        default="src/nmon",
        help="Package directory to walk (default: src/nmon)",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter to use for import checks "
        "(default: current interpreter -- run this script inside the project venv)",
    )
    parser.add_argument(
        "--max-iters",
        type=int,
        default=3,
        help="Maximum fix iterations before giving up (default: 3)",
    )
    args = parser.parse_args()

    pkg_dir = Path(args.package).resolve()
    if not pkg_dir.is_dir():
        sys.exit(f"Package directory not found: {pkg_dir}")
    src_root = pkg_dir.parent

    fix_model, endpoint = resolve_fix_config()
    os.environ["OLLAMA_API_BASE"] = endpoint
    print(f"[fix_imports] OLLAMA_API_BASE={endpoint}")
    print(f"[fix_imports] model={fix_model}")

    for iteration in range(1, args.max_iters + 1):
        print(f"\n=== Import check iteration {iteration}/{args.max_iters} ===")
        failures = run_iteration(pkg_dir, args.python)

        if not failures:
            print("\nAll modules import cleanly.")
            return

        print(f"\n{len(failures)} module(s) failed. Invoking aider to fix...")
        for mod_name, path, err in failures:
            last_line = err.splitlines()[-1] if err else "<no stderr>"
            print(f"\n--- {mod_name} ({path.relative_to(src_root.parent)}) ---")
            print(f"    {last_line}")
            related = locate_related_file(err, src_root)
            invoke_aider(path, related, err, fix_model)

    print(
        f"\nStill failing after {args.max_iters} iterations. "
        "Manual intervention needed."
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
