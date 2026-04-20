> How do I install the program in C:\Coding\WorkFolder\nmonLocalLLM\src\nmon in a .venv and run it?

● There's already a .venv at C:\Coding\WorkFolder\nmonLocalLLM\.venv (Python 3.14.2, created by the earlier pipeline
  run). And pyproject.toml is already in place with nmon declared as a package, a nmon = "nmon.main:run" console entry
  point, and requires-python = ">=3.11".

  Fast path — use the existing venv:

  cd C:\Coding\WorkFolder\nmonLocalLLM
  .\ActivateEnv.ps1                                # or: .\.venv\Scripts\Activate.ps1
  python -m pip install --upgrade pip
  python -m pip install -e .[dev]
  nmon --help                                       # installed console script

  pip install -e .[dev] installs nmon in editable mode (so your edits in src/nmon/ take effect without reinstall) plus
  the dev extras (pytest, pytest-asyncio, pytest-mock, mypy, ruff).

  If install fails under Python 3.14 (some wheel missing), recreate the venv with 3.12 — that's already on your machine
  via the Astral build, and it's the safest version for the declared requires-python = ">=3.11":

  cd C:\Coding\WorkFolder\nmonLocalLLM
  deactivate                                        # if the old venv is active
  py -V:Astral/CPython3.12.13 -m venv .venv --clear
  .\ActivateEnv.ps1
  python -m pip install --upgrade pip
  python -m pip install -e .[dev]

  Run the app:

  nmon                                              # uses the console entry point
  # equivalent:
  python -m nmon.main

  Run the tests:

  pytest                                            # finds tests/ automatically
  pytest tests/test_config.py                       # single file

  Lint / type-check (from the dev extras):

  ruff check src tests
  mypy src

  Caveats for this specific project — Stage 4 didn't finish cleanly during our earlier runs, so expect import errors on
  first launch:
  - Several sub-package __init__.py files weren't generated (earlier audit). pip install -e . will still succeed, but
  import nmon.gpu may fail with ModuleNotFoundError until you touch the missing __init__.py files:
  foreach ($d in 'gpu','llm','storage','ui','ui\widgets') {
      $p = "src\nmon\$d\__init__.py"
      if (-not (Test-Path $p)) { New-Item -ItemType File -Path $p | Out-Null; Write-Host "created $p" }
  }
  - src/nmon/gpu/monitor.py may be corrupted from the failed Step 16 retries — if from nmon.gpu.monitor import
  GpuMonitor errors, regenerate via python C:\Coding\LocalLLM_Pipeline\LocalLLMCoding\run_aider.py --only-step 15 (venv
  doesn't need to be active for that).
  - Some steps (18, 20, 21, and later UI-heavy steps) may still be empty or partially-generated depending on how far
  Stage 4 got. pytest will tell you which tests fail to import — those point at the files that need completion.