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
import datetime
import os
import re
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
# Make Common/_pipeline importable so we can reuse env helpers.
sys.path.insert(0, str(SCRIPT_DIR.parent / "Common"))
from _pipeline import config as cfg  # noqa: E402

DEFAULT_FIX_MODEL = "qwen3-coder:30b"


def resolve_fix_config() -> tuple[str, str]:
    """Return (aider_model_string, ollama_endpoint) using the shared
    endpoint-resolution precedence from _pipeline.config."""
    env = cfg.load_env()
    model = env.get("LLM_FIX_IMPORTS_MODEL", DEFAULT_FIX_MODEL)
    aider_model = f"ollama_chat/{model}"
    endpoint = cfg.resolve_ollama_endpoint(env)
    return aider_model, endpoint


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
    cmd = ["aider", "--no-git", "--yes", "--model", model, "--message", message, *files]
    print(f"    aider --no-git --yes --model {model} <message> {' '.join(files)}")
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
    parser.add_argument(
        "--log",
        default="fix_imports.log",
        help="Path to log file recording each iteration's failures "
             "(default: ./fix_imports.log, relative to cwd)",
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

    log_path = Path(args.log).resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"# fix_imports.py log\n")
        log.write(f"Started: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n")
        log.write(f"Package: {pkg_dir}\n")
        log.write(f"Model:   {fix_model}\n\n")
        print(f"[fix_imports] logging to {log_path}")

        for iteration in range(1, args.max_iters + 1):
            print(f"\n=== Import check iteration {iteration}/{args.max_iters} ===")
            log.write(f"\n## Iteration {iteration}/{args.max_iters}\n")
            log.write(f"Time: {datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n\n")
            failures = run_iteration(pkg_dir, args.python)

            if not failures:
                msg = "All modules import cleanly."
                print(f"\n{msg}")
                log.write(f"{msg}\n")
                log.flush()
                return

            header = f"{len(failures)} module(s) failed."
            print(f"\n{header}")
            log.write(f"{header}\n\n")

            # Log ALL failures up front so the full picture is on disk
            # before we burn time invoking aider (which may hang, time out,
            # or be interrupted).
            log.write("### Summary\n")
            for mod_name, path, err in failures:
                rel_path = path.relative_to(src_root.parent)
                last_line = err.splitlines()[-1] if err else "<no stderr>"
                log.write(f"- {mod_name} ({rel_path}): {last_line}\n")
            log.write("\n### Full errors\n\n")
            related_for: dict[str, Path | None] = {}
            for mod_name, path, err in failures:
                rel_path = path.relative_to(src_root.parent)
                related = locate_related_file(err, src_root)
                related_for[mod_name] = related
                log.write(f"#### {mod_name}  ({rel_path})\n")
                if related:
                    log.write(f"Related file: {related.relative_to(src_root.parent)}\n")
                log.write("```\n")
                log.write(err if err else "<no stderr>")
                log.write("\n```\n\n")
            log.flush()

            # Dedupe by edit scope: when multiple failing modules trace back
            # to the same source file (e.g. __main__ fails because main_window
            # has a bad import), calling aider once per failure makes later
            # calls overwrite the earlier fix. Group failures whose aider
            # scope (target + related) is identical, and make one call per
            # unique scope combining their errors.
            groups: dict[tuple, dict] = {}
            for mod_name, path, err in failures:
                related = related_for[mod_name]
                scope_paths = sorted({str(path), str(related)} if related else {str(path)})
                key = tuple(scope_paths)
                if key not in groups:
                    groups[key] = {
                        "target": path, "related": related,
                        "modules": [], "errors": [],
                    }
                groups[key]["modules"].append(mod_name)
                groups[key]["errors"].append(err)

            print(f"\nInvoking aider to fix... ({len(groups)} unique edit scope(s) "
                  f"for {len(failures)} failure(s))")
            for key, g in groups.items():
                mods = ", ".join(g["modules"])
                rel_target = g["target"].relative_to(src_root.parent)
                print(f"\n--- {rel_target}  (modules: {mods}) ---")
                for mod_name, err in zip(g["modules"], g["errors"]):
                    last_line = err.splitlines()[-1] if err else "<no stderr>"
                    print(f"    [{mod_name}] {last_line}")
                combined_err = "\n\n---\n\n".join(
                    f"# Module {m}\n{e}" for m, e in zip(g["modules"], g["errors"])
                )
                invoke_aider(g["target"], g["related"], combined_err, fix_model)

    msg = f"Still failing after {args.max_iters} iterations. Manual intervention needed."
    print(f"\n{msg}")
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n## Result\n{msg}\n")
    sys.exit(1)


if __name__ == "__main__":
    main()
