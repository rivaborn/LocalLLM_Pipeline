# ============================================================
# archgen_local.ps1 -- Per-File Architecture Doc Generator (Local LLM)
#
# Generates one .md doc per source file using a local Ollama LLM.
# Synchronous single-threaded processing (GPU handles one request at a time).
#
# Requires: llm_common.ps1 in the same directory.
#
# Usage:
#   .\archgen_local.ps1 [-TargetDir <path>] [-Preset <n>] [-Clean] [-Jobs <n>]
#
# Examples:
#   .\archgen_local.ps1 -Preset generals
#   .\archgen_local.ps1 -TargetDir Generals\Code\GameEngine -Preset generals
#   .\archgen_local.ps1 -Clean
# ============================================================

[CmdletBinding()]
param(
    [string]$TargetDir    = ".",
    [string]$Preset       = "",
    [switch]$Clean,
    [switch]$NoHeaders,
    [string]$EnvFile      = "",
    [switch]$Test
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($EnvFile -eq "") { $EnvFile = Join-Path $PSScriptRoot '..\Common\.env' }

# ── Load shared module ───────────────────────────────────────

. (Join-Path $PSScriptRoot '..\Common\llm_common.ps1')

# ── Load config ──────────────────────────────────────────────

$script:cfg = Read-EnvFile $EnvFile

# Promote Analysis-specific context window into LLM_NUM_CTX so Invoke-LocalLLM's
# auto-read picks it up without any callsite changes.
if ($script:cfg.ContainsKey('LLM_ANALYSIS_NUM_CTX') -and $script:cfg['LLM_ANALYSIS_NUM_CTX'] -ne '') {
    $script:cfg['LLM_NUM_CTX'] = $script:cfg['LLM_ANALYSIS_NUM_CTX']
}

$presetName   = if ($Preset -ne '') { $Preset } else { Cfg 'PRESET' '' }
$presetData   = Get-Preset $presetName
$includeRx    = Cfg 'INCLUDE_EXT_REGEX'   $presetData.Include
$excludeRx    = Cfg 'EXCLUDE_DIRS_REGEX'  $presetData.Exclude
$extraExclude = Cfg 'EXTRA_EXCLUDE_REGEX' ''
$codebaseDesc = Cfg 'CODEBASE_DESC'       $presetData.Desc
$defaultFence = Cfg 'DEFAULT_FENCE'       $presetData.Fence
$maxFileLines = [int](Cfg 'MAX_FILE_LINES'    '800')
$skipTrivial  = Cfg 'SKIP_TRIVIAL'            '1'
$minTrivialLines = [int](Cfg 'MIN_TRIVIAL_LINES' '20')

# LLM settings
$llmEndpoint    = Get-LLMEndpoint
$llmModel       = Cfg 'LLM_MODEL'       'qwen2.5-coder:14b'
$llmTemperature = [double](Cfg 'LLM_TEMPERATURE' '0.1')
$llmMaxTokens   = [int](Cfg 'LLM_MAX_TOKENS'    '800')
$llmTimeout     = [int](Cfg 'LLM_TIMEOUT'        '120')

# ── Paths ────────────────────────────────────────────────────

$repoRoot = (Get-Location).Path
try {
    $g = & git rev-parse --show-toplevel 2>$null
    if ($LASTEXITCODE -eq 0 -and $g) { $repoRoot = $g.Trim() }
} catch {}

$archDir  = Join-Path $repoRoot 'architecture'
$stateDir = Join-Path $archDir  '.archgen_state'
New-Item -ItemType Directory -Force -Path $archDir  | Out-Null
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null

$serenaContextDir = Join-Path $PSScriptRoot '.serena_context'
$hasSerenaContext  = Test-Path $serenaContextDir

# Prompt file
$promptFile = Join-Path $PSScriptRoot 'archgen_local_prompt.txt'
if (-not (Test-Path $promptFile)) {
    Write-Host "Missing prompt file: $promptFile" -ForegroundColor Red
    exit 2
}
$promptSchema = Get-Content $promptFile -Raw

# System prompt (short, for local LLM)
$systemPrompt = "You are analyzing source files from a $codebaseDesc. Follow the output schema exactly. Be concise."

$hashDbPath = Join-Path $stateDir 'hashes.tsv'
$errorLog   = Join-Path $stateDir 'last_error.log'

# ── Clean ────────────────────────────────────────────────────

if ($Clean) {
    Write-Host "CLEAN: removing docs and state (preserving .serena_context) ..." -ForegroundColor Cyan
    $preserve = @('.serena_context', '.dir_context', '.dir_headers')
    Get-ChildItem -Path $archDir -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -notin $preserve
    } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $archDir  | Out-Null
    New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
}

'' | Set-Content $errorLog -Encoding UTF8
if (-not (Test-Path $hashDbPath)) { '' | Set-Content $hashDbPath -Encoding UTF8 }

# ── Hash DB ──────────────────────────────────────────────────

$oldSha = @{}
Get-Content $hashDbPath | ForEach-Object {
    $parts = $_ -split "`t", 2
    if ($parts.Count -eq 2 -and $parts[1] -ne '') { $oldSha[$parts[1]] = $parts[0] }
}

# ── Collect files ────────────────────────────────────────────

$scanRoot = if ($TargetDir -eq '.') { $repoRoot } else { Join-Path $repoRoot $TargetDir }
if (-not (Test-Path $scanRoot)) { Write-Host "Target directory not found: $scanRoot" -ForegroundColor Red; exit 1 }

$allFiles = Get-ChildItem -Path $scanRoot -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object {
        $rel = $_.FullName.Substring($repoRoot.Length).TrimStart('\', '/') -replace '\\', '/'
        if ($rel -match '^architecture/' -or $rel -match '/architecture/') { return $false }
        if ($_.Name -match '\.ignore$') { return $false }
        if ($rel -match $excludeRx) { return $false }
        if ($extraExclude -ne '' -and $rel -match $extraExclude) { return $false }
        if ($rel -match $includeRx) { return $true }
        return $false
    } | Sort-Object FullName

$files = $allFiles | ForEach-Object {
    $_.FullName.Substring($repoRoot.Length).TrimStart('\', '/') -replace '\\', '/'
}

$total = @($files).Count
if ($total -eq 0) { Write-Host "No matching source files found under '$scanRoot'" -ForegroundColor Red; exit 1 }

# ── Build queue (skip unchanged + trivial) ───────────────────

$queue         = [System.Collections.Generic.List[string]]::new()
$skipUnchanged = 0
$skipTrivialN  = 0

foreach ($rel in $files) {
    $src = Join-Path $repoRoot ($rel -replace '/', '\')
    $out = Join-Path $archDir  (($rel -replace '/', '\') + '.md')
    $sha = Get-SHA1 $src

    # Skip unchanged files
    if ($oldSha.ContainsKey($rel) -and $oldSha[$rel] -eq $sha -and (Test-Path $out)) {
        $skipUnchanged++
        continue
    }

    # Skip trivial/generated files
    if ($skipTrivial -eq '1' -and (Test-TrivialFile $rel $src $minTrivialLines)) {
        $outDir = Split-Path $out -Parent
        New-Item -ItemType Directory -Force -Path $outDir | Out-Null
        Write-TrivialStub $rel $out
        [System.IO.File]::AppendAllText($hashDbPath, "$sha`t$rel`n")
        $skipTrivialN++
        continue
    }

    $queue.Add($rel)
}

$toDo = $queue.Count

# ── Banner ───────────────────────────────────────────────────

Write-Host '============================================' -ForegroundColor Yellow
Write-Host '  archgen_local.ps1 -- Local LLM Doc Gen'    -ForegroundColor Yellow
Write-Host '============================================' -ForegroundColor Yellow
Write-Host "Repo root:       $repoRoot"
Write-Host "Codebase:        $codebaseDesc"
if ($presetName) { Write-Host "Preset:          $presetName" }
Write-Host "Target:          $TargetDir"
Write-Host "LLM:             $llmModel @ $llmEndpoint"
Write-Host "Max file lines:  $maxFileLines"
Write-Host "Max output tok:  $llmMaxTokens"
$skipDetail = "unchanged=$skipUnchanged"
if ($skipTrivialN -gt 0) { $skipDetail += "  trivial=$skipTrivialN" }
Write-Host "Files:           $total total  |  $skipDetail  |  process: $toDo"
Write-Host "Prompt:          $promptFile"
$lspStatus = if ($hasSerenaContext) { "YES (compressed symbols)" } else { "NO" }
Write-Host "Serena context:  $lspStatus"
Write-Host ''
Write-Host 'Press Ctrl+Q to cancel (checked between files).' -ForegroundColor DarkGray
Write-Host ''

if ($toDo -eq 0) {
    Write-Host 'Nothing to do. All docs are up to date.' -ForegroundColor Green
    exit 0
}

# ── Process files ────────────────────────────────────────────

$startTime = [datetime]::Now
$done      = 0
$failed    = 0

foreach ($rel in $queue) {
    Test-CancelKey
    $src     = Join-Path $repoRoot ($rel -replace '/', '\')
    $outPath = Join-Path $archDir  (($rel -replace '/', '\') + '.md')
    $outDir  = Split-Path $outPath -Parent
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null

    $fence    = Get-FenceLang $rel $defaultFence
    $srcLines = @(Get-Content $src -ErrorAction SilentlyContinue)
    if (-not $srcLines) { $srcLines = @() }

    # Truncate source
    $sourceContent = Truncate-Source $srcLines $maxFileLines

    # Load compressed LSP context (symbols only)
    $lspSection = ''
    if ($hasSerenaContext) {
        $lsp = Load-CompressedLSP $serenaContextDir $rel
        if ($lsp -ne '') {
            $lspSection = "`nLSP SYMBOL CONTEXT:`n$lsp`n"
        }
    }

    # Adaptive output budget
    $budget = Get-OutputBudget $srcLines.Count

    # Build user prompt
    $userPrompt = @"
$promptSchema

FILE PATH: $rel
$lspSection
FILE CONTENT ($($srcLines.Count) lines):
``````$fence
$sourceContent
``````

OUTPUT BUDGET: ~$budget tokens max.
"@

    try {
        $resp = Invoke-LocalLLM `
            -SystemPrompt $systemPrompt `
            -UserPrompt   $userPrompt `
            -Endpoint     $llmEndpoint `
            -Model        $llmModel `
            -Temperature  $llmTemperature `
            -MaxTokens    $budget `
            -Timeout      $llmTimeout

        # Post-process: ensure the response starts with a heading
        if ($resp -notmatch '^#') {
            # Strip any preamble chatter before the first heading
            $headingIdx = $resp.IndexOf("`n#")
            if ($headingIdx -ge 0) {
                $resp = $resp.Substring($headingIdx + 1)
            } else {
                # No heading found -- prepend one
                $resp = "# $rel`n`n$resp"
            }
        }

        $resp | Set-Content -Path $outPath -Encoding UTF8

        # Record hash
        $sha = Get-SHA1 $src
        [System.IO.File]::AppendAllText($hashDbPath, "$sha`t$rel`n")
        $done++
    }
    catch {
        $failed++
        $errEntry = "$(Get-Date -Format u) | FAIL | $rel | $($_.Exception.Message)`n"
        [System.IO.File]::AppendAllText($errorLog, $errEntry)
        Write-Host "`n  [FAIL] $rel -- $($_.Exception.Message)" -ForegroundColor Red
    }

    Show-SimpleProgress $done $toDo $startTime
}

Write-Host ''
Write-Host ''

# ── Deduplicate hash DB ──────────────────────────────────────

if (Test-Path $hashDbPath) {
    $seen = @{}
    $keep = [System.Collections.Generic.List[string]]::new()
    $raw  = @(Get-Content $hashDbPath | Where-Object { $_.Trim() -ne '' })
    [array]::Reverse($raw)
    foreach ($line in $raw) {
        $parts = $line -split "`t", 2
        if ($parts.Count -eq 2 -and -not $seen.ContainsKey($parts[1])) {
            $seen[$parts[1]] = $true
            $keep.Add($line)
        }
    }
    ($keep | Sort-Object) -join "`n" | Set-Content $hashDbPath -Encoding UTF8
}

# ── Result ───────────────────────────────────────────────────

if ($failed -gt 0) {
    Write-Host "Completed with $failed failures. See: $errorLog" -ForegroundColor Yellow
} else {
    Write-Host "Done. $done files processed. Docs in: $archDir" -ForegroundColor Green
}
