# subprocess_runner.py -- Streaming Subprocess Wrapper

## Overview

`Common/_pipeline/subprocess_runner.py` centralizes the pattern for spawning
PowerShell or Python child processes, streaming their stdout to the terminal
line-by-line, logging every line, and raising on non-zero exit. This ensures
progress bars from pip/tqdm render correctly and all output is captured in
the pipeline log.

**Location:** `LocalLLM_Pipeline/Common/_pipeline/subprocess_runner.py`

---

## Classes

### `StepFailed`

```python
class StepFailed(Exception)
```

Raised when a pipeline step exits with a non-zero code other than 130.
The error message includes the failed command, exit code, and the last 50
lines of output for diagnostics.

---

### `UserCancelled`

```python
class UserCancelled(Exception)
```

Raised when a child process exits with code 130, indicating that the user
pressed Ctrl+Q to cancel. The orchestrator catches this to perform a clean
shutdown.

---

## Functions

### `run_command`

```python
def run_command(
    cmd: list[str],
    cwd: Path,
    logger: logging.Logger,
    dry_run: bool = False,
) -> None
```

Spawns a subprocess and streams its output in real time.

**Parameters:**

| Name      | Type             | Default    | Description                                    |
| --------- | ---------------- | ---------- | ---------------------------------------------- |
| `cmd`     | `list[str]`      | (required) | Command and arguments to execute               |
| `cwd`     | `Path`           | (required) | Working directory for the subprocess           |
| `logger`  | `logging.Logger` | (required) | Logger for recording output                    |
| `dry_run` | `bool`           | `False`    | If True, logs the command but does not execute |

**Behavior:**

1. Logs the full command string at INFO level.
2. If `dry_run` is True, logs `[DRY RUN] Skipped` and returns.
3. Spawns the process with `stdout=PIPE` and `stderr=None`.
   - `stderr=None` lets progress bars (tqdm, pip) pass through directly to
     the terminal without buffering.
4. Reads stdout line-by-line, printing each line to the terminal (`flush=True`)
   and logging at DEBUG level.
5. After the process exits:
   - Exit code **130**: raises `UserCancelled`.
   - Exit code **non-zero**: raises `StepFailed` with the last 50 lines of
     output included in the error message.
   - Exit code **0**: returns normally.

**Raises:**

- `UserCancelled` -- on exit code 130 (Ctrl+Q).
- `StepFailed` -- on any other non-zero exit code.

**Example:**

```python
from pathlib import Path
from _pipeline.subprocess_runner import run_command, StepFailed, UserCancelled
from _pipeline.ui import setup_logging

logger = setup_logging(Path("output/pipeline.log"))

try:
    run_command(
        ["python", "analyze.py", "--target", "src/"],
        cwd=Path("C:/Code/MyProject"),
        logger=logger,
    )
except UserCancelled:
    logger.info("User cancelled the pipeline")
except StepFailed as e:
    logger.error(f"Step failed: {e}")
```

---

### `powershell_cmd`

```python
def powershell_cmd(script: Path, *args: str) -> list[str]
```

Builds a command list for running a PowerShell script with the standard
flags used throughout the pipeline.

**Parameters:**

| Name     | Type   | Description                               |
| -------- | ------ | ----------------------------------------- |
| `script` | `Path` | Path to the `.ps1` script                 |
| `*args`  | `str`  | Additional arguments passed to the script |

**Returns:** `list[str]` -- command list in the form:

```python
["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
 "-File", "<script>", *args]
```

**Flags used:**

| Flag                      | Purpose                                                       |
| ------------------------- | ------------------------------------------------------------- |
| `-NoProfile`              | Skip loading the user's PowerShell profile for faster startup |
| `-ExecutionPolicy Bypass` | Allow running unsigned scripts                                |
| `-File`                   | Execute the script at the given path                          |

**Example:**

```python
from pathlib import Path
from _pipeline.subprocess_runner import powershell_cmd, run_command

cmd = powershell_cmd(
    Path("C:/Coding/LocalLLM_Pipeline/LocalLLMAnalysis/Analyze-Step1.ps1"),
    "-TargetDir", "C:/Code/MyProject",
    "-Model", "qwen2.5-coder:14b",
)
# -> ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
#     "-File", "C:\\Coding\\...\\Analyze-Step1.ps1",
#     "-TargetDir", "C:\\Code\\MyProject", "-Model", "qwen2.5-coder:14b"]

run_command(cmd, cwd=Path("C:/Code/MyProject"), logger=logger)
```

---

## Dependencies

- `logging` (stdlib)
- `subprocess` (stdlib)
- `pathlib` (stdlib)
- No third-party packages

---

## Usage by Other Modules

```python
# In the orchestrator (ArchPipeline.py or mode scripts)
from _pipeline.subprocess_runner import run_command, powershell_cmd, StepFailed, UserCancelled
from _pipeline.config import toolkit_root

toolkit = toolkit_root()
script = toolkit / "LocalLLMAnalysis" / "Analyze-Step1.ps1"

cmd = powershell_cmd(script, "-TargetDir", target_dir, "-Preset", preset)

try:
    run_command(cmd, cwd=Path(target_dir), logger=logger)
except UserCancelled:
    progress.save(current_step)
    sys.exit(130)
except StepFailed:
    logger.error("Analysis step failed")
    raise
```
