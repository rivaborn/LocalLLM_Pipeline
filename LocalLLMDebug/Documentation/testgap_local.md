# testgap_local.ps1

## Purpose

`testgap_local.ps1` performs a comprehensive test gap analysis on a codebase using a local LLM (via Ollama). It identifies source files with no corresponding test file, analyses the quality and completeness of existing test coverage, and produces a prioritised gap report. The script operates as a three-pass pipeline:

- **Pass 0 (static mapping):** Maps every source file to its test file(s) using naming conventions. Files with no matching test are flagged immediately as `[NO TEST FILE]`.
- **Pass 1 (per-file LLM analysis):** For each source file, sends the source code, its test file (if any), shared fixtures (`conftest.py`), and optionally integration tests to the LLM. The model identifies specific coverage gaps, missing edge cases, undertested error paths, and weak assertions.
- **Pass 2 (synthesis):** Combines all per-file analyses into a single prioritised `GAP_REPORT.md` with actionable recommendations.

## Prerequisites

- **PowerShell 5.1+** (Windows PowerShell or PowerShell Core)
- **Ollama** running locally with a code-capable model loaded
- **`llm_common.ps1`** -- shared module at `Common/llm_common.ps1`
- **`.env` file** -- at `Common/.env` (or custom path via `-EnvFile`)
- **Prompt files** in the `LocalLLMDebug/` directory:
  - `testgap_file_prompt.txt` (per-file analysis for files with a matching test)
  - `testgap_notest_prompt.txt` (per-file analysis for files with no test)
  - `testgap_synth_prompt.txt` (synthesis schema)
- A structured project with separate source and test directories. The default expectation is `src/` and `tests/` at the repo root.

## Usage

```powershell
.\LocalLLMDebug\testgap_local.ps1 [options]
```

### CLI Options

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `-SrcDir` | string | `"src"` | Source code directory (relative to repo root). |
| `-TestDir` | string | `"tests"` | Test directory (relative to repo root). Must exist. |
| `-Clean` | switch | off | Delete the entire `test_gaps/` directory and cache before running. |
| `-EnvFile` | string | `Common/.env` | Path to the `.env` configuration file. |

## How It Is Invoked

### Standalone

```powershell
cd C:\Projects\MyApp
.\LocalLLMDebug\testgap_local.ps1 -SrcDir src -TestDir tests
```

### Via Arch_Debug_Pipeline.ps1 (legacy orchestrator)

The legacy debug pipeline calls `testgap_local.ps1` as step 3 of its 6-step sequence:

```
Step 1: dataflow_local.ps1
Step 2: interfaces_local.ps1
Step 3: testgap_local.ps1       <-- this script
Step 4: bughunt_local.ps1
Step 5: LLM-based bug fixing
Step 6: Archive summary
```

### Via ArchPipeline.py

The unified `ArchPipeline.py` debug mode is not yet fully wired but is expected to call this script when completed.

## Input Files

| Input | Description |
|-------|-------------|
| `.py` source files under `SrcDir` | All Python files, excluding `__pycache__` and `.egg-info` directories. |
| Test files under `TestDir` | Matched to source files via naming conventions (see below). |
| `tests/conftest.py` (optional) | Shared pytest fixtures. When present, included in every per-file analysis prompt so the LLM understands available fixtures. |
| `tests/test_integration.py` (optional) | Integration tests. When present, included as supplementary context for files that have unit tests (not included for files with no test). |
| `Common/.env` | Configuration for LLM endpoint, model, temperature, and token limits. |
| `testgap_file_prompt.txt` | Prompt schema for files that have a matching test file. |
| `testgap_notest_prompt.txt` | Prompt schema for files with no test file. |
| `testgap_synth_prompt.txt` | Prompt schema for the synthesis pass. |

### Test File Discovery

The script uses three naming convention candidates to match source files to test files:

1. **Full path join:** `test_<subdir>_<subdir>_<stem>.py` -- e.g., `src/nmon/gpu/nvml_source.py` matches `tests/test_gpu_nvml_source.py`
2. **Stem only:** `test_<stem>.py` -- e.g., matches `tests/test_nvml_source.py`
3. **Stripped suffix:** Common implementation suffixes (`_source`, `_base`, `_impl`, `_helper`, `_utils`, `_util`) are stripped before matching -- e.g., `nvml_source.py` becomes `nvml`, matching `tests/test_gpu_nvml.py`

Path segments after `src/<package>/` are used to build the test file name. The `src/` and package-name directories are stripped.

## Output Files and Directories

| Output | Description |
|--------|-------------|
| `test_gaps/GAP_REPORT.md` | The final synthesised, prioritised test gap report. Includes an HTML comment header with generation metadata (date, codebase, source/test dirs, file count, model). |
| `test_gaps/<src_rel>.gap.md` | Per-file gap analysis in Markdown. One file per source module. |
| `test_gaps/.testgap_state/cache/<key>.txt` | Cached analysis results keyed by `<src_sha>_<test_sha>` (or `<src_sha>_notest` for files without tests). Only re-analyses files where either the source or test has changed. |
| `test_gaps/.testgap_state/last_error.log` | Timestamped log of analysis and synthesis failures. |

### Fallback Behavior

If the synthesis call fails, the script writes a fallback document containing all raw per-file analyses concatenated together, prefixed with the error message. The script exits with code 1 in this case, but partial results are preserved.

## Environment Variables / .env Keys

| Key | Default | Description |
|-----|---------|-------------|
| `PRESET` | `""` | Named preset for codebase description and language settings. |
| `CODEBASE_DESC` | from preset | Human-readable codebase description for LLM system prompts. |
| `DEFAULT_FENCE` | from preset | Code fence language identifier. |
| `MAX_FILE_LINES` | `800` | Files longer than this are truncated before sending to the LLM. |
| `LLM_ENDPOINT` or `LLM_HOST`+`LLM_PORT` | — | Ollama API endpoint. |
| `LLM_MODEL_HIGH_CTX` | fallback to `LLM_MODEL` | Preferred model name. High-context variant preferred because the synthesis pass reads ~10k tokens of per-file analyses. |
| `LLM_MODEL` | `qwen2.5-coder:14b` | Fallback model name. |
| `LLM_TEMPERATURE` | `0.1` | LLM sampling temperature. |
| `LLM_TIMEOUT` | `120` | Base per-request timeout in seconds. The synthesis call uses `3x` this value. |
| `TESTGAP_FILE_TOKENS` | `700` | Max output tokens per file analysis call (Pass 1). |
| `TESTGAP_SYNTH_TOKENS` | `1800` | Max output tokens for the synthesis call (Pass 2). |

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success. Gap report and per-file analyses written. |
| `1` | Source or test directory not found, no analyses succeeded, or synthesis failed (fallback written). |
| `2` | Required prompt file is missing. |

Individual analysis failures do not cause the script to exit. The synthesis proceeds with whatever analyses succeeded.

## Examples

### Example 1: Standard test gap analysis

```powershell
cd C:\Projects\MyApp
.\LocalLLMDebug\testgap_local.ps1
```

Uses the default `src/` and `tests/` directories. Maps source files to tests, analyses each pair, and produces `test_gaps/GAP_REPORT.md`.

### Example 2: Custom source and test directories

```powershell
.\LocalLLMDebug\testgap_local.ps1 -SrcDir lib/core -TestDir spec
```

Scans source files under `lib/core/` and looks for test files under `spec/`.

### Example 3: Clean start after changing prompts

```powershell
.\LocalLLMDebug\testgap_local.ps1 -Clean
```

Wipes the entire `test_gaps/` directory and cache, then re-analyses all files from scratch. Necessary when you have modified prompt files, since the cache is keyed by source/test content SHA1, not prompt content.

## Notes

- The script supports Ctrl+Q cancellation between files.
- The cache key is a combination of the source file SHA1 and the test file SHA1 (or `notest`). If either file changes, the analysis is re-run.
- Files with no matching test receive a different prompt (`testgap_notest_prompt.txt`) that focuses on recommending what tests should be written.
- The `conftest.py` content is included in every prompt so the LLM knows what shared fixtures are available.
- Integration tests from `test_integration.py` are included as supplementary context when analysing files that already have unit tests, but not for files with no test file.
- The system prompt identifies itself as a "senior Python engineer" -- this script is primarily designed for Python projects using pytest.
- The final output suggests loading in Claude Code: `Read test_gaps/GAP_REPORT.md`.
