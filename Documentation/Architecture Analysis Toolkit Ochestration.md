# Instructions for Using Arch_Analysis_Pipeline.py and test_arch_analysis_pipeline.py

## Overview

This document provides instructions on how to use the `Arch_Analysis_Pipeline.py` script for orchestrating the architecture analysis pipeline and how to run its corresponding unit tests using `test_arch_analysis_pipeline.py`.

## Prerequisites

1. **Python 3.12+**: Ensure you have Python 3.12 or a later version installed.
2. **Dependencies**:
   - No external dependencies are required beyond the standard library.
   - For testing, ensure `pytest` is installed (`pip install pytest`).

## Directory Structure

Ensure your project directory has the following structure (example with nmon):

```
C:\Coding\nmon\
│
├── src\                          Source code to analyse (subsection target)
│
└── LocalLLM_Pipeline\
    ├── Common\
    │   ├── .env                  Shared config (Analysis / Debug / Coding)
    │   └── llm_common.ps1        Shared helper module
    └── LocalLLMAnalysis\
        ├── Arch_Analysis_Pipeline.py
        ├── test_arch_analysis_pipeline.py
        └── ...
```

`Arch_Analysis_Pipeline.py` reads `LocalLLM_Pipeline/Common/.env` (not a local `.env`) and runs subprocesses with `cwd` set to the actual repo root (two levels up from `LocalLLMAnalysis/`).

## Usage of Arch_Analysis_Pipeline.py

### 1. Running the Pipeline

To run the architecture analysis pipeline, execute `Arch_Analysis_Pipeline.py` with the desired options.

#### Basic Command

```powershell
python ..\LocalLLM_Pipeline\LocalLLMAnalysis\Arch_Analysis_Pipeline.py
```

#### Options

- **--dry-run**: Simulate the pipeline without executing any commands.

  ```powershell
  python ..\LocalLLM_Pipeline\LocalLLMAnalysis\Arch_Analysis_Pipeline.py --dry-run
  ```

- **--start-from N**: Start the pipeline from a specific subsection (1-based index).

  ```powershell
  python ..\LocalLLM_Pipeline\LocalLLMAnalysis\Arch_Analysis_Pipeline.py --start-from 3
  ```

- **--skip-lsp**: Skip the LSP extraction steps.

  ```powershell
  python ..\LocalLLM_Pipeline\LocalLLMAnalysis\Arch_Analysis_Pipeline.py --skip-lsp
  ```

### 2. Example Commands

- **Full Run**:

  ```powershell
  python ..\LocalLLM_Pipeline\LocalLLMAnalysis\Arch_Analysis_Pipeline.py
  ```

- **Dry Run Starting from Subsection 3**:

  ```powershell
  python ..\LocalLLM_Pipeline\LocalLLMAnalysis\Arch_Analysis_Pipeline.py --dry-run --start-from 3
  ```

- **Skip LSP Steps**:

  ```powershell
  python ..\LocalLLM_Pipeline\LocalLLMAnalysis\Arch_Analysis_Pipeline.py --skip-lsp
  ```

## Usage of test_arch_analysis_pipeline.py

### Running Tests

To run the unit tests for `Arch_Analysis_Pipeline.py`, use `pytest`.

#### Basic Command

```powershell
python -m pytest LocalLLM_Pipeline\LocalLLMAnalysis\test_arch_analysis_pipeline.py -v
```

- **-v**: Verbose mode to see detailed test output.

### Example Commands

- **Run All Tests**:

  ```powershell
  python -m pytest LocalLLM_Pipeline\LocalLLMAnalysis\test_arch_analysis_pipeline.py -v
  ```

## Notes

- Ensure `LocalLLM_Pipeline/Common/.env` is correctly formatted with the `#Subsections begin` and `#Subsections end` markers. Comment lines inside the block are ignored by the parser.
- The pipeline creates `architecture/` at the repository root; it does not need to exist beforehand.
- After each subsection completes, `architecture/` is renamed to `N. <subsection>`. If you want the Debug pipeline to consume these outputs, update `ARCHITECTURE_DIR` in `Common/.env` to point at the renamed folder.

---

## Troubleshooting

- **FileNotFoundError (.env)**: Verify `LocalLLM_Pipeline/Common/.env` exists. `get_env_file()` in `Arch_Analysis_Pipeline.py` resolves this path via `script_dir.parent / "Common" / ".env"`.
- **subprocess.CalledProcessError**: Check the logs for detailed error messages from the failed commands.

For any issues or further assistance, please refer to the plan document or contact the development team.
