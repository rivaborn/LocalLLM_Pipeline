 Let me clarify, the stable version is 'cd C:\Coding\nmon'. The version I am editing is'cd C:\Coding\nmon2'.

● That changes the picture. Right now the install points at C:\Coding\nmon2 (the dev folder) — that's what pip show nmon
  reported. So the install is wrong: it needs to point at C:\Coding\nmon instead.

  Step 1: Repoint the install at the stable folder

  pip uninstall nmon -y
  cd C:\Coding\nmon
  pip install -e .

  -e keeps it editable, so if you ever update the stable copy in C:\Coding\nmon, the nmon command picks it up automatically. If
   you'd rather freeze the stable version so it never moves, drop the -e:
  pip install .   # frozen snapshot in site-packages, fully decoupled

  Verify:
  python -c "import nmon; print(nmon.__file__)"
  # Expected: C:\Coding\nmon\src\nmon\__init__.py   (stable)

  After this, typing nmon runs C:\Coding\nmon. Edits in C:\Coding\nmon2 no longer touch it.

  Step 2: Run the dev copy in C:\Coding\nmon2 on demand

  Option A — One-shot, no setup (best for occasional dev runs):
  cd C:\Coding\nmon2
  $env:PYTHONPATH = "$PWD\src"
  python -m nmon
  The PYTHONPATH override makes Python find nmon in C:\Coding\nmon2\src\ before checking site-packages. It only lasts for that
  PowerShell session — open a new shell and you're back to the stable install.

  Option B — Persistent dev venv (cleaner if you iterate often):
  cd C:\Coding\nmon2
  python -m venv .venv
  .\.venv\Scripts\Activate.ps1
  pip install -e .
  nmon                       # runs C:\Coding\nmon2\src\nmon
  deactivate                 # back to global → stable C:\Coding\nmon

  The venv has its own nmon.exe shim pointing at nmon2. The global one still points at nmon. Fully separated.

  Sanity check after either option

  Inside the dev environment:
  python -c "import nmon; print(nmon.__file__)"
  # Expected: C:\Coding\nmon2\src\nmon\__init__.py
  Outside it:
  python -c "import nmon; print(nmon.__file__)"
  # Expected: C:\Coding\nmon\src\nmon\__init__.py

  If both shells print the path you expect, the separation is real and you can edit nmon2 freely without ever touching the
  stable nmon.