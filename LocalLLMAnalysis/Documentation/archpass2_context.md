# archpass2_context.ps1 -- Per-File Targeted Context Builder for Pass 2

## Purpose

`archpass2_context.ps1` prepares targeted context files for the second-pass analysis
(`archpass2_local.ps1`). Rather than feeding the entire `architecture.md` and `xref_index.md` to
every Pass 2 LLM call (which would exceed local LLM context windows), this script extracts only the
sections relevant to each source file -- matching subsystem headings from the architecture overview
and cross-reference entries that mention the file.

The result is a set of small `.ctx.txt` files, one per source file, containing just the architecture
context and cross-reference entries that are relevant to that specific file. This dramatically
reduces the token budget needed for Pass 2 and improves LLM output quality by focusing context.

This script makes **zero LLM calls** -- it is pure text processing and runs in seconds even on
large codebases.

## Prerequisites

| Requirement                    | Details                                                               |
| ------------------------------ | --------------------------------------------------------------------- |
| PowerShell 5.1+ or pwsh 7+     | Uses `Set-StrictMode -Version Latest`                                 |
| `../Common/.env`               | Configuration file                                                    |
| `architecture/architecture.md` | Output of `arch_overview_local.ps1` (or any `*architecture.md` files) |
| `architecture/xref_index.md`   | Output of `archxref.ps1` (or any `*xref_index.md` files)              |
| Per-file docs                  | Output of `archgen_local.ps1` in `architecture/`                      |

## Usage

```powershell
.\archpass2_context.ps1 [-TargetDir <dir>] [-EnvFile <path>] [-Test]
```

### CLI Options

| Parameter    | Type   | Default          | Description                                        |
| ------------ | ------ | ---------------- | -------------------------------------------------- |
| `-TargetDir` | string | `"."`            | Subdirectory to process. `"."` processes all docs. |
| `-EnvFile`   | string | `../Common/.env` | Alternative `.env` file.                           |
| `-Test`      | switch | off              | Run the built-in unit test suite.                  |

## How It Is Invoked

**Standalone:**
```powershell
cd C:\Coding\MyProject
.\LocalLLMAnalysis\archpass2_context.ps1
```

**Via ArchPipeline.py (analysis mode):**
Called as the fifth analysis step (after `archgen_local.ps1`, `archxref.ps1`, `archgraph.ps1`,
`arch_overview_local.ps1`):
```
python Common/ArchPipeline.py analysis
```

## Input Files

| Input                    | Location                                                      | Description                                    |
| ------------------------ | ------------------------------------------------------------- | ---------------------------------------------- |
| Architecture overview(s) | `<repo>/architecture/architecture.md` (or `*architecture.md`) | Subsystem headings and descriptions            |
| Cross-reference index    | `<repo>/architecture/xref_index.md` (or `*xref_index.md`)     | Function-to-file mappings, call edges, globals |
| Per-file docs            | `<repo>/architecture/**/*.md`                                 | Used to enumerate files needing context        |

## Output Files

| Output                 | Location                                                | Description                                                                         |
| ---------------------- | ------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| Targeted context files | `<repo>/architecture/.pass2_context/<rel_path>.ctx.txt` | One file per source file containing relevant architecture sections and xref entries |

### Context File Format

Each `.ctx.txt` file contains:
```
=== TARGETED CONTEXT FOR: <relative_path> ===

## Architecture Context (subsystem)
<matching sections from architecture.md>

## Cross-Reference Entries
<matching xref lines mentioning this file>
```

## Matching Logic

- **Subsystem keys** are derived from the file's directory path: the last 1, 2, and 3 directory
  components. For example, `Engine/Source/Runtime/Core/Private/Math.cpp` produces keys `Private`,
  `Core/Private`, and `Runtime/Core/Private`.
- **Architecture sections** are matched when a `##` heading contains any subsystem key
  (case-insensitive). Matching stops at the next non-matching `##` heading or after 30 lines.
- **Xref entries** are matched by filename or full relative path substring match.
- Cross-reference entries are capped at 50 per file.

## Environment Variables / .env Keys

This script reads `.env` for basic configuration but does not use LLM-specific keys.

## Exit Codes

| Code | Meaning                                                                       |
| ---- | ----------------------------------------------------------------------------- |
| `0`  | Success                                                                       |
| `2`  | Missing `architecture.md` or `xref_index.md` (run prerequisite scripts first) |

## Examples

**Example 1: Build context for all files**
```powershell
cd C:\Coding\Generals
.\LocalLLMAnalysis\archpass2_context.ps1
```
Reads `architecture.md` and `xref_index.md`, extracts targeted context for every per-file doc,
writes `.ctx.txt` files to `architecture/.pass2_context/`.

**Example 2: Run the unit test suite**
```powershell
.\archpass2_context.ps1 -Test
```
Tests `Get-SubsystemKeys`, `Extract-ArchSections`, `Extract-XrefEntries`,
`Build-TargetedContext`, `Get-DocRelPath`, and end-to-end integration. Returns 0 on success.

**Example 3: Process a specific subdirectory**
```powershell
.\archpass2_context.ps1 -TargetDir Generals/Code/GameEngine
```
Only builds context files for docs under the `GameEngine` directory tree.
