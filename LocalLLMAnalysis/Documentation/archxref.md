# archxref.ps1 -- Cross-Reference Index Generator

## Purpose

`archxref.ps1` parses the per-file architecture documents produced by `archgen_local.ps1` and builds
a comprehensive cross-reference index. It extracts function definitions, caller-callee relationships,
global/file-static variable ownership, header `#include` dependencies, and subsystem interface
inventories. The result is a single `xref_index.md` file that serves as a structured lookup table
for the entire codebase.

This cross-reference index is consumed by downstream pipeline steps -- `arch_overview_local.ps1`,
`archpass2_context.ps1`, and `archpass2_local.ps1` -- to provide relationship context that a
per-file analysis alone cannot capture. The script makes **zero LLM calls**; it is pure text
processing and runs quickly even on 1000+ file codebases.

## Prerequisites

| Requirement                | Details                                                 |
| -------------------------- | ------------------------------------------------------- |
| PowerShell 5.1+ or pwsh 7+ | Uses `Set-StrictMode -Version Latest`                   |
| `../Common/.env`           | Configuration file                                      |
| Per-file docs              | Output of `archgen_local.ps1` in `<repo>/architecture/` |

**Note:** This script does NOT require `llm_common.ps1` or Ollama. It has its own `Read-EnvFile`.

## Usage

```powershell
.\archxref.ps1 [-TargetDir <subsystem>] [-EnvFile <path>] [-Test]
```

### CLI Options

| Parameter    | Type   | Default          | Description                                                                              |
| ------------ | ------ | ---------------- | ---------------------------------------------------------------------------------------- |
| `-TargetDir` | string | `"."`            | Subdirectory within `architecture/` to scan. `"."` or `"all"` scans the entire doc tree. |
| `-EnvFile`   | string | `../Common/.env` | Alternative `.env` file.                                                                 |
| `-Test`      | switch | off              | Run the built-in unit test suite.                                                        |

## How It Is Invoked

**Standalone:**
```powershell
cd C:\Coding\MyProject
.\LocalLLMAnalysis\archxref.ps1
```

**Via ArchPipeline.py (analysis mode):**
Called as the second analysis step (after `archgen_local.ps1`):
```
python Common/ArchPipeline.py analysis
```

## Input Files

| Input         | Location                      | Description                                                                                                     |
| ------------- | ----------------------------- | --------------------------------------------------------------------------------------------------------------- |
| Per-file docs | `<repo>/architecture/**/*.md` | Architecture docs with `## Key Functions`, `## Global / File-Static State`, `## External Dependencies` sections |
| `.env`        | `../Common/.env`              | Basic configuration                                                                                             |

### What the Parser Extracts

From each per-file doc, `Parse-DocFile` extracts:

- **Function definitions**: `### <name>` headings under `## Key Functions / Methods` or `## Key Methods`
- **Call edges**: Lines matching `- Calls: \`FuncA\`, \`FuncB\`` or `- Call: \`FuncC\`` under function headings
- **Global state**: Table rows under `## Global / File-Static State` with columns `Name | Type | Scope | Purpose`
- **Include dependencies**: Backtick-wrapped file references under `## External Dependencies` matching `name.ext` patterns

## Output Files

| Output                | Location                            | Description                                                         |
| --------------------- | ----------------------------------- | ------------------------------------------------------------------- |
| Cross-reference index | `<repo>/architecture/xref_index.md` | Comprehensive index (or `<prefix>_xref_index.md` with `-TargetDir`) |

### Index Sections

The generated `xref_index.md` contains:

1. **Function Definition Map** -- table mapping every function to its defining file
2. **Call Graph - Most Connected Functions** -- top 40 callers ranked by outgoing call count
3. **Most Called Functions** -- top 30 callees ranked by incoming call count, with caller list
4. **Global State Ownership** -- all global/file-static variables with type, scope, and owner file
5. **Header Dependencies** -- top 25 most-included headers by dependent count
6. **Subsystem Interfaces** -- functions grouped by top-level directory

## Environment Variables / .env Keys

This script reads `.env` for basic configuration only. No LLM-specific keys are used.

## Exit Codes

| Code | Meaning                                                |
| ---- | ------------------------------------------------------ |
| `0`  | Success                                                |
| `1`  | No per-file docs found (run `archgen_local.ps1` first) |

## Examples

**Example 1: Generate cross-reference index for the full codebase**
```powershell
cd C:\Coding\Generals
.\LocalLLMAnalysis\archxref.ps1
```
Parses all per-file docs and writes `architecture/xref_index.md`.

**Example 2: Generate index for a specific subsystem**
```powershell
.\archxref.ps1 -TargetDir GameEngine
```
Scans only `architecture/GameEngine/` and writes `architecture/GameEngine_xref_index.md`.

**Example 3: Run the unit test suite**
```powershell
.\archxref.ps1 -Test
```
Tests `Test-DocFileIncluded`, `Parse-DocFile` (function extraction, globals, deps, section
transitions, edge cases, call patterns, formatting), `Build-XrefOutput` (structure, content,
empty inputs), and end-to-end integration. Returns 0 on all-pass.
