from __future__ import annotations
import argparse
import dataclasses
import datetime
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

BEGIN_MARKER = "#Subsections begin"
END_MARKER = "#Subsections end"

# ANSI color codes for terminal output
class Color:
    CYAN    = "\033[96m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    MAGENTA = "\033[95m"
    BLUE    = "\033[94m"
    RED     = "\033[91m"
    BOLD    = "\033[1m"
    RESET   = "\033[0m"

def cprint(msg: str, color: str = Color.RESET) -> None:
    """Print a colored message to stderr (same stream as logger console handler)."""
    print(f"{color}{msg}{Color.RESET}", file=sys.stderr)

# ---------------------------------------------------------------------------
# check_cancel -- Poll for Ctrl+Q between long operations
#
# Windows: uses msvcrt non-blocking key polling. Any pending Ctrl+Q drains
# the keyboard buffer and exits 130. No-op on non-Windows (rely on Ctrl+C)
# or if stdin is not a tty.
#
# Child PowerShell scripts also poll independently and exit 130 themselves
# on Ctrl+Q, so pressing the shortcut while a subprocess is running is
# caught by the child and propagates back via the non-zero exit code (see
# run_pipeline's CalledProcessError handling).
# ---------------------------------------------------------------------------
try:
    import msvcrt  # Windows only
    _HAS_MSVCRT = True
except ImportError:
    _HAS_MSVCRT = False

def check_cancel() -> None:
    if not _HAS_MSVCRT:
        return
    if not sys.stdin.isatty():
        return
    while msvcrt.kbhit():  # type: ignore[name-defined]
        ch = msvcrt.getwch()  # type: ignore[name-defined]
        if ch == '\x11':  # Ctrl+Q
            cprint("", Color.RESET)
            cprint("[Ctrl+Q] User cancelled. Exiting cleanly...", Color.YELLOW)
            sys.exit(130)

@dataclasses.dataclass(frozen=True)
class PipelineStep:
    name: str              # Human-readable, e.g. "Per-file docs"
    script: str            # Filename, e.g. "archgen_local.ps1"
    args: list[str]        # Extra fixed args, e.g. ["-Preset", "generals"]
    use_target_dir: bool   # Whether to add -TargetDir <subsection>
    is_powershell: bool    # True for .ps1, False for .py

PIPELINE_STEPS = [
    PipelineStep("Per-file docs",        "archgen_local.ps1",       ["-Preset", "generals"], use_target_dir=True,  is_powershell=True),
    PipelineStep("Cross-reference index", "archxref.ps1",            [],                      use_target_dir=False, is_powershell=True),
    PipelineStep("Mermaid diagrams",      "archgraph.ps1",           [],                      use_target_dir=False, is_powershell=True),
    PipelineStep("Architecture overview", "arch_overview_local.ps1", [],                      use_target_dir=False, is_powershell=True),
    PipelineStep("Pass 2 context",        "archpass2_context.ps1",   [],                      use_target_dir=False, is_powershell=True),
    PipelineStep("Pass 2 analysis",       "archpass2_local.ps1",     [],                      use_target_dir=False, is_powershell=True),
]

def get_script_dir() -> Path:
    """Return the directory containing this script."""
    return Path(__file__).resolve().parent

def get_env_file(script_dir: Path) -> Path:
    """Return the shared .env path at LocalLLM_Pipeline/Common/.env."""
    env_path = script_dir.parent / "Common" / ".env"
    if not env_path.exists():
        raise FileNotFoundError(f".env not found at {env_path}")
    return env_path

def get_repo_root(script_dir: Path) -> Path:
    """Return the repository root of the codebase being analysed.

    The toolkit now lives at a fixed path (e.g. C:/Coding/LocalLLM_Pipeline/) and is
    used against multiple target codebases. The target is determined by the
    current working directory at invocation time: run this script from the root
    of the codebase you want to analyse, and src/, tests/, etc. will be
    resolved relative to that cwd.
    """
    return Path.cwd()

def parse_subsections(env_path: Path) -> list[str]:
    with open(env_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    in_block = False
    subsections = []
    
    for line in lines:
        stripped_line = line.strip()
        if stripped_line == BEGIN_MARKER:
            in_block = True
        elif stripped_line == END_MARKER:
            break
        elif in_block and stripped_line and not stripped_line.startswith('#'):
            subsections.append(stripped_line)
    
    return subsections

def sanitize_subsection_name(subsection: str) -> str:
    return subsection.strip().replace("\\", "_")

def setup_logging(log_path: Path) -> logging.Logger:
    logger = logging.getLogger("archpipeline")
    logger.setLevel(logging.DEBUG)
    
    log_path.parent.mkdir(parents=True, exist_ok=True)
    
    file_handler = logging.FileHandler(log_path, mode="a")
    file_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(logging.DEBUG)
    
    console_handler = logging.StreamHandler(sys.stderr)
    console_formatter = logging.Formatter("%(message)s")
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(logging.INFO)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

def build_command(step: PipelineStep, subsection: str | None, script_dir: Path) -> list[str]:
    script_path = script_dir / step.script
    if step.is_powershell:
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path)]
    else:
        cmd = [sys.executable, str(script_path)]
    
    if step.use_target_dir and subsection is not None:
        cmd.extend(["-TargetDir", subsection])
    
    cmd.extend(step.args)
    
    return cmd

def run_command(cmd: list[str], repo_root: Path, logger: logging.Logger, dry_run: bool = False) -> None:
    logger.info(f"Running: {' '.join(cmd)}")
    if dry_run:
        logger.info("[DRY RUN] Skipped")
        return

    # Stream output in real time (no capture_output) while also logging
    # stderr passes through directly to the terminal (for \r progress lines)
    proc = subprocess.Popen(
        cmd,
        cwd=str(repo_root),
        stdout=subprocess.PIPE,
        stderr=None,
        text=True,
        bufsize=1,
    )

    output_lines = []
    last_was_progress = False
    for line in proc.stdout:
        line = line.rstrip("\n\r")
        # Check if this is a carriage-return progress line (e.g. "PROGRESS: 5/227 ...")
        # These lines start with \r or contain \r, or start with "PROGRESS:"
        parts = line.rsplit("\r", 1)
        display = parts[-1]  # take the text after the last \r
        is_progress = display.lstrip().startswith("PROGRESS:")
        if is_progress:
            # Overwrite the current line in place
            sys.stdout.write(f"\r{display.ljust(80)}")
            sys.stdout.flush()
            last_was_progress = True
        else:
            if last_was_progress:
                sys.stdout.write("\n")  # finish the progress line before printing normal output
                last_was_progress = False
            print(display)
        logger.debug(display)
        output_lines.append(display)

    if last_was_progress:
        sys.stdout.write("\n")  # ensure we end on a newline

    proc.wait()

    if proc.returncode != 0:
        output_tail = "\n".join(output_lines[-50:])
        error_msg = f"Command failed (exit {proc.returncode}): {' '.join(cmd)}\noutput (last 50 lines):\n{output_tail}"
        logger.error(error_msg)
        raise subprocess.CalledProcessError(proc.returncode, cmd, output="\n".join(output_lines))

def is_subsection_completed(repo_root: Path, subsection: str) -> bool:
    """Check if a completed output folder already exists for this subsection.

    Completed folders are named like '1. CODE', '2. WIN32LIB', etc.
    """
    sanitized = sanitize_subsection_name(subsection)
    for item in repo_root.iterdir():
        if item.is_dir() and re.match(r'^\d+\.\s+', item.name) and item.name.endswith(sanitized):
            return True
    return False

def run_one_time_steps(script_dir: Path, repo_root: Path, logger: logging.Logger, dry_run: bool = False, skip_lsp: bool = False) -> None:
    if skip_lsp:
        logger.info("Skipping LSP steps")
        return
    
    cprint("=" * 60, Color.CYAN)
    cprint("  ONE-TIME SETUP STEPS", Color.CYAN + Color.BOLD)
    cprint("=" * 60, Color.CYAN)
    logger.info("=== One-time setup steps ===")

    cprint("  >> generate_compile_commands.py", Color.BLUE)
    run_command([sys.executable, str(script_dir / "generate_compile_commands.py")], repo_root, logger, dry_run)
    cprint("  >> serena_extract.ps1", Color.BLUE)
    run_command(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_dir / "serena_extract.ps1")], repo_root, logger, dry_run)

def run_pipeline(script_dir: Path, repo_root: Path, subsections: list[str], logger: logging.Logger, dry_run: bool = False, start_from: int = 1) -> None:
    total = len(subsections)

    step_colors = [Color.CYAN, Color.GREEN, Color.MAGENTA, Color.BLUE, Color.YELLOW, Color.CYAN]

    cprint("Press Ctrl+Q to cancel (checked between steps).", Color.BLUE)

    for i, subsection in enumerate(subsections, start=1):
        check_cancel()
        if i < start_from:
            cprint(f"  SKIP {i}/{total}: {subsection} (--start-from)", Color.YELLOW)
            logger.info(f"Skipping {i}/{total}: {subsection} (--start-from)")
            continue

        if is_subsection_completed(repo_root, subsection):
            cprint(f"  SKIP {i}/{total}: {subsection} (already completed)", Color.YELLOW)
            logger.info(f"Skipping {i}/{total}: {subsection} (already completed)")
            continue

        cprint("=" * 60, Color.GREEN)
        cprint(f"  SUBSECTION {i}/{total}: {subsection}", Color.GREEN + Color.BOLD)
        cprint("=" * 60, Color.GREEN)
        logger.info(f"Subsection {i}/{total}: {subsection}")

        for step_idx, step in enumerate(PIPELINE_STEPS, start=1):
            check_cancel()
            color = step_colors[(step_idx - 1) % len(step_colors)]
            cprint(f"  --- Step {step_idx}/{len(PIPELINE_STEPS)}: {step.name} ---", color + Color.BOLD)
            logger.info(f"  Step {step_idx}/{len(PIPELINE_STEPS)}: {step.name}")
            cmd = build_command(step, subsection, script_dir)
            try:
                run_command(cmd, repo_root, logger, dry_run)
            except subprocess.CalledProcessError as exc:
                # Exit code 130 from a child PowerShell script means the user
                # pressed Ctrl+Q inside that child. Propagate the cancellation
                # cleanly instead of dumping a Python traceback.
                if exc.returncode == 130:
                    cprint("", Color.RESET)
                    cprint("[Ctrl+Q] Child process cancelled by user. Exiting pipeline.", Color.YELLOW)
                    sys.exit(130)
                raise

    cprint("=" * 60, Color.GREEN)
    cprint("  PIPELINE COMPLETE", Color.GREEN + Color.BOLD)
    cprint("=" * 60, Color.GREEN)
    logger.info("Pipeline complete.")

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Architecture analysis pipeline orchestrator")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--start-from", type=int, default=1)
    parser.add_argument("--skip-lsp", action="store_true")
    
    args = parser.parse_args(argv)
    
    if args.start_from < 1:
        parser.error("--start-from must be >= 1")
    
    return args

def main() -> None:
    args = parse_args()
    script_dir = get_script_dir()
    repo_root = get_repo_root(script_dir)
    logger = setup_logging(script_dir / "pipeline.log")
    cprint("=" * 60, Color.CYAN)
    cprint(f"  ARCHITECTURE PIPELINE", Color.CYAN + Color.BOLD)
    cprint(f"  Started at {datetime.datetime.now():%Y-%m-%d %H:%M:%S}", Color.CYAN)
    cprint("=" * 60, Color.CYAN)
    logger.info(f"Pipeline started at {datetime.datetime.now()}")

    subsections = parse_subsections(get_env_file(script_dir))

    if len(subsections) == 0:
        cprint("ERROR: No subsections found in .env", Color.RED)
        logger.error("No subsections found in .env")
        sys.exit(1)
    
    if args.start_from > len(subsections):
        logger.error("--start-from exceeds subsection count")
        sys.exit(1)
    
    try:
        run_one_time_steps(script_dir, repo_root, logger, args.dry_run, args.skip_lsp)
        run_pipeline(script_dir, repo_root, subsections, logger, args.dry_run, args.start_from)
    except subprocess.CalledProcessError as e:
        cprint(f"PIPELINE FAILED: {e}", Color.RED + Color.BOLD)
        logger.error(f"PIPELINE FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        cprint(f"Unexpected error: {e}", Color.RED + Color.BOLD)
        logger.error(f"Unexpected error: {e}")
        sys.exit(2)

if __name__ == '__main__':
    # Enable ANSI escape codes on Windows
    if sys.platform == "win32":
        os.system("")  # triggers Windows to enable VT100 processing
    main()
