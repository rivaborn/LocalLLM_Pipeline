# llm_common.ps1 -- Shared PowerShell Library

## Overview

`llm_common.ps1` is a shim that dot-sources two sub-modules, providing the
entire shared PowerShell library to the 8 worker scripts in the pipeline.
Worker scripts only need to dot-source `llm_common.ps1`; it loads everything.

**Files:**

| File               | Lines   | Responsibility                                                                                                          |
| ------------------ | ------- | ----------------------------------------------------------------------------------------------------------------------- |
| `llm_common.ps1`   | 17      | Shim -- loads the two sub-modules                                                                                       |
| `llm_core.ps1`     | ~210    | Backend + endpoint + model resolution, LLM invocation, .env parsing, cancel key, config lookup                         |
| `file_helpers.ps1` | 230     | Presets, hashing, fence-lang mapping, trivial-file detection, truncation, architecture/LSP resolution, progress display |

**Location:** `LocalLLM_Pipeline/Common/`

---

## How Workers Use It

Every worker script begins with:

```powershell
# Dot-source the shared library
. (Join-Path $PSScriptRoot '..\Common\llm_common.ps1')
```

This single line makes all the shared functions below available in the caller's scope.

---

## Module 1: llm_core.ps1

### Functions

---

### `Get-LLMBackend`

**Signature:** `Get-LLMBackend` (no parameters)

Returns the local backend, `vllm` (default) or `ollama`. Precedence:
`$env:LLM_BACKEND` -> config key `LLM_BACKEND` -> `vllm`. Throws on any other value.

---

### `Get-LLMEndpoint`

**Signature:** `Get-LLMEndpoint [-Backend <string>]` (defaults to `Get-LLMBackend`)

Resolves the backend base URL (no trailing slash):

- `vllm`   -> the LLMConfig OpenAI `/v1` gateway (default port `11430`)
- `ollama` -> the native Ollama server (default port `11434`)

**Resolution order:**

1. `$env:OLLAMA_API_BASE` (ollama backend only)
2. `$env:LLM_ENDPOINT`
3. Config key `LLM_ENDPOINT`
4. `LLM_HOST` + (`11430` for vllm, `LLM_PORT`/`11434` for ollama)

**Example:**

```powershell
Get-LLMEndpoint                    # -> "http://192.168.1.15:11430" (vllm default)
Get-LLMEndpoint -Backend ollama    # -> "http://192.168.1.40:11434"
```

---

### `Test-CancelKey`

**Signature:** `Test-CancelKey` (no parameters)

Polls the console for a **Ctrl+Q** keypress. If detected, prints a yellow
cancellation message and exits with code 130. If input is redirected (piped
stdin), returns immediately without checking.

Worker scripts call this between steps so the user can gracefully abort a
long-running pipeline.

**Example:**

```powershell
foreach ($file in $files) {
    Test-CancelKey
    # ... process $file ...
}
```

---

### `Invoke-LocalLLM`

**Signature:**

```powershell
Invoke-LocalLLM
    [-SystemPrompt <string>]
    [-UserPrompt <string>]
    [-Backend <string>]            # default: '' (auto-resolved: vllm | ollama)
    [-Endpoint <string>]           # default: '' (auto-resolved)
    [-Model <string>]              # default: 'qwen2.5-coder:14b'
    [-Temperature <double>]        # default: 0.1
    [-MaxTokens <int>]             # default: 800
    [-NumCtx <int>]                # default: -1 (read from config)
    [-Timeout <int>]               # default: 120 seconds
    [-MaxRetries <int>]            # default: 3
    [-RetryDelay <int>]            # default: 5 seconds
    [-Think <bool>]                # default: $false
    [-ThinkingFile <string>]       # default: ''
```

Calls the local LLM and returns the generated text (trimmed).

**Backend selects the wire protocol:**

| Backend                 | API Endpoint                           | Body format                                                     |
| ----------------------- | -------------------------------------- | --------------------------------------------------------------- |
| `vllm` (default)        | `/v1/chat/completions` (gateway)       | `model`, `messages`, `temperature`, `max_tokens`               |
| `ollama`, `NumCtx > 0`  | `/api/chat` (native Ollama)            | `options.num_ctx`, `options.temperature`, `options.num_predict` |
| `ollama`, `NumCtx == 0` | `/v1/chat/completions` (Ollama compat) | `temperature`, `max_tokens`                                     |

`Backend` defaults to `Get-LLMBackend`; `Endpoint` to `Get-LLMEndpoint -Backend`.
`NumCtx`/`Think` apply to the ollama backend only.

**Thinking model support:** On the ollama backend with `NumCtx > 0` and `Think`
`$true`, the request body includes `"think": true`; a `thinking` field in the
response is written to `ThinkingFile` if set. Inert on the vllm backend.

**Retry logic:** On failure (network error, empty response, garbled output),
retries up to `MaxRetries` times with `RetryDelay` seconds between attempts.
After exhausting retries, throws a terminating error.

**Sanity checks:**

- Empty or whitespace-only responses are rejected.
- Responses shorter than 20 characters or lacking ASCII alphanumeric content
  are rejected as garbled.
- If thinking content exists but the main content is empty/garbled, the error
  message suggests raising `LLM_PLANNING_MAX_TOKENS`.

**Example:**

```powershell
$result = Invoke-LocalLLM `
    -SystemPrompt "You are a code reviewer." `
    -UserPrompt   "Review this function: ..." `
    -Model        'qwen2.5-coder:14b' `
    -MaxTokens    1200 `
    -Think        $true `
    -ThinkingFile "C:\output\thinking.txt"
```

---

### `Read-EnvFile`

**Signature:** `Read-EnvFile($path)`

Parses a `.env` file into a hashtable. Handles:

- Comment lines (starting with `#`) and blank lines are skipped.
- Values are trimmed and stripped of surrounding single/double quotes.
- `$HOME` is replaced with `$env:USERPROFILE`.
- Leading `~` is replaced with `$env:USERPROFILE`.

**Parameters:**

| Name    | Type   | Description                      |
| ------- | ------ | -------------------------------- |
| `$path` | string | Absolute path to the `.env` file |

**Returns:** Hashtable `@{ KEY = 'value'; ... }`

**Example:**

```powershell
$cfg = Read-EnvFile (Join-Path $PSScriptRoot '.env')
$cfg['LLM_HOST']   # -> '192.168.1.40'
```

---

### `Cfg`

**Signature:** `Cfg($key, $default = '')`

Looks up a key in the script-scoped `$script:cfg` hashtable. If the key is
missing or empty, returns `$default`. This is the single config accessor
used by all other functions.

Worker scripts populate `$script:cfg` early:

```powershell
$script:cfg = Read-EnvFile (Join-Path $PSScriptRoot '..\Common\.env')
```

**Example:**

```powershell
$maxLines = [int](Cfg 'LLM_MAX_SOURCE_LINES' '3000')
```

---

## Module 2: file_helpers.ps1

### Functions

---

### `Get-SHA1`

**Signature:** `Get-SHA1($filePath)`

Returns the lowercase hex SHA1 hash of a file's contents.

**Example:**

```powershell
$hash = Get-SHA1 "C:\repo\src\main.cpp"
# -> "a3f2b8c1d4e5..."
```

---

### `Get-Preset`

**Signature:** `Get-Preset($name)`

Returns a hashtable defining file include/exclude regex patterns for a named
engine preset. Used to filter source files for analysis.

**Supported presets:**

| Name(s)                                       | Description                    |
| --------------------------------------------- | ------------------------------ |
| `quake`, `quake2`, `quake3`, `doom`, `idtech` | id Software C engine codebases |
| `unreal`, `ue4`, `ue5`                        | Unreal Engine C++/C#           |
| `godot`                                       | Godot engine (C++/GDScript/C#) |
| `unity`                                       | Unity (C#/shader)              |
| `source`, `valve`                             | Source Engine (Valve C++)      |
| `rust`                                        | Rust codebase (.rs, .toml)     |
| `python`, `py`                                | Python codebase (.py, .toml)   |
| `generals`, `cnc`, `sage`                     | C&C Generals / SAGE engine     |
| `''` (empty)                                  | Generic game engine fallback   |

**Return hashtable keys:**

| Key       | Description                               |
| --------- | ----------------------------------------- |
| `Include` | Regex matching file extensions to include |
| `Exclude` | Regex matching directory paths to exclude |
| `Desc`    | Human-readable description                |
| `Fence`   | Default markdown fence language           |

**Example:**

```powershell
$preset = Get-Preset 'python'
$preset.Include  # -> '\.(py|toml)$'
$preset.Fence    # -> 'python'
```

---

### `Get-FenceLang`

**Signature:** `Get-FenceLang($file, $def)`

Maps a file's extension to the appropriate markdown code-fence language tag.

**Parameters:**

| Name    | Type   | Description                               |
| ------- | ------ | ----------------------------------------- |
| `$file` | string | File path (extension is extracted)        |
| `$def`  | string | Fallback language if extension is unknown |

**Supported extensions:** `.c`, `.h`, `.cpp`, `.cc`, `.cs`, `.java`, `.py`,
`.rs`, `.lua`, `.gd`, `.swift`, `.m`, `.mm`, `.shader`, `.hlsl`, `.toml`,
`.tscn`, `.tres`, and more.

**Example:**

```powershell
Get-FenceLang "main.py" "text"   # -> "python"
Get-FenceLang "foo.xyz" "c"      # -> "c"
```

---

### `Test-TrivialFile`

**Signature:** `Test-TrivialFile($rel, $fullPath, $minLines)`

Detects auto-generated or trivial files that should be skipped or given a
stub doc instead of full analysis.

**Detection criteria (any triggers `$true`):**

1. Filename matches a trivial pattern (`.generated.h`, `.gen.cpp`,
   `Module.*.cpp`, `Classes.h`)
2. File has fewer than `$minLines` lines
3. After stripping blank lines, includes, pragmas, and comments, 2 or fewer
   substantive lines remain

**Parameters:**

| Name        | Type   | Description                                        |
| ----------- | ------ | -------------------------------------------------- |
| `$rel`      | string | Relative path (used for filename pattern matching) |
| `$fullPath` | string | Absolute path to read the file                     |
| `$minLines` | int    | Minimum line count threshold                       |

**Example:**

```powershell
if (Test-TrivialFile $rel $fullPath 10) {
    Write-TrivialStub $rel $outPath
    continue
}
```

---

### `Write-TrivialStub`

**Signature:** `Write-TrivialStub($rel, $outPath)`

Writes a minimal markdown stub for a trivial/generated file, noting that no
detailed analysis is needed.

---

### `Get-OutputBudget`

**Signature:** `Get-OutputBudget($lineCount)`

Returns an adaptive `max_tokens` value scaled to file size.

| Line count  | Budget   |
| ----------- | -------- |
| < 50        | 300      |
| < 200       | 400      |
| < 500       | 600      |
| >= 500      | 800      |

**Example:**

```powershell
$budget = Get-OutputBudget $srcLines.Count
$result = Invoke-LocalLLM -MaxTokens $budget ...
```

---

### `Truncate-Source`

**Signature:** `Truncate-Source($srcLines, $maxLines)`

Truncates source code to `$maxLines` by keeping the first half and last half,
with a `/* ... TRUNCATED ... */` marker in the middle. If the source is
already within the limit (or `$maxLines <= 0`), returns it unchanged.

**Example:**

```powershell
$truncated = Truncate-Source $lines 500
```

---

### `Resolve-ArchFile`

**Signature:**

```powershell
Resolve-ArchFile
    [-Name <string>]
    [-BaseDir <string>]   # default: current directory
```

Locates a file inside the `ARCHITECTURE_DIR` configured in .env. Supports
both absolute and relative architecture directory paths. Returns the resolved
path if the file exists, or empty string if not found.

**Config key:** `ARCHITECTURE_DIR`

**Example:**

```powershell
$archPath = Resolve-ArchFile -Name 'overview.md'
if ($archPath) { $archContent = Get-Content $archPath -Raw }
```

---

### `Get-SerenaContextDir`

**Signature:**

```powershell
Get-SerenaContextDir
    [-BaseDir <string>]   # default: current directory
```

Resolves the Serena context directory path from the `SERENA_CONTEXT_DIR`
config key. Returns the path if it exists as a directory, empty string
otherwise.

**Config key:** `SERENA_CONTEXT_DIR`

---

### `Load-CompressedLSP`

**Signature:** `Load-CompressedLSP($serenaContextDir, $rel)`

Loads the "Symbol Overview" section from a `.serena_context.txt` sidecar
file. These files are generated by the Serena LSP tool and contain structured
symbol information for each source file.

**Parameters:**

| Name                | Type   | Description                          |
| ------------------- | ------ | ------------------------------------ |
| `$serenaContextDir` | string | Path to the Serena context directory |
| `$rel`              | string | Relative path of the source file     |

The function looks for `<serenaContextDir>/<rel>.serena_context.txt` and
extracts the `## Symbol Overview` section using a regex.

**Returns:** The Symbol Overview text, or empty string if not found.

---

### `Show-SimpleProgress`

**Signature:** `Show-SimpleProgress($done, $total, $startTime)`

Displays a single-line progress indicator on the console with the format:

```
PROGRESS: 42/100  rate=1.23/s  eta=0h00m47s
```

Uses carriage return (`\r`) to overwrite the line in-place. Calculates
rate and ETA from elapsed time.

**Parameters:**

| Name         | Type     | Description               |
| ------------ | -------- | ------------------------- |
| `$done`      | int      | Number of items completed |
| `$total`     | int      | Total number of items     |
| `$startTime` | datetime | Pipeline start time       |

**Example:**

```powershell
$start = [datetime]::Now
for ($i = 0; $i -lt $total; $i++) {
    # ... process item ...
    Show-SimpleProgress ($i + 1) $total $start
}
```

---

## .env Keys Consumed

These keys are read via `Cfg` or `Read-EnvFile` across the three files:

| Key                  | Used by                | Default         | Description                                  |
| -------------------- | ---------------------- | --------------- | -------------------------------------------- |
| `LLM_BACKEND`        | `Get-LLMBackend`       | `vllm`          | `vllm` (gateway /v1) or `ollama` (native)    |
| `LLM_ENDPOINT`       | `Get-LLMEndpoint`      | (none)          | Full backend base URL (overrides host/port)  |
| `LLM_HOST`           | `Get-LLMEndpoint`      | `192.168.1.40` | Backend hostname                             |
| `LLM_PORT`           | `Get-LLMEndpoint`      | `11434`         | Ollama server port (ollama backend only)     |
| `LLM_NUM_CTX`        | `Invoke-LocalLLM`      | `0`             | Context window (ollama backend only)         |
| `ARCHITECTURE_DIR`   | `Resolve-ArchFile`     | (none)          | Path to architecture docs directory          |
| `SERENA_CONTEXT_DIR` | `Get-SerenaContextDir` | (none)          | Path to Serena LSP context files             |

---

## Dependencies

- **PowerShell 5.1+** (ships with Windows)
- **System.Security.Cryptography.SHA1** (.NET, available in all PowerShell versions)
- **Invoke-RestMethod** (built-in cmdlet for HTTP calls)
- No external modules required

---

## Architecture Notes

The `$script:cfg` hashtable is the central configuration store. Worker
scripts must populate it before calling any function that uses `Cfg`:

```powershell
$script:cfg = Read-EnvFile (Join-Path $PSScriptRoot '..\Common\.env')
```

`LLM_BACKEND` selects the wire protocol. The default `vllm` backend talks to the
LLMConfig OpenAI `/v1` gateway, which auto-loads the requested served-name model
onto the GPU on first request. The `ollama` backend talks to a raw Ollama server;
with `LLM_NUM_CTX > 0` it uses native `/api/chat` for `num_ctx` control and
thinking-model features, otherwise Ollama's OpenAI-compatible `/v1` path.
