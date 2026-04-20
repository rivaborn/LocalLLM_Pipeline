# progress.py -- File-Based Progress Tracking

## Overview

`Common/_pipeline/progress.py` provides file-based progress tracking for
pipeline runs. It preserves the key=value line format used by the legacy
PowerShell pipelines, so an in-progress run migrated between the old and
new orchestrator reads identically.

**Location:** `LocalLLM_Pipeline/Common/_pipeline/progress.py`

---

## Classes

### `ProgressState`

```python
@dataclass
class ProgressState:
    last_completed: int
    sub_step: int | None
    mode: str | None
    target_dir: str | None
```

Immutable snapshot of progress read from disk.

| Field            | Type            | Description                                     |
| ---------------- | --------------- | ----------------------------------------------- |
| `last_completed` | `int`           | Last fully completed step number (-1 if none)   |
| `sub_step`       | `int` or `None` | Sub-step within the current step                |
| `mode`           | `str` or `None` | Pipeline mode (e.g. `"allclaude"`, `"default"`) |
| `target_dir`     | `str` or `None` | Target directory for the run                    |

---

### `ProgressFile`

```python
class ProgressFile:
    def __init__(self, path: Path) -> None
```

Manages reading, writing, and clearing a progress sentinel file.

**Constructor parameters:**

| Name   | Type   | Description                                                       |
| ------ | ------ | ----------------------------------------------------------------- |
| `path` | `Path` | Path to the progress file (e.g. `.progress` or `.debug_progress`) |

#### Methods

##### `read`

```python
def read(self) -> ProgressState
```

Reads the progress file and returns a `ProgressState`. If the file does
not exist, returns a default state with `last_completed=-1`.

**Parsed keys:**

| Key             | Maps to          | Notes                                                 |
| --------------- | ---------------- | ----------------------------------------------------- |
| `LastCompleted` | `last_completed` | Must be a digit string                                |
| `SubStep`       | `sub_step`       | Must be a digit string                                |
| `Mode`          | `mode`           | Free-form string                                      |
| `TargetDir`     | `target_dir`     | Free-form string                                      |
| `Engine`        | `mode` (legacy)  | `"claude"` -> `"allclaude"`, `"local"` -> `"default"` |

The `Engine` key is a legacy compatibility shim from before the "AllClaude"
mode was introduced. It is only used if `Mode` was not already set.

**Example:**

```python
from pathlib import Path
from _pipeline.progress import ProgressFile

pf = ProgressFile(Path(".progress"))
state = pf.read()
if state.last_completed >= 3:
    print("Resuming from step 4")
```

##### `save`

```python
def save(
    self,
    step: int,
    *,
    sub_step: int | None = None,
    mode: str | None = None,
    target_dir: str | None = None,
) -> None
```

Writes the progress file. Always includes `LastCompleted` and `Timestamp`.
Optional fields are written only when provided.

**File format example:**

```
LastCompleted=3
Timestamp=2026-04-15 14:30:00
SubStep=2
Mode=allclaude
TargetDir=C:\Code\MyProject
```

**Parameters:**

| Name         | Type            | Default    | Description                |
| ------------ | --------------- | ---------- | -------------------------- |
| `step`       | `int`           | (required) | Step number just completed |
| `sub_step`   | `int` or `None` | `None`     | Sub-step within the step   |
| `mode`       | `str` or `None` | `None`     | Pipeline mode string       |
| `target_dir` | `str` or `None` | `None`     | Target directory path      |

##### `clear`

```python
def clear(self) -> None
```

Deletes the progress file if it exists. Called at the end of a successful
pipeline run.

---

## File Format

The progress file uses a simple `KEY=VALUE` format, one pair per line:

```
LastCompleted=5
Timestamp=2026-04-15 14:30:00
SubStep=0
Mode=default
TargetDir=C:\Code\MyProject
```

This format is intentionally compatible with the PowerShell pipeline's
progress files, allowing seamless migration between orchestrators.

---

## Dependencies

- `datetime` (stdlib)
- `dataclasses` (stdlib)
- `pathlib` (stdlib)
- No third-party packages

---

## Usage by Other Modules

```python
# In the orchestrator
from _pipeline.progress import ProgressFile

progress = ProgressFile(work_dir / ".progress")
state = progress.read()

for step in range(state.last_completed + 1, total_steps):
    run_step(step)
    progress.save(step, mode="default", target_dir=str(target))

progress.clear()  # Clean up on success
```
