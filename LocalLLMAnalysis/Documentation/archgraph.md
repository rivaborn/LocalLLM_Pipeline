# archgraph.ps1 -- Call Graph & Dependency Diagram Generator

## Purpose

`archgraph.ps1` parses the per-file architecture Markdown documents produced by `archgen_local.ps1`
and extracts function definitions and call edges from the `## Key Functions / Methods` sections. It
then generates two Mermaid diagrams: a **function-level call graph** showing significant functions
grouped by subsystem, and a **subsystem dependency diagram** showing cross-subsystem call counts.
Both diagrams are also combined into a single Markdown file with embedded Mermaid blocks and
summary statistics.

This script makes **zero LLM calls** -- it is pure text processing. It runs fast even on 1000+ file
codebases. The graph is filtered by a configurable significance threshold (minimum incoming call
count) and a maximum edge cap to keep diagrams readable.

## Prerequisites

| Requirement                | Details                                                           |
| -------------------------- | ----------------------------------------------------------------- |
| PowerShell 5.1+ or pwsh 7+ | Uses `Set-StrictMode -Version Latest`                             |
| `../Common/.env`           | Configuration file (read for environment, but no LLM keys needed) |
| Per-file docs              | Output of `archgen_local.ps1` in `<repo>/architecture/`           |

**Note:** This script does NOT require `llm_common.ps1` or Ollama. It has its own `Read-EnvFile`
implementation.

## Usage

```powershell
.\archgraph.ps1 [-TargetDir <subsystem>] [-MaxCallEdges <n>] [-MinCallSignificance <n>] [-EnvFile <path>] [-Test]
```

### CLI Options

| Parameter              | Type   | Default          | Description                                                                                                                              |
| ---------------------- | ------ | ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `-TargetDir`           | string | `"."`            | Subdirectory within `architecture/` to scan. `"."` scans the entire tree.                                                                |
| `-MaxCallEdges`        | int    | `150`            | Maximum number of call edges to render in the function call graph.                                                                       |
| `-MinCallSignificance` | int    | `2`              | Minimum number of incoming calls for a callee to be considered "significant" and included in the graph. All callers are always included. |
| `-EnvFile`             | string | `../Common/.env` | Path to `.env` configuration file.                                                                                                       |
| `-Test`                | switch | off              | Run the built-in unit test suite (no external dependencies needed).                                                                      |

## How It Is Invoked

**Standalone:**
```powershell
cd C:\Coding\MyProject
.\LocalLLMAnalysis\archgraph.ps1
```

**Via ArchPipeline.py (analysis mode):**
Called as the third analysis step (after `archgen_local.ps1` and `archxref.ps1`):
```
python Common/ArchPipeline.py analysis
```

## Input Files

| Input         | Location                      | Description                                                    |
| ------------- | ----------------------------- | -------------------------------------------------------------- |
| Per-file docs | `<repo>/architecture/**/*.md` | Architecture docs with `## Key Functions` and call annotations |
| `.env`        | `../Common/.env`              | Read for configuration (only basic env parsing needed)         |

The parser extracts:
- `# <filepath>` heading to determine file path and subsystem (first path component)
- `### <function_name>` under `## Key Functions / Methods` for function definitions
- `- Calls: \`FuncA\`, \`FuncB\`` lines for call edges

## Output Files

| Output                      | Location                                 | Description                                                           |
| --------------------------- | ---------------------------------------- | --------------------------------------------------------------------- |
| Call graph (Mermaid)        | `<repo>/architecture/callgraph.mermaid`  | Raw Mermaid `graph LR` diagram with subsystem subgraphs               |
| Subsystem diagram (Mermaid) | `<repo>/architecture/subsystems.mermaid` | Raw Mermaid `graph TD` showing cross-subsystem edges with call counts |
| Combined Markdown           | `<repo>/architecture/callgraph.md`       | Both diagrams embedded in Markdown fenced blocks, plus statistics     |

When `-TargetDir` is set, output filenames are prefixed with the target leaf name.

## Environment Variables / .env Keys

This script only reads `.env` for basic configuration. It does not use any LLM-specific keys.

## Exit Codes

| Code | Meaning                                                |
| ---- | ------------------------------------------------------ |
| `0`  | Success                                                |
| `1`  | No per-file docs found (run `archgen_local.ps1` first) |

## Examples

**Example 1: Generate diagrams for the full codebase**
```powershell
cd C:\Coding\Generals
.\LocalLLMAnalysis\archgraph.ps1
```
Parses all per-file docs, outputs `callgraph.mermaid`, `subsystems.mermaid`, and `callgraph.md`
to `architecture/`.

**Example 2: Restrict to a subsystem with higher significance threshold**
```powershell
.\archgraph.ps1 -TargetDir GameEngine -MinCallSignificance 3 -MaxCallEdges 80
```
Generates diagrams only for the `GameEngine` subsystem, showing only functions called 3+ times
and capping at 80 edges.

**Example 3: Run the built-in tests**
```powershell
.\archgraph.ps1 -Test
```
Runs all unit tests for `SanitizeId`, `Parse-GraphDoc`, `Get-SignificantFunctions`,
`Build-CallGraph`, `Get-CrossSubsystemEdges`, `Build-SubsystemDiagram`,
`Build-CombinedMarkdown`, and end-to-end integration tests. Returns exit code 0 on success.
