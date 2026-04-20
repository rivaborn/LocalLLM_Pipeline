# ============================================================
# archpass2_local.ps1 -- Context-Aware Second-Pass Analysis (Local LLM)
#
# Re-analyzes source files with architecture context injected.
# Synchronous single-threaded processing.
#
# Requires: llm_common.ps1 in the same directory.
# Prerequisites: archgen_local.ps1, archxref.ps1, arch_overview_local.ps1
#
# Usage:
#   .\archpass2_local.ps1 [-TargetDir <dir>] [-Clean] [-Top <n>] [-ScoreOnly]
#   .\archpass2_local.ps1 -Only "path/to/file1.cpp,path/to/file2.cpp"
# ============================================================

[CmdletBinding()]
param(
    [string]$TargetDir = ".",
    [switch]$Clean,
    [string]$Only      = "",
    [int]   $Top       = 0,
    [switch]$ScoreOnly,
    [string]$EnvFile   = ""
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

$presetName   = Cfg 'PRESET' ''
$presetData   = Get-Preset $presetName
$includeRx    = Cfg 'INCLUDE_EXT_REGEX'   $presetData.Include
$excludeRx    = Cfg 'EXCLUDE_DIRS_REGEX'  $presetData.Exclude
$extraExclude = Cfg 'EXTRA_EXCLUDE_REGEX' ''
$codebaseDesc = Cfg 'CODEBASE_DESC'       $presetData.Desc
$defaultFence = Cfg 'DEFAULT_FENCE'       $presetData.Fence

$llmEndpoint    = Get-LLMEndpoint
$llmModel       = Get-LLMModel -RoleKey 'LLM_MODEL'
$llmTemperature = [double](Cfg 'LLM_TEMPERATURE' '0.1')
$llmTimeout     = [int](Cfg 'LLM_TIMEOUT'        '120')

# ── Paths ────────────────────────────────────────────────────

$repoRoot = (Get-Location).Path
try {
    $g = & git rev-parse --show-toplevel 2>$null
    if ($LASTEXITCODE -eq 0 -and $g) { $repoRoot = $g.Trim() }
} catch {}

$archDir  = Join-Path $repoRoot 'architecture'
$stateDir = Join-Path $archDir  '.pass2_state'
New-Item -ItemType Directory -Force -Path $archDir  | Out-Null
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null

$hashDbPath = Join-Path $stateDir 'hashes.tsv'
$errorLog   = Join-Path $stateDir 'last_error.log'

# Prompt file
$promptFile = Join-Path $PSScriptRoot 'archpass2_local_prompt.txt'
$promptSchema = ''
if (Test-Path $promptFile) {
    $promptSchema = Get-Content $promptFile -Raw
}

$systemPrompt = "You are doing second-pass architectural analysis of a $codebaseDesc. Add cross-cutting insights. Be concise."

# Check prerequisites
$archOverview = Join-Path $archDir 'architecture.md'
$xrefIndex    = Join-Path $archDir 'xref_index.md'

if (-not (Test-Path $archOverview)) {
    Write-Host "Missing architecture.md -- run arch_overview_local.ps1 first." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $xrefIndex)) {
    Write-Host "Missing xref_index.md -- run archxref.ps1 first." -ForegroundColor Red
    exit 1
}

# Load global context (truncated for local LLM)
$archContext = Get-Content $archOverview -Raw -ErrorAction SilentlyContinue
if ($archContext.Length -gt 8000) {
    $archContext = $archContext.Substring(0, 8000) + "`n... [truncated] ..."
}

$xrefContext = Get-Content $xrefIndex -Raw -ErrorAction SilentlyContinue
if ($xrefContext.Length -gt 4000) {
    $xrefContext = $xrefContext.Substring(0, 4000) + "`n... [truncated] ..."
}

# ── Clean ────────────────────────────────────────────────────

if ($Clean) {
    Write-Host "CLEAN: removing Pass 2 output and state..." -ForegroundColor Cyan
    Get-ChildItem -Path $archDir -Recurse -Filter '*.pass2.md' -ErrorAction SilentlyContinue | Remove-Item -Force
    Remove-Item $stateDir -Recurse -Force -ErrorAction SilentlyContinue
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

# ── Scoring ──────────────────────────────────────────────────

function Get-Pass2FileScore($rel, $lineCount, $incomingCount, $hasSerena) {
    $score = ($incomingCount * 3) + ($lineCount / 100.0)
    if ($hasSerena) { $score *= 0.5 }
    return [math]::Round($score, 2)
}

# ── Collect files ────────────────────────────────────────────

$serenaContextDir = Join-Path $PSScriptRoot '.serena_context'
$hasSerenaContext  = Test-Path $serenaContextDir

if ($Only -ne '') {
    # Manual file list
    $files = @($Only -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
} else {
    $scanRoot = if ($TargetDir -eq '.') { $repoRoot } else { Join-Path $repoRoot $TargetDir }
    if (-not (Test-Path $scanRoot)) { Write-Host "Not found: $scanRoot" -ForegroundColor Red; exit 1 }

    $files = Get-ChildItem -Path $scanRoot -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object {
            $rel = $_.FullName.Substring($repoRoot.Length).TrimStart('\', '/') -replace '\\', '/'
            if ($rel -match '^architecture/') { return $false }
            if ($rel -match $excludeRx) { return $false }
            if ($extraExclude -ne '' -and $rel -match $extraExclude) { return $false }
            if ($rel -match $includeRx) { return $true }
            return $false
        } | ForEach-Object {
            $_.FullName.Substring($repoRoot.Length).TrimStart('\', '/') -replace '\\', '/'
        } | Sort-Object
}

# Filter to files that have Pass 1 docs
$candidates = @($files | Where-Object {
    $pass1 = Join-Path $archDir (($_ -replace '/','\') + '.md')
    Test-Path $pass1
})

# ── Score and rank ───────────────────────────────────────────

# Parse xref for incoming reference counts
$incomingCounts = @{}
$xrefLines = @(Get-Content $xrefIndex -ErrorAction SilentlyContinue)
foreach ($line in $xrefLines) {
    if ($line -match '^\|\s*`?([^|`]+)`?\s*\|\s*(\d+)\s*\|') {
        # Try to match function call count table rows
    }
    # Simple approach: count filename mentions
    foreach ($rel in $candidates) {
        $leaf = Split-Path $rel -Leaf
        if ($line -match [regex]::Escape($leaf)) {
            if (-not $incomingCounts.ContainsKey($rel)) { $incomingCounts[$rel] = 0 }
            $incomingCounts[$rel]++
        }
    }
}

$scored = @($candidates | ForEach-Object {
    $rel = $_
    $src = Join-Path $repoRoot ($rel -replace '/','\')
    $lc  = @(Get-Content $src -ErrorAction SilentlyContinue).Count
    $inc = if ($incomingCounts.ContainsKey($rel)) { $incomingCounts[$rel] } else { 0 }
    $hasSerena = $hasSerenaContext -and (Test-Path (Join-Path $serenaContextDir (($rel -replace '/','\') + '.serena_context.txt')))
    $score = Get-Pass2FileScore $rel $lc $inc $hasSerena
    [pscustomobject]@{ Rel = $rel; Score = $score; Lines = $lc; Incoming = $inc }
} | Sort-Object Score -Descending)

if ($ScoreOnly) {
    Write-Host "Top files by Pass 2 score:" -ForegroundColor Cyan
    $show = if ($Top -gt 0) { $scored | Select-Object -First $Top } else { $scored | Select-Object -First 50 }
    $show | ForEach-Object {
        Write-Host ("  {0,7:F2}  {1,5} lines  {2,3} refs  {3}" -f $_.Score, $_.Lines, $_.Incoming, $_.Rel)
    }
    exit 0
}

# Apply -Top filter
if ($Top -gt 0) {
    $scored = @($scored | Select-Object -First $Top)
}

# Build queue (skip unchanged)
$queue = [System.Collections.Generic.List[string]]::new()
$skipUnchanged = 0

foreach ($item in $scored) {
    $rel = $item.Rel
    $src = Join-Path $repoRoot ($rel -replace '/','\')
    $out = Join-Path $archDir  (($rel -replace '/','\') + '.pass2.md')
    $sha = Get-SHA1 $src

    if ($oldSha.ContainsKey($rel) -and $oldSha[$rel] -eq $sha -and (Test-Path $out)) {
        $skipUnchanged++
        continue
    }
    $queue.Add($rel)
}

$toDo = $queue.Count

# ── Banner ───────────────────────────────────────────────────

Write-Host '============================================' -ForegroundColor Yellow
Write-Host '  archpass2_local.ps1 -- Pass 2 (Local LLM)' -ForegroundColor Yellow
Write-Host '============================================' -ForegroundColor Yellow
Write-Host "Codebase:       $codebaseDesc"
Write-Host "LLM:            $llmModel @ $llmEndpoint"
Write-Host "Candidates:     $($scored.Count)  |  unchanged=$skipUnchanged  |  process: $toDo"
if ($Top -gt 0) { Write-Host "Top filter:     $Top" }
Write-Host "Output:         architecture/<path>.pass2.md"
Write-Host ''
Write-Host 'Press Ctrl+Q to cancel (checked between files).' -ForegroundColor DarkGray
Write-Host ''

if ($toDo -eq 0) {
    Write-Host 'Nothing to do. All Pass 2 docs are up to date.' -ForegroundColor Green
    exit 0
}

# ── Process files ────────────────────────────────────────────

$startTime = [datetime]::Now
$done      = 0
$failed    = 0

foreach ($rel in $queue) {
    Test-CancelKey
    $src     = Join-Path $repoRoot ($rel -replace '/','\')
    $outPath = Join-Path $archDir  (($rel -replace '/','\') + '.pass2.md')
    $pass1   = Join-Path $archDir  (($rel -replace '/','\') + '.md')
    $fence   = Get-FenceLang $rel $defaultFence
    $outDir  = Split-Path $outPath -Parent
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null

    $srcLines     = @(Get-Content $src -ErrorAction SilentlyContinue)
    $pass1Content = if (Test-Path $pass1) { Get-Content $pass1 -Raw } else { '(no first-pass doc)' }

    # Truncate source to 300 lines for Pass 2 (context budget is tight)
    $sourceContent = Truncate-Source $srcLines 300

    # Load targeted context if available, otherwise use global (truncated)
    $contextSection = ''
    $targetedCtxDir  = Join-Path $archDir '.pass2_context'
    $targetedCtxFile = Join-Path $targetedCtxDir (($rel -replace '/','\') + '.ctx.txt')
    if (Test-Path $targetedCtxFile) {
        $contextSection = Get-Content $targetedCtxFile -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
    } else {
        # Truncate global context more aggressively
        $shortArch = if ($archContext.Length -gt 3000) { $archContext.Substring(0, 3000) + "`n..." } else { $archContext }
        $shortXref = if ($xrefContext.Length -gt 2000) { $xrefContext.Substring(0, 2000) + "`n..." } else { $xrefContext }
        $contextSection = "ARCHITECTURE CONTEXT:`n$shortArch`n`nCROSS-REFERENCE CONTEXT:`n$shortXref"
    }

    $userPrompt = @"
$promptSchema

FILE PATH: $rel

FILE CONTENT ($($srcLines.Count) lines, truncated):
``````$fence
$sourceContent
``````

FIRST-PASS ANALYSIS:
$pass1Content

$contextSection
"@

    try {
        $resp = Invoke-LocalLLM `
            -SystemPrompt $systemPrompt `
            -UserPrompt   $userPrompt `
            -Endpoint     $llmEndpoint `
            -Model        $llmModel `
            -Temperature  $llmTemperature `
            -MaxTokens    500 `
            -Timeout      $llmTimeout

        # Post-process: ensure heading exists
        if ($resp -notmatch '^#') {
            $headingIdx = $resp.IndexOf("`n#")
            if ($headingIdx -ge 0) {
                $resp = $resp.Substring($headingIdx + 1)
            } else {
                $resp = "# $rel - Enhanced Analysis`n`n$resp"
            }
        }

        $resp | Set-Content -Path $outPath -Encoding UTF8

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
    Write-Host "Done. $done files processed. Pass 2 docs in: $archDir" -ForegroundColor Green
}
