# serena_extract.py -- clangd LSP Context Extraction (Python Core)

## Purpose

`serena_extract.py` is the Python core of the LSP-based context extraction system. It spawns one or
more clangd processes, communicates with them over LSP JSON-RPC via stdio, and extracts rich
semantic context for each C/C++ source file: symbol overviews (classes, structs, enums, functions,
methods, variables), incoming cross-file references, direct `#include` dependencies, and optionally
a trimmed source view that preserves only the key code sections identified by symbol ranges.

The script implements **adaptive parallel extraction**: it starts with a RAM-based estimate of how
many clangd instances to spawn, then dynamically scales workers up or down based on real-time memory
monitoring. Each worker maintains its own clangd process with automatic restart after crashes or
after processing a configurable number of files (to control memory growth). PCH files created during
the session are tracked and cleaned up at exit.

## Prerequisites

| Requirement | Details |
|---|---|
| Python 3.12+ | Run via `uv run --python 3.12` (typically invoked by `serena_extract.ps1`) |
| `clangd` | On PATH or provided via `--clangd-path` |
| `compile_commands.json` | At the repo root for clangd to resolve compilation flags |
| Sufficient RAM | Each clangd instance uses ~3-8 GB depending on project size |

**No pip dependencies** -- the script uses only the Python standard library.

## Usage

```bash
python serena_extract.py --repo-root <path> [options]
python serena_extract.py --test  # run unit tests
```

### CLI Options

| Parameter | Type | Default | Description |
|---|---|---|---|
| `--repo-root` | string | (required) | Repository root directory |
| `--target-dir` | string | `"."` | Subdirectory to scan |
| `--output-dir` | string | `architecture/.serena_context` | Output directory for context files |
| `--clangd-path` | string | `"clangd"` | Path to clangd binary |
| `--jobs` | int | `2` | clangd `-j` parallelism per instance (background index threads) |
| `--workers` | int | `0` | Max parallel clangd instances. `0` = auto based on free RAM. |
| `--file-list` | string | `None` | Path to a text file containing relative paths (one per line) |
| `--include-rx` | string | `\.(cpp\|cc\|cxx\|h\|hpp\|inl\|c)$` | Include regex for file extensions |
| `--exclude-rx` | string | (complex default) | Exclude regex for directories |
| `--force` | flag | off | Re-extract even if hash matches (context is up-to-date) |
| `--skip-refs` | flag | off | Skip reference queries (much faster, symbols only) |
| `--compress` | flag | off | Collapse class methods into counts, show top-15 symbols |
| `--min-free-ram` | float | `6.0` | Minimum free RAM in GB to maintain |
| `--ram-per-worker` | float | `5.0` | Estimated RAM per clangd instance in GB |
| `--test` | flag | off | Run unit tests (no clangd required) |

## How It Is Invoked

Almost always through `serena_extract.ps1`, which builds the argument list and runs:
```
uv run --python 3.12 serena_extract.py --repo-root <root> --target-dir <dir> ...
```

Can also be invoked directly:
```bash
python serena_extract.py --repo-root C:/Coding/Generals --target-dir . --include-rx "\.(cpp|h)$"
```

## Input Files

| Input | Location | Description |
|---|---|---|
| Source files | `<repo>/<target-dir>/` | Files matching `--include-rx` and not `--exclude-rx` |
| `compile_commands.json` | `<repo>/compile_commands.json` | Compilation database for clangd |
| `.cache/clangd/index/` | `<repo>/.cache/clangd/index/` | clangd background index (built automatically on first run) |

## Output Files

| Output | Location | Description |
|---|---|---|
| Context files | `<output-dir>/<rel>.serena_context.txt` | Per-file LSP context |
| Hash database | `<output-dir>/.state/hashes.tsv` | SHA-1 hashes for incremental skip |
| Performance log | `<output-dir>/.state/perf.log` | Tab-separated timing data per file |
| Error log | `<output-dir>/.state/errors.log` | Timestamped worker errors |

### Context File Format

Each `.serena_context.txt` file contains:

```
=== LSP CONTEXT FOR: <relative_path> ===

## Symbol Overview
### Classes / Structs / Enums
- MyClass (Class, lines 10-150)
### Functions
- DoWork (lines 200-250)
### Methods
- MyClass/Init (lines 12-30)
### File-Scope Variables
- gState (line 5)

## Incoming References (who calls/uses symbols defined here)
- DoWork:
  - src/caller.cpp:45
  - src/other.cpp:120

## Direct Include Dependencies
- CoreMinimal.h
- MyClass.h

## Trimmed Source (key sections only)
```cpp
// first 30 lines (includes) ...
// ... [gap] ...
// symbol ranges ...
```
```

When `--compress` is used, the Symbol Overview section is collapsed into a compact format.

## Architecture

### ClangdClient
Synchronous LSP client that talks to a clangd subprocess over stdio JSON-RPC. Provides:
- `did_open` / `did_close` for document lifecycle
- `document_symbol` for hierarchical symbol extraction
- `find_references` for incoming cross-file references
- Threaded reader loop for non-blocking response handling

### ExtractionWorker
Each worker runs in its own thread with a dedicated `ClangdClient`. Features:
- Pulls files from a shared thread-safe queue
- Automatic clangd restart after crashes (broken pipe / process exit)
- Automatic clangd restart after N files (configurable, default 1000) to control memory growth
- Retry-once on crash for the current file before marking it as failed

### Adaptive Scaling
The main monitor loop checks free RAM periodically and:
- **Scales down** (stops a worker) when free RAM drops below 4 GB
- **Scales up** (starts a new worker) when free RAM exceeds 10 GB and worker count is below max

### PCH Cleanup
clangd with `--pch-storage=disk` writes large `preamble-*.pch` files to the temp directory. The
script snapshots existing PCH files at startup and registers an `atexit` handler to clean up only
the PCH files created during the session.

## Environment Variables / .env Keys

This script does not read `.env` directly. All configuration is passed via CLI arguments from
`serena_extract.ps1`.

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Success (or all tests passed with `--test`) |
| `1` | No matching source files found, or tests failed |

## Examples

**Example 1: Full extraction with auto-scaling**
```bash
python serena_extract.py --repo-root C:/Coding/Generals --include-rx "\.(cpp|h)$" --exclude-rx "[/\\](\.git|Debug)([/\\]|$)"
```

**Example 2: Fast symbols-only extraction**
```bash
python serena_extract.py --repo-root . --skip-refs --workers 3
```
Runs 3 parallel clangd instances, extracting only symbols (no reference queries). Typically
5-10x faster than full extraction.

**Example 3: Extract specific files from a list**
```bash
echo "src/main.cpp" > files.txt
echo "src/engine.cpp" >> files.txt
python serena_extract.py --repo-root . --file-list files.txt
```

**Example 4: Run unit tests**
```bash
python serena_extract.py --test
```
Tests `flatten_symbols`, `uri_to_relpath`, `generate_trimmed_source`, `sha1_file`,
hash database operations, `collect_files`, PCH cleanup, symbol kinds, and the `_Future` class.
No clangd installation required.
