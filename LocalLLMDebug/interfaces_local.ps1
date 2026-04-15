# ============================================================
# interfaces_local.ps1 -- Interface Contract Summary (Local LLM)
#
# Two-pass pipeline:
#   Pass 1 (per-file): extract the precise contract of every public
#           class and function -- preconditions, postconditions,
#           raises, silent failure modes, thread safety, and resource
#           lifecycle.
#   Pass 2 (synthesis): combine all per-module contracts into a single
#           reference document with a quick-reference table,
#           cross-module obligations, and a consolidated silent-failure
#           inventory.
#
# Output:
#   architecture/INTERFACES.md              -- combined reference
#   architecture/interfaces/<rel>.iface.md  -- per-file contracts
#
# Cache: pass-1 results are cached by SHA1 in
#   architecture/.interfaces_state/cache/
# Re-running after editing one file only re-extracts that file.
#
# Requires: llm_common.ps1, interfaces_prompt.txt,
#           interfaces_synth_prompt.txt
#
# Usage:
#   .\LocalLLMDebug\interfaces_local.ps1 [-TargetDir <path>] [-Clean]
#
# Examples:
#   .\LocalLLMDebug\interfaces_local.ps1
#   .\LocalLLMDebug\interfaces_local.ps1 -TargetDir src/nmon
#   .\LocalLLMDebug\interfaces_local.ps1 -Clean
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
$maxFileLines = [int](Cfg 'MAX_FILE_LINES'              '800')

$llmEndpoint    = Get-LLMEndpoint
# Prefer LLM_MODEL_HIGH_CTX if set (synth pass reads ~10k tokens of per-file
# contracts); otherwise fall back to the standard LLM_MODEL.
$llmModel       = Cfg 'LLM_MODEL_HIGH_CTX' (Cfg 'LLM_MODEL' 'qwen2.5-coder:14b')
$llmTemperature = [double](Cfg 'LLM_TEMPERATURE'    '0.1')
$llmTimeout     = [int](Cfg 'LLM_TIMEOUT'           '120')
$extractTokens  = [int](Cfg 'INTERFACES_EXTRACT_TOKENS' '700')
$synthTokens    = [int](Cfg 'INTERFACES_SYNTH_TOKENS'   '2000')

# ── Paths ────────────────────────────────────────────────────

$repoRoot = (Get-Location).Path
try {
    $g = & git rev-parse --show-toplevel 2>$null
    if ($LASTEXITCODE -eq 0 -and $g) { $repoRoot = $g.Trim() }
} catch {}

$archDir    = Join-Path $repoRoot 'architecture'
$ifaceDir   = Join-Path $archDir  'interfaces'
$stateDir   = Join-Path $archDir  '.interfaces_state'
$cacheDir   = Join-Path $stateDir 'cache'

foreach ($d in @($archDir, $ifaceDir, $stateDir, $cacheDir)) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}

$outputFile = Join-Path $archDir 'INTERFACES.md'
$errorLog   = Join-Path $stateDir 'last_error.log'

$extractPromptFile = Join-Path $PSScriptRoot 'interfaces_prompt.txt'
$synthPromptFile   = Join-Path $PSScriptRoot 'interfaces_synth_prompt.txt'

foreach ($f in @($extractPromptFile, $synthPromptFile)) {
    if (-not (Test-Path $f)) {
        Write-Host "Missing prompt file: $f" -ForegroundColor Red; exit 2
    }
}

$extractPromptSchema = Get-Content $extractPromptFile -Raw
$synthPromptSchema   = Get-Content $synthPromptFile   -Raw

$extractSysPrompt = "You are a senior Python engineer documenting interface contracts for a $codebaseDesc. Be precise: use exact type names, exact exception names, and name specific conditions."
$synthSysPrompt   = "You are writing a combined interface contract reference for a $codebaseDesc. Be specific and use exact names from the analyses provided."

$tb = '```'

# ── Clean ────────────────────────────────────────────────────

if ($Clean) {
    Write-Host "CLEAN: removing interface docs and cache..." -ForegroundColor Cyan
    Remove-Item -Path $ifaceDir   -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -Path $stateDir   -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -Path $outputFile -Force          -ErrorAction SilentlyContinue
    foreach ($d in @($ifaceDir, $stateDir, $cacheDir)) {
        New-Item -ItemType Directory -Force -Path $d | Out-Null
    }
}

'' | Set-Content $errorLog -Encoding UTF8

# ── Collect files ────────────────────────────────────────────

$scanRoot = if ($TargetDir -eq '.') { $repoRoot } else { Join-Path $repoRoot $TargetDir }
if (-not (Test-Path $scanRoot)) {
    Write-Host "Target directory not found: $scanRoot" -ForegroundColor Red; exit 1
}

$allFiles = Get-ChildItem -Path $scanRoot -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object {
        $rel = $_.FullName.Substring($repoRoot.Length).TrimStart('\','/') -replace '\\','/'
        if ($rel -match '^(architecture|bug_reports|bug_fixes|test_gaps)/' -or
            $rel -match '/(architecture|bug_reports|bug_fixes|test_gaps)/') { return $false }
        if ($_.Name -match '\.ignore$') { return $false }
        if ($rel -match $excludeRx) { return $false }
        if ($extraExclude -ne '' -and $rel -match $extraExclude) { return $false }
        if ($rel -match $includeRx) { return $true }
        return $false
    } | Sort-Object FullName

$files = @($allFiles | ForEach-Object {
    $_.FullName.Substring($repoRoot.Length).TrimStart('\','/') -replace '\\','/'
})

$total = $files.Count
if ($total -eq 0) {
    Write-Host "No matching source files found under '$scanRoot'" -ForegroundColor Red; exit 1
}

# ── Banner ───────────────────────────────────────────────────

Write-Host '============================================'          -ForegroundColor Cyan
Write-Host '  interfaces_local.ps1 -- Contract Summary'           -ForegroundColor Cyan
Write-Host '============================================'          -ForegroundColor Cyan
Write-Host "Repo root:       $repoRoot"
Write-Host "Codebase:        $codebaseDesc"
Write-Host "Target:          $TargetDir"
Write-Host "LLM:             $llmModel @ $llmEndpoint"
Write-Host "Files:           $total"
Write-Host "Extract tokens:  $extractTokens  |  Synth tokens: $synthTokens"
Write-Host "Output:          $outputFile"
Write-Host ''
Write-Host 'Press Ctrl+Q to cancel (checked between files).' -ForegroundColor DarkGray
Write-Host ''

# ── Optional: locate serena LSP context directory ───────────
# If SERENA_CONTEXT_DIR is configured and exists, each per-file extract
# prompt gets prefixed with the file's Symbol Overview section (via
# Load-CompressedLSP) so the model reconstructs contracts from precise LSP
# types instead of from source-text inference alone. Gracefully skips when
# serena_extract hasn't been run.

$serenaCtxDir = Get-SerenaContextDir $repoRoot
if ($serenaCtxDir) {
    Write-Host ("  [integration] serena context dir: {0}" -f $serenaCtxDir) -ForegroundColor DarkCyan
}

# ── Pass 1: Per-file contract extraction ─────────────────────

Write-Host "Pass 1: extracting contracts..." -ForegroundColor Yellow
Write-Host ''

$extractions   = [System.Collections.Generic.List[string]]::new()
$extractFailed = 0
$startTime     = [datetime]::Now

for ($i = 0; $i -lt $total; $i++) {
    Test-CancelKey
    $rel = $files[$i]
    $src = Join-Path $repoRoot ($rel -replace '/', '\')

    $sha       = Get-SHA1 $src
    $cachePath = Join-Path $cacheDir ($sha + '.txt')

    Write-Host ("  [{0}/{1}] {2}" -f ($i + 1), $total, $rel) -NoNewline

    # Serve from cache
    if (Test-Path $cachePath) {
        $cached = Get-Content $cachePath -Raw -Encoding UTF8
        if ($cached -and $cached.Trim() -ne '') {
            $extractions.Add($cached.Trim())

            # Also write/refresh the per-file output
            $ifaceOut    = Join-Path $ifaceDir (($rel -replace '/', '\') + '.iface.md')
            $ifaceOutDir = Split-Path $ifaceOut -Parent
            New-Item -ItemType Directory -Force -Path $ifaceOutDir | Out-Null
            if (-not (Test-Path $ifaceOut)) {
                $cached | Set-Content $ifaceOut -Encoding UTF8
            }

            Write-Host ' [cached]' -ForegroundColor DarkGray
            continue
        }
    }

    $srcLines = @(Get-Content $src -ErrorAction SilentlyContinue)
    if (-not $srcLines) { $srcLines = @() }
    $fence     = Get-FenceLang $rel $defaultFence
    $truncated = Truncate-Source $srcLines $maxFileLines

    $lspSection = ''
    if ($serenaCtxDir) {
        $serenaCtx = Load-CompressedLSP $serenaCtxDir $rel
        if ($serenaCtx) {
            $lspSection = @"
LSP SYMBOL CONTEXT (from serena / clangd, authoritative for types and signatures):

$serenaCtx

Use the LSP symbols above to ground the extracted contract in exact type names rather than inferring from source text. When LSP and source disagree, trust LSP.

"@
        }
    }

    $userPrompt = @"
$extractPromptSchema

${lspSection}FILE PATH: $rel

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

        # Normalise: ensure starts with a heading
        if ($result -notmatch '^#') {
            $hIdx  = $result.IndexOf("`n#")
            $result = if ($hIdx -ge 0) { $result.Substring($hIdx + 1) } else { "# Module: ``$rel```n`n$result" }
        }

        # Write cache
        $result | Set-Content $cachePath -Encoding UTF8

        # Write per-file output
        $ifaceOut    = Join-Path $ifaceDir (($rel -replace '/', '\') + '.iface.md')
        $ifaceOutDir = Split-Path $ifaceOut -Parent
        New-Item -ItemType Directory -Force -Path $ifaceOutDir | Out-Null
        $result | Set-Content $ifaceOut -Encoding UTF8

        $extractions.Add($result.Trim())
        Write-Host ' done' -ForegroundColor Green
    }
    catch {
        $extractFailed++
        $errMsg = "$(Get-Date -Format u) | FAIL | $rel | $($_.Exception.Message)"
        [System.IO.File]::AppendAllText($errorLog, "$errMsg`n")
        Write-Host " [FAIL] $($_.Exception.Message)" -ForegroundColor Red
    }
}

Write-Host ''
$elapsed1 = [math]::Round(([datetime]::Now - $startTime).TotalSeconds)
Write-Host ("Pass 1 complete: {0}/{1} contracts extracted  ({2}s)" -f $extractions.Count, $total, $elapsed1) -ForegroundColor Yellow
if ($extractFailed -gt 0) {
    Write-Host ("  WARNING: {0} failure(s) -- synthesis will be incomplete." -f $extractFailed) -ForegroundColor Yellow
}
Write-Host ''

if ($extractions.Count -eq 0) {
    Write-Host "No extractions succeeded -- cannot synthesize." -ForegroundColor Red; exit 1
}

# ── Pass 2: Synthesis ─────────────────────────────────────────

Write-Host "Pass 2: synthesising combined contract reference..." -ForegroundColor Yellow

$allExtractions = $extractions -join "`n`n---`n`n"

$synthPrompt = @"
$synthPromptSchema

CODEBASE: $codebaseDesc

BEGIN PER-MODULE CONTRACTS
$allExtractions
END PER-MODULE CONTRACTS
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
        $hIdx       = $synthResult.IndexOf("`n#")
        $synthResult = if ($hIdx -ge 0) { $synthResult.Substring($hIdx + 1) } else { "# Interface Contracts`n`n$synthResult" }
    }

    $header = @"
<!--
  Generated by interfaces_local.ps1
  Date:      $(Get-Date -Format 'yyyy-MM-dd HH:mm')
  Codebase:  $codebaseDesc
  Files:     $($extractions.Count) / $total extracted
  LLM:       $llmModel
  Per-file:  architecture/interfaces/<path>.iface.md
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

    # Fallback: concatenate per-file contracts
    $fallback  = "# Interface Contracts (synthesis failed -- per-file contracts below)`n`n"
    $fallback += "Error: $($_.Exception.Message)`n`n---`n`n"
    $fallback += $allExtractions
    $fallback | Set-Content $outputFile -Encoding UTF8
    Write-Host "Fallback: raw contracts written to: $outputFile" -ForegroundColor Yellow
    exit 1
}

# ── Done ─────────────────────────────────────────────────────

Write-Host ''
Write-Host '============================================' -ForegroundColor Green
Write-Host '  Interface contracts written'               -ForegroundColor Green
Write-Host '============================================' -ForegroundColor Green
Write-Host "Combined:  $outputFile"                 -ForegroundColor Cyan
Write-Host "Per-file:  $ifaceDir"                   -ForegroundColor Cyan
Write-Host ''
Write-Host 'Load in Claude Code:'
Write-Host '  Read architecture/INTERFACES.md'
