# ui.py -- Terminal Output Helpers

## Overview

`Common/_pipeline/ui.py` provides ANSI color output, Ctrl+Q cancellation
handling, logging setup, and banner display for the Python pipeline. It is
ported from `Arch_Analysis_Pipeline.py` to maintain visual consistency
during the deprecation window of the legacy pipeline.

**Location:** `LocalLLM_Pipeline/Common/_pipeline/ui.py`

---

## Classes

### `Color`

```python
class Color:
    CYAN    = "\033[96m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    MAGENTA = "\033[95m"
    BLUE    = "\033[94m"
    RED     = "\033[91m"
    BOLD    = "\033[1m"
    RESET   = "\033[0m"
```

ANSI escape code constants for terminal coloring. All output functions in
this module accept a `color` parameter using these constants.

---

## Functions

### `cprint`

```python
def cprint(msg: str, color: str = Color.RESET) -> None
```

Prints a colored message to stderr. The color is applied as a prefix and
`Color.RESET` is appended automatically.

**Parameters:**

| Name    | Type   | Default       | Description                        |
| ------- | ------ | ------------- | ---------------------------------- |
| `msg`   | `str`  | (required)    | Message text                       |
| `color` | `str`  | `Color.RESET` | ANSI color code from `Color` class |

**Example:**

```python
from _pipeline.ui import cprint, Color

cprint("Processing step 3...", Color.CYAN)
cprint("Warning: file skipped", Color.YELLOW)
```

---

### `check_cancel`

```python
def check_cancel() -> None
```

Checks for a **Ctrl+Q** keypress on Windows. If detected, prints a yellow
cancellation message and exits with code 130. This mirrors the behavior of
`Test-CancelKey` in the PowerShell library.

**Platform behavior:**

- **Windows:** Uses `msvcrt.kbhit()` and `msvcrt.getwch()` to poll for
  keypresses. Detects `\x11` (Ctrl+Q).
- **Non-Windows:** Returns immediately (no-op) since `msvcrt` is not
  available.
- **Piped stdin:** Returns immediately if `sys.stdin` is not a TTY.

Call this function between pipeline steps to allow graceful cancellation.

**Example:**

```python
from _pipeline.ui import check_cancel

for step in steps:
    check_cancel()
    run_step(step)
```

---

### `enable_windows_ansi`

```python
def enable_windows_ansi() -> None
```

Enables VT100 escape-sequence processing on Windows terminals by calling
`os.system("")`, which triggers the `ENABLE_VIRTUAL_TERMINAL_PROCESSING`
flag. Call this once at startup before any colored output.

On non-Windows platforms, this is a no-op (the `os.system("")` call is
harmless).

**Example:**

```python
from _pipeline.ui import enable_windows_ansi

enable_windows_ansi()
# Now ANSI color codes work in Windows Terminal / cmd.exe
```

---

### `setup_logging`

```python
def setup_logging(
    log_path: Path,
    logger_name: str = "archpipeline",
) -> logging.Logger
```

Creates and configures a logger with both file and console handlers.

**Handlers:**

| Handler   | Level   | Format                                    | Output          |
| --------- | ------- | ----------------------------------------- | --------------- |
| File      | `DEBUG` | `%(asctime)s [%(levelname)s] %(message)s` | `log_path` file |
| Console   | `INFO`  | `%(message)s`                             | `sys.stderr`    |

The log directory is created automatically (`mkdir -p`). If the logger
already has handlers (e.g., from a re-import in tests), it is returned
as-is to avoid duplicate handlers.

**Parameters:**

| Name          | Type   | Default          | Description                         |
| ------------- | ------ | ---------------- | ----------------------------------- |
| `log_path`    | `Path` | (required)       | Path to the log file                |
| `logger_name` | `str`  | `"archpipeline"` | Logger name for `logging.getLogger` |

**Returns:** `logging.Logger` -- the configured logger instance.

**Example:**

```python
from pathlib import Path
from _pipeline.ui import setup_logging

logger = setup_logging(Path("output/pipeline.log"))
logger.info("Pipeline started")
logger.debug("This only goes to the file")
```

---

### `banner`

```python
def banner(
    title: str,
    color: str = Color.CYAN,
    width: int = 60,
) -> None
```

Prints a visually prominent section banner to stderr:

```
============================================================
  Pipeline Step 3: Code Analysis
============================================================
```

**Parameters:**

| Name    | Type   | Default      | Description                  |
| ------- | ------ | ------------ | ---------------------------- |
| `title` | `str`  | (required)   | Banner title text            |
| `color` | `str`  | `Color.CYAN` | Color for the banner lines   |
| `width` | `int`  | `60`         | Width of the separator lines |

**Example:**

```python
from _pipeline.ui import banner, Color

banner("Step 3: Code Analysis", Color.GREEN)
```

---

## Dependencies

- `logging` (stdlib)
- `os` (stdlib)
- `sys` (stdlib)
- `pathlib` (stdlib)
- `msvcrt` (stdlib, Windows-only, optional)
- No third-party packages

---

## Usage by Other Modules

```python
# Typical initialization in the orchestrator
from _pipeline.ui import enable_windows_ansi, setup_logging, banner, check_cancel, Color

enable_windows_ansi()
logger = setup_logging(Path("output/pipeline.log"))

banner("Architecture Analysis Pipeline", Color.CYAN)

for i, step in enumerate(steps):
    check_cancel()
    banner(f"Step {i+1}: {step.name}", Color.GREEN)
    step.run(logger)
```
