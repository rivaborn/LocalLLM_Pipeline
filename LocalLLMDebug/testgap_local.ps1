# ============================================================
# testgap_local.ps1 -- Test Gap Analysis (Local LLM)
#
# Three passes:
#   Pass 0 (static):    Map every source file to its test file(s).
#                       Files with no match are flagged NONE immediately.
#   Pass 1 (per-file):  For each source file, send source + test +
#                       conftest.py to the LLM and identify gaps.
#                       Results are cached by SHA1.
#   Pass 2 (synthesis): Combine all per-file analyses into a single
#                       prioritised GAP_REPORT.md.
#
# Output:
#   test_gaps/GAP_REPORT.md          -- main prioritised report
#   test_gaps/<src_rel>.gap.md       -- per-file analysis
#
# Requires: llm_common.ps1, testgap_file_prompt.txt,
#           testgap_notest_prompt.txt, testgap_synth_prompt.txt
#
# Usage:
#   .\LocalLLMDebug\testgap_local.ps1 [-SrcDir <path>] [-TestDir <path>] [-Clean]
#
# Examples:
#   .\LocalLLMDebug\testgap_local.ps1
#   .\LocalLLMDebug\testgap_local.ps1 -SrcDir src -TestDir tests
#   .\LocalLLMDebug\testgap_local.ps1 -Clean
# ============================================================

[CmdletBinding()]
param(
    [string]$SrcDir  = "src",
    [string]$TestDir = "tests",
    [switch]$Clean,
    [string]$EnvFile = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($EnvFile -eq "") { $EnvFile = Join-Path $PSScriptRoot '..\Common\.env' }

# ── Load shared module ───────────────────────────────────────

. (Join-Path $PSScriptRoot '..\Common\llm_common.ps1')

# ── Load config ──────────────────────────────────────────────

$script:cfg = Read-EnvFile $EnvFile

$presetName   = Cfg 'PRESET' ''
$presetData   = Get-Preset $presetName
$codebaseDesc = Cfg 'CODEBASE_DESC' $presetData.Desc
$defaultFence = Cfg 'DEFAULT_FENCE' $presetData.Fence
$maxFileLines = [int](Cfg 'MAX_FILE_LINES'            '800')

$llmEndpoint   = Get-LLMEndpoint
# Prefer LLM_MODEL_HIGH_CTX if set (synth pass reads ~10k tokens of per-file
# gap analyses); otherwise fall back to the standard LLM_MODEL.
$llmModel      = Cfg 'LLM_MODEL_HIGH_CTX' (Cfg 'LLM_MODEL' 'qwen2.5-coder:14b')
$llmTemperature= [double](Cfg 'LLM_TEMPERATURE'   '0.1')
$llmTimeout    = [int](Cfg 'LLM_TIMEOUT'           '120')
$fileTokens    = [int](Cfg 'TESTGAP_FILE_TOKENS'   '700')
$synthTokens   = [int](Cfg 'TESTGAP_SYNTH_TOKENS'  '1800')

# ── Paths ────────────────────────────────────────────────────

$repoRoot = (Get-Location).Path
try {
    $g = & git rev-parse --show-toplevel 2>$null
    if ($LASTEXITCODE -eq 0 -and $g) { $repoRoot = $g.Trim() }
} catch {}

$srcRoot  = Join-Path $repoRoot $SrcDir
$testRoot = Join-Path $repoRoot $TestDir
$gapDir   = Join-Path $repoRoot 'test_gaps'
$stateDir = Join-Path $gapDir   '.testgap_state'
$cacheDir = Join-Path $stateDir 'cache'

foreach ($d in @($gapDir, $stateDir, $cacheDir)) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}

$reportFile = Join-Path $gapDir   'GAP_REPORT.md'
$errorLog   = Join-Path $stateDir 'last_error.log'

$tb = '```'

# Prompt files
$filePromptPath   = Join-Path $PSScriptRoot 'testgap_file_prompt.txt'
$notestPromptPath = Join-Path $PSScriptRoot 'testgap_notest_prompt.txt'
$synthPromptPath  = Join-Path $PSScriptRoot 'testgap_synth_prompt.txt'

foreach ($f in @($filePromptPath, $notestPromptPath, $synthPromptPath)) {
    if (-not (Test-Path $f)) {
        Write-Host "Missing prompt file: $f" -ForegroundColor Red; exit 2
    }
}

$filePromptSchema   = Get-Content $filePromptPath   -Raw
$notestPromptSchema = Get-Content $notestPromptPath -Raw
$synthPromptSchema  = Get-Content $synthPromptPath  -Raw

$fileSysPrompt   = "You are a senior Python engineer auditing test coverage for a $codebaseDesc. Be precise and use exact names from the source."
$synthSysPrompt  = "You are writing a prioritised test gap report for a $codebaseDesc. Be specific and actionable."

# ── Clean ────────────────────────────────────────────────────

if ($Clean) {
    Write-Host "CLEAN: removing test_gaps and cache..." -ForegroundColor Cyan
    Remove-Item -Path $gapDir -Recurse -Force -ErrorAction SilentlyContinue
    foreach ($d in @($gapDir, $stateDir, $cacheDir)) {
        New-Item -ItemType Directory -Force -Path $d | Out-Null
    }
}

'' | Set-Content $errorLog -Encoding UTF8

# ── Helpers ──────────────────────────────────────────────────

# Read a file's text, truncated to maxLines. Returns '' if not found.
function Read-Source($path) {
    if (-not (Test-Path $path)) { return '' }
    $lines = @(Get-Content $path -ErrorAction SilentlyContinue)
    if (-not $lines) { return '' }
    return Truncate-Source $lines $maxFileLines
}

# Given a source relative path, return the path of the corresponding test
# file (or $null). Tries several naming conventions.
function Find-TestFile($srcRel) {
    # srcRel e.g. "src/nmon/gpu/nvml_source.py"
    $normalized = $srcRel -replace '\\', '/'

    # Walk the path to find components after the top-level src/ directory
    $parts = $normalized -split '/'
    $srcIdx = [Array]::IndexOf($parts, 'src')
    # Skip src/ and the package name (nmon/) — start after src/<pkg>/
    $start = if ($srcIdx -ge 0) { [Math]::Min($srcIdx + 2, $parts.Count - 1) } else { 0 }
    $relParts = @($parts[$start..($parts.Count - 1)])

    # Strip .py from the last element
    $relParts[-1] = $relParts[-1] -replace '\.py$', ''

    # Candidate 1: test_<all parts joined with _>.py
    #   src/nmon/gpu/nvml_source.py → test_gpu_nvml_source.py
    $c1 = 'test_' + ($relParts -join '_') + '.py'
    $p1 = Join-Path $testRoot $c1
    if (Test-Path $p1) { return $p1 }

    # Candidate 2: test_<stem only>.py
    #   → test_nvml_source.py
    $stem = $relParts[-1]
    $c2 = "test_${stem}.py"
    $p2 = Join-Path $testRoot $c2
    if ((Test-Path $p2) -and $p2 -ne $p1) { return $p2 }

    # Candidate 3: strip common implementation suffixes from stem, rejoin
    #   nvml_source → nvml  → test_gpu_nvml.py
    $stemClean = $stem -replace '_(source|base|impl|helper|utils?)$', ''
    if ($stemClean -ne $stem) {
        $partsClean = $relParts[0..($relParts.Count - 2)] + @($stemClean)
        $c3 = 'test_' + ($partsClean -join '_') + '.py'
        $p3 = Join-Path $testRoot $c3
        if ((Test-Path $p3) -and $p3 -notin @($p1, $p2)) { return $p3 }
    }

    return $null
}

# ── Pass 0: Collect source files and map to test files ───────

Write-Host ''
Write-Host '============================================' -ForegroundColor Cyan
Write-Host '  testgap_local.ps1 -- Test Gap Analysis'   -ForegroundColor Cyan
Write-Host '============================================' -ForegroundColor Cyan
Write-Host "Repo root:   $repoRoot"
Write-Host "Source dir:  $SrcDir"
Write-Host "Test dir:    $TestDir"
Write-Host "LLM:         $llmModel @ $llmEndpoint"
Write-Host "File tokens: $fileTokens  |  Synth tokens: $synthTokens"
Write-Host "Output:      $reportFile"
Write-Host ''
Write-Host 'Press Ctrl+Q to cancel (checked between files).' -ForegroundColor DarkGray
Write-Host ''

if (-not (Test-Path $srcRoot)) {
    Write-Host "Source directory not found: $srcRoot" -ForegroundColor Red; exit 1
}
if (-not (Test-Path $testRoot)) {
    Write-Host "Test directory not found: $testRoot" -ForegroundColor Red; exit 1
}

# Collect .py source files, excluding __pycache__, egg-info, etc.
$srcFiles = @(Get-ChildItem -Path $srcRoot -Recurse -Filter '*.py' -File |
    Where-Object {
        $rel = $_.FullName.Substring($repoRoot.Length).TrimStart('\','/') -replace '\\','/'
        $rel -notmatch '__pycache__|\.egg-info'
    } | Sort-Object FullName |
    ForEach-Object { $_.FullName.Substring($repoRoot.Length).TrimStart('\','/') -replace '\\','/' })

# Read conftest.py (shared fixtures used in every test)
$conftestPath = Join-Path $testRoot 'conftest.py'
$conftestText = Read-Source $conftestPath

# Read integration test if present (referenced as supplementary context)
$integrationPath = Join-Path $testRoot 'test_integration.py'
$integrationText = Read-Source $integrationPath

Write-Host "Pass 0: mapping source files to test files..." -ForegroundColor Yellow
Write-Host ''

# Build the map: srcRel -> testPath (or $null)
$fileMap = @{}
$hasTest = 0
$noTest  = 0

foreach ($rel in $srcFiles) {
    $testPath = Find-TestFile $rel
    $fileMap[$rel] = $testPath
    if ($testPath) { $hasTest++ } else { $noTest++ }
    $status = if ($testPath) {
        $testName = [System.IO.Path]::GetFileName($testPath)
        "-> $testName"
    } else { '-> [NO TEST FILE]' }
    $color  = if ($testPath) { 'Gray' } else { 'Yellow' }
    Write-Host ("  {0,-55} {1}" -f $rel, $status) -ForegroundColor $color
}

Write-Host ''
Write-Host ("  {0} source files  |  {1} have tests  |  {2} have no test file" -f $srcFiles.Count, $hasTest, $noTest)
Write-Host ''

# ── Pass 1: Per-file gap analysis ────────────────────────────

Write-Host "Pass 1: per-file gap analysis..." -ForegroundColor Yellow
Write-Host ''

$analyses      = [System.Collections.Generic.List[string]]::new()
$analysisFailed= 0
$startTime     = [datetime]::Now
$fileIdx       = 0

foreach ($rel in $srcFiles) {
    Test-CancelKey
    $fileIdx++
    $testPath = $fileMap[$rel]
    $src      = Join-Path $repoRoot ($rel -replace '/', '\')
    $fence    = Get-FenceLang $rel $defaultFence

    # Build a cache key from SHA1 of source + test (empty string if no test)
    $srcSha  = Get-SHA1 $src
    $testSha = if ($testPath) { Get-SHA1 $testPath } else { 'notest' }
    $cacheKey = ($srcSha + '_' + $testSha)
    $cachePath = Join-Path $cacheDir ($cacheKey + '.txt')

    $label = [System.IO.Path]::GetFileName($rel)
    Write-Host ("  [{0}/{1}] {2}" -f $fileIdx, $srcFiles.Count, $rel) -NoNewline

    # Serve from cache if available
    if (Test-Path $cachePath) {
        $cached = Get-Content $cachePath -Raw -Encoding UTF8
        if ($cached -and $cached.Trim() -ne '') {
            $analyses.Add($cached.Trim())
            Write-Host ' [cached]' -ForegroundColor DarkGray
            continue
        }
    }

    # Build the user prompt
    $srcText = Read-Source $src

    if ($testPath) {
        # ── Has a test file ──────────────────────────────────
        $testText = Read-Source $testPath
        $testName = [System.IO.Path]::GetFileName($testPath)

        $extraCtx = ''
        if ($integrationText -ne '' -and $testPath -notmatch 'integration') {
            $extraCtx = @"

INTEGRATION TESTS (test_integration.py) -- may provide additional coverage:
${tb}python
$integrationText
${tb}
"@
        }

        $userPrompt = @"
$filePromptSchema

SOURCE FILE: $rel
${tb}$fence
$srcText
${tb}

TEST FILE: $testName
${tb}python
$testText
${tb}

SHARED FIXTURES (conftest.py):
${tb}python
$conftestText
${tb}
$extraCtx
"@
    }
    else {
        # ── No test file ─────────────────────────────────────
        $userPrompt = @"
$notestPromptSchema

SOURCE FILE: $rel
${tb}$fence
$srcText
${tb}

SHARED FIXTURES (conftest.py) -- available for use in future tests:
${tb}python
$conftestText
${tb}
"@
    }

    try {
        $result = Invoke-LocalLLM `
            -SystemPrompt $fileSysPrompt `
            -UserPrompt   $userPrompt `
            -Endpoint     $llmEndpoint `
            -Model        $llmModel `
            -Temperature  $llmTemperature `
            -MaxTokens    $fileTokens `
            -Timeout      $llmTimeout

        # Ensure starts with heading
        if ($result -notmatch '^#') {
            $hIdx = $result.IndexOf("`n#")
            $result = if ($hIdx -ge 0) { $result.Substring($hIdx + 1) } else { "# $rel`n`n$result" }
        }

        # Save to cache and output dir
        $result | Set-Content $cachePath -Encoding UTF8

        $gapOut = Join-Path $gapDir (($rel -replace '/', '\') + '.gap.md')
        $gapOutDir = Split-Path $gapOut -Parent
        New-Item -ItemType Directory -Force -Path $gapOutDir | Out-Null
        $result | Set-Content $gapOut -Encoding UTF8

        $analyses.Add($result.Trim())
        Write-Host ' done' -ForegroundColor Green
    }
    catch {
        $analysisFailed++
        $errMsg = "$(Get-Date -Format u) | FAIL | $rel | $($_.Exception.Message)"
        [System.IO.File]::AppendAllText($errorLog, "$errMsg`n")
        Write-Host " [FAIL] $($_.Exception.Message)" -ForegroundColor Red
    }
}

Write-Host ''
$elapsed1 = [math]::Round(([datetime]::Now - $startTime).TotalSeconds)
Write-Host ("Pass 1 complete: {0}/{1} analyses  ({2}s)" -f $analyses.Count, $srcFiles.Count, $elapsed1) -ForegroundColor Yellow
if ($analysisFailed -gt 0) {
    Write-Host ("  WARNING: {0} failure(s) -- synthesis will be incomplete." -f $analysisFailed) -ForegroundColor Yellow
}
Write-Host ''

if ($analyses.Count -eq 0) {
    Write-Host "No analyses succeeded -- cannot synthesize." -ForegroundColor Red; exit 1
}

# ── Pass 2: Synthesis ─────────────────────────────────────────

Write-Host "Pass 2: synthesising gap report..." -ForegroundColor Yellow

$allAnalyses = $analyses -join "`n`n---`n`n"

$synthPrompt = @"
$synthPromptSchema

CODEBASE: $codebaseDesc
SOURCE DIR: $SrcDir   TEST DIR: $TestDir

BEGIN PER-FILE ANALYSES
$allAnalyses
END PER-FILE ANALYSES
"@

$startTime2 = [datetime]::Now
try {
    $synthResult = Invoke-LocalLLM `
        -SystemPrompt $synthSysPrompt `
        -UserPrompt   $synthPrompt `
        -Endpoint     $llmEndpoint `
        -Model        $llmModel `
        -Temperature  $llmTemperature `
        -MaxTokens    $synthTokens `
        -Timeout      ($llmTimeout * 3)

    if ($synthResult -notmatch '^#') {
        $hIdx = $synthResult.IndexOf("`n#")
        $synthResult = if ($hIdx -ge 0) { $synthResult.Substring($hIdx + 1) } else { "# Test Gap Report`n`n$synthResult" }
    }

    $header = @"
<!--
  Generated by testgap_local.ps1
  Date:     $(Get-Date -Format 'yyyy-MM-dd HH:mm')
  Codebase: $codebaseDesc
  Source:   $SrcDir ($($srcFiles.Count) files)   Test: $TestDir
  LLM:      $llmModel
  Per-file gap reports: test_gaps/<src_path>.gap.md
-->

"@

    ($header + $synthResult) | Set-Content $reportFile -Encoding UTF8

    $elapsed2 = [math]::Round(([datetime]::Now - $startTime2).TotalSeconds)
    Write-Host ("Synthesis complete ({0}s)" -f $elapsed2) -ForegroundColor Green
}
catch {
    $errMsg = "$(Get-Date -Format u) | SYNTH FAIL | $($_.Exception.Message)"
    [System.IO.File]::AppendAllText($errorLog, "$errMsg`n")
    Write-Host "Synthesis failed: $($_.Exception.Message)" -ForegroundColor Red

    # Fallback: concatenate per-file analyses
    $fallback  = "# Test Gap Report (synthesis failed -- raw analyses below)`n`n"
    $fallback += "Error: $($_.Exception.Message)`n`n---`n`n"
    $fallback += $allAnalyses
    $fallback | Set-Content $reportFile -Encoding UTF8
    Write-Host "Fallback: raw analyses written to: $reportFile" -ForegroundColor Yellow
    exit 1
}

# ── Done ─────────────────────────────────────────────────────

Write-Host ''
Write-Host '============================================' -ForegroundColor Green
Write-Host '  Test gap report written'                   -ForegroundColor Green
Write-Host '============================================' -ForegroundColor Green
Write-Host "Report:       $reportFile"    -ForegroundColor Cyan
Write-Host "Per-file:     $gapDir"        -ForegroundColor Cyan
Write-Host ''
Write-Host 'Load in Claude Code:'
Write-Host '  Read test_gaps/GAP_REPORT.md'
