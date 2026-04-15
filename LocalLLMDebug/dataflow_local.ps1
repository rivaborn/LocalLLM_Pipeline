# ============================================================
# dataflow_local.ps1 -- Data Flow Trace Generator (Local LLM)
#
# Two-pass pipeline:
#   Pass 1 (per-file): extract each file's pipeline interface --
#           what types it defines, what it produces, what it consumes,
#           threading notes, and error surface.
#   Pass 2 (synthesis): combine all extractions into a single
#           debugging-focused data flow trace document.
#
# Output: architecture/DATA_FLOW.md
#
# Requires: llm_common.ps1, dataflow_extract_prompt.txt, dataflow_synth_prompt.txt
# Prerequisites: none (reads source directly, not architecture/ docs)
#
# Usage:
#   .\LocalLLMDebug\dataflow_local.ps1 [-TargetDir <path>] [-Clean]
#
# Examples:
#   .\LocalLLMDebug\dataflow_local.ps1
#   .\LocalLLMDebug\dataflow_local.ps1 -TargetDir src/nmon
# ============================================================

[CmdletBinding()]
param(
    [string]$TargetDir = ".",
    [switch]$Clean,
    [string]$EnvFile   = ""
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
$includeRx    = Cfg 'INCLUDE_EXT_REGEX'   $presetData.Include
$excludeRx    = Cfg 'EXCLUDE_DIRS_REGEX'  $presetData.Exclude
$extraExclude = Cfg 'EXTRA_EXCLUDE_REGEX' ''
$codebaseDesc = Cfg 'CODEBASE_DESC'       $presetData.Desc
$defaultFence = Cfg 'DEFAULT_FENCE'       $presetData.Fence
$maxFileLines = [int](Cfg 'MAX_FILE_LINES' '800')

# LLM settings
$llmEndpoint    = Get-LLMEndpoint
$llmModel       = Cfg 'LLM_MODEL'       'qwen2.5-coder:14b'
$llmTemperature = [double](Cfg 'LLM_TEMPERATURE' '0.1')
$llmTimeout     = [int](Cfg 'LLM_TIMEOUT'        '120')
$extractTokens  = [int](Cfg 'DATAFLOW_EXTRACT_TOKENS' '400')
$synthTokens    = [int](Cfg 'DATAFLOW_SYNTH_TOKENS'   '1800')

# ── Paths ────────────────────────────────────────────────────

$repoRoot = (Get-Location).Path
try {
    $g = & git rev-parse --show-toplevel 2>$null
    if ($LASTEXITCODE -eq 0 -and $g) { $repoRoot = $g.Trim() }
} catch {}

$archDir  = Join-Path $repoRoot 'architecture'
$stateDir = Join-Path $archDir  '.dataflow_state'
New-Item -ItemType Directory -Force -Path $archDir  | Out-Null
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null

$outputFile = Join-Path $archDir 'DATA_FLOW.md'
$errorLog   = Join-Path $stateDir 'last_error.log'
# Intermediate extractions saved here so synthesis can be re-run without re-extracting
$extractDir = Join-Path $stateDir 'extractions'
New-Item -ItemType Directory -Force -Path $extractDir | Out-Null

# Prompt files
$extractPromptFile = Join-Path $PSScriptRoot 'dataflow_extract_prompt.txt'
$synthPromptFile   = Join-Path $PSScriptRoot 'dataflow_synth_prompt.txt'

foreach ($f in @($extractPromptFile, $synthPromptFile)) {
    if (-not (Test-Path $f)) {
        Write-Host "Missing prompt file: $f" -ForegroundColor Red
        exit 2
    }
}

$extractPromptSchema = Get-Content $extractPromptFile -Raw
$synthPromptSchema   = Get-Content $synthPromptFile   -Raw

$extractSysPrompt = "You are analysing source files from a $codebaseDesc. Extract only the pipeline interface -- types, inputs, outputs, threading. Be concise and factual."
$synthSysPrompt   = "You are writing a debugging-focused data flow document for a $codebaseDesc. Be specific to the actual types and methods in the summaries provided."

$tb = '```'

# ── Clean ────────────────────────────────────────────────────

if ($Clean) {
    Write-Host "CLEAN: removing extractions and output..." -ForegroundColor Cyan
    Remove-Item -Path $stateDir -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -Path $outputFile -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $stateDir  | Out-Null
    New-Item -ItemType Directory -Force -Path $extractDir | Out-Null
}

'' | Set-Content $errorLog -Encoding UTF8

# ── Collect files ────────────────────────────────────────────

$scanRoot = if ($TargetDir -eq '.') { $repoRoot } else { Join-Path $repoRoot $TargetDir }
if (-not (Test-Path $scanRoot)) {
    Write-Host "Target directory not found: $scanRoot" -ForegroundColor Red
    exit 1
}

$allFiles = Get-ChildItem -Path $scanRoot -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object {
        $rel = $_.FullName.Substring($repoRoot.Length).TrimStart('\', '/') -replace '\\', '/'
        if ($rel -match '^(architecture|bug_reports|bug_fixes)/' -or
            $rel -match '/(architecture|bug_reports|bug_fixes)/') { return $false }
        if ($_.Name -match '\.ignore$') { return $false }
        if ($rel -match $excludeRx) { return $false }
        if ($extraExclude -ne '' -and $rel -match $extraExclude) { return $false }
        if ($rel -match $includeRx) { return $true }
        return $false
    } | Sort-Object FullName

$files = @($allFiles | ForEach-Object {
    $_.FullName.Substring($repoRoot.Length).TrimStart('\', '/') -replace '\\', '/'
})

$total = $files.Count
if ($total -eq 0) {
    Write-Host "No matching source files found under '$scanRoot'" -ForegroundColor Red
    exit 1
}

# ── Banner ───────────────────────────────────────────────────

Write-Host '============================================' -ForegroundColor Cyan
Write-Host '  dataflow_local.ps1 -- Data Flow Trace'     -ForegroundColor Cyan
Write-Host '============================================' -ForegroundColor Cyan
Write-Host "Repo root:        $repoRoot"
Write-Host "Codebase:         $codebaseDesc"
Write-Host "Target:           $TargetDir"
Write-Host "LLM:              $llmModel @ $llmEndpoint"
Write-Host "Files to extract: $total"
Write-Host "Extract tokens:   $extractTokens  |  Synth tokens: $synthTokens"
Write-Host "Output:           $outputFile"
Write-Host ''
Write-Host 'Press Ctrl+Q to cancel (checked between files).' -ForegroundColor DarkGray
Write-Host ''

# ── Pass 1: Per-file interface extraction ─────────────────────
#
# For each source file, ask the LLM to describe only its pipeline
# interface (types, inputs, outputs, threading, errors).
# Results are cached in .dataflow_state/extractions/ by SHA1 so a
# re-run after editing one file only re-extracts changed files.

Write-Host "Pass 1: extracting file interfaces..." -ForegroundColor Yellow
Write-Host ''

$extractions   = [System.Collections.Generic.List[string]]::new()
$extractFailed = 0
$startTime     = [datetime]::Now

for ($i = 0; $i -lt $total; $i++) {
    Test-CancelKey
    $rel = $files[$i]
    $src = Join-Path $repoRoot ($rel -replace '/', '\')

    $sha       = Get-SHA1 $src
    $cachePath = Join-Path $extractDir ($sha + '.txt')

    Write-Host ("  [{0}/{1}] {2}" -f ($i + 1), $total, $rel) -NoNewline

    # Use cached extraction if source hasn't changed
    if (Test-Path $cachePath) {
        $cached = Get-Content $cachePath -Raw -Encoding UTF8
        if ($cached -and $cached.Trim() -ne '') {
            $extractions.Add($cached.Trim())
            Write-Host " [cached]" -ForegroundColor DarkGray
            continue
        }
    }

    $srcLines = @(Get-Content $src -ErrorAction SilentlyContinue)
    if (-not $srcLines) { $srcLines = @() }
    $fence     = Get-FenceLang $rel $defaultFence
    $truncated = Truncate-Source $srcLines $maxFileLines

    $userPrompt = @"
$extractPromptSchema

FILE PATH: $rel

FILE CONTENT ($($srcLines.Count) lines):
${tb}$fence
$truncated
${tb}
"@

    try {
        $result = Invoke-LocalLLM `
            -SystemPrompt $extractSysPrompt `
            -UserPrompt   $userPrompt `
            -Endpoint     $llmEndpoint `
            -Model        $llmModel `
            -Temperature  $llmTemperature `
            -MaxTokens    $extractTokens `
            -Timeout      $llmTimeout

        # Ensure starts with a heading
        if ($result -notmatch '^#') {
            $hIdx = $result.IndexOf("`n#")
            $result = if ($hIdx -ge 0) { $result.Substring($hIdx + 1) } else { "# $rel`n`n$result" }
        }

        # Cache to disk
        $result | Set-Content $cachePath -Encoding UTF8

        $extractions.Add($result.Trim())
        Write-Host " done" -ForegroundColor Green
    }
    catch {
        $extractFailed++
        $errMsg = "$(Get-Date -Format u) | EXTRACT FAIL | $rel | $($_.Exception.Message)"
        [System.IO.File]::AppendAllText($errorLog, "$errMsg`n")
        Write-Host " [FAIL] $($_.Exception.Message)" -ForegroundColor Red
    }
}

Write-Host ''

$extracted = $extractions.Count
if ($extracted -eq 0) {
    Write-Host "No extractions succeeded -- cannot synthesize." -ForegroundColor Red
    exit 1
}

$elapsed1 = [math]::Round(([datetime]::Now - $startTime).TotalSeconds)
Write-Host ("Pass 1 complete: {0}/{1} files extracted  ({2}s)" -f $extracted, $total, $elapsed1) -ForegroundColor Yellow
if ($extractFailed -gt 0) {
    Write-Host ("  WARNING: {0} extraction(s) failed -- synthesis will be incomplete." -f $extractFailed) -ForegroundColor Yellow
}
Write-Host ''

# ── Pass 2: Synthesise data flow document ─────────────────────
#
# Feed all per-file extractions into a single synthesis call.
# The LLM produces the end-to-end flow trace with handoff points.

Write-Host "Pass 2: synthesising data flow document..." -ForegroundColor Yellow

$allExtractions = $extractions -join "`n`n---`n`n"

# ── Optional: inject architecture.md as subsystem scaffolding ─
# If Analysis has been run and produced architecture.md, feed it into the
# synth prompt as a subsystem-level scaffold. The LLM then produces a more
# coherent cross-module flow trace anchored to the known subsystem boundaries
# rather than re-inventing them from per-file extractions. Gracefully skips
# when Analysis hasn't been run.

$archOverview = ''
$archPath = Resolve-ArchFile 'architecture.md' $repoRoot
if ($archPath) {
    $archOverview = Get-Content $archPath -Raw
    Write-Host ("  [integration] architecture.md loaded: {0:N1} KB ({1})" -f ($archOverview.Length / 1KB), $archPath) -ForegroundColor DarkCyan
}

$archSection = ''
if ($archOverview) {
    $archSection = @"
SUBSYSTEM OVERVIEW (from LocalLLMAnalysis architecture.md -- anchor the data flow trace to these subsystem boundaries):

$archOverview

"@
}

$synthPrompt = @"
$synthPromptSchema

CODEBASE: $codebaseDesc

${archSection}BEGIN PER-FILE INTERFACE SUMMARIES
$allExtractions
END PER-FILE INTERFACE SUMMARIES
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
        -Timeout      ($llmTimeout * 3)   # synthesis takes longer

    # Ensure starts with a heading
    if ($synthResult -notmatch '^#') {
        $hIdx = $synthResult.IndexOf("`n#")
        $synthResult = if ($hIdx -ge 0) { $synthResult.Substring($hIdx + 1) } else { "# Data Flow`n`n$synthResult" }
    }

    # Prepend metadata header
    $header = @"
<!--
  Generated by dataflow_local.ps1
  Date:      $(Get-Date -Format 'yyyy-MM-dd HH:mm')
  Codebase:  $codebaseDesc
  Files:     $extracted / $total extracted
  LLM:       $llmModel
-->

"@

    ($header + $synthResult) | Set-Content $outputFile -Encoding UTF8

    $elapsed2 = [math]::Round(([datetime]::Now - $startTime2).TotalSeconds)
    Write-Host ("Synthesis complete ({0}s)" -f $elapsed2) -ForegroundColor Green
}
catch {
    $errMsg = "$(Get-Date -Format u) | SYNTH FAIL | $($_.Exception.Message)"
    [System.IO.File]::AppendAllText($errorLog, "$errMsg`n")
    Write-Host "Synthesis failed: $($_.Exception.Message)" -ForegroundColor Red

    # Fallback: write raw extractions as the output
    $fallback = "# Data Flow (extraction only -- synthesis failed)`n`n"
    $fallback += "Synthesis error: $($_.Exception.Message)`n`n---`n`n"
    $fallback += $allExtractions
    $fallback | Set-Content $outputFile -Encoding UTF8
    Write-Host "Fallback: raw extractions written to: $outputFile" -ForegroundColor Yellow
    exit 1
}

# ── Done ─────────────────────────────────────────────────────

Write-Host ''
Write-Host '============================================' -ForegroundColor Green
Write-Host '  Data flow trace written'                   -ForegroundColor Green
Write-Host '============================================' -ForegroundColor Green
Write-Host "Output: $outputFile" -ForegroundColor Cyan
Write-Host ''
Write-Host "Load in Claude Code:"
Write-Host "  Read architecture/DATA_FLOW.md"
