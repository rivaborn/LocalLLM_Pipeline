# ============================================================
# arch_overview_local.ps1 -- Architecture Overview Generator (Local LLM)
#
# Synthesizes per-file docs into a subsystem-level architecture overview.
# Always uses chunked mode (local LLM context is too small for single-pass).
#
# Requires: llm_common.ps1 in the same directory.
# Prerequisites: Run archgen_local.ps1 first.
#
# Usage:
#   .\LocalLLMAnalysis\arch_overview_local.ps1 [-TargetDir <dir>] [-Single] [-Clean] [-Full]
# ============================================================

[CmdletBinding()]
param(
    [string]$TargetDir = "all",
    [switch]$Single,
    [switch]$Clean,
    [switch]$Full,
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

$codebaseDesc   = Cfg 'CODEBASE_DESC' 'game engine / game codebase'
$chunkThreshold = [int](Cfg 'CHUNK_THRESHOLD' '400')

$llmEndpoint    = Get-LLMEndpoint
$llmModel       = Cfg 'LLM_MODEL'       'qwen2.5-coder:14b'
$llmTemperature = [double](Cfg 'LLM_TEMPERATURE' '0.1')
$llmTimeout     = [int](Cfg 'LLM_TIMEOUT'        '120')

# ── Paths ────────────────────────────────────────────────────

$repoRoot = (Get-Location).Path
try {
    $g = & git rev-parse --show-toplevel 2>$null
    if ($LASTEXITCODE -eq 0 -and $g) { $repoRoot = $g.Trim() }
} catch {}

$archDir  = Join-Path $repoRoot 'architecture'
$stateDir = Join-Path $archDir  '.overview_state'
New-Item -ItemType Directory -Force -Path $archDir  | Out-Null
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null

$errorLog = Join-Path $stateDir 'last_error.log'
'' | Set-Content $errorLog -Encoding UTF8

# Prompt file
$overviewPromptFile = Join-Path $PSScriptRoot 'arch_overview_local_prompt.txt'
$overviewPromptSchema = ''
if (Test-Path $overviewPromptFile) {
    $overviewPromptSchema = Get-Content $overviewPromptFile -Raw
}

$systemPrompt = "You are generating architecture documentation for a $codebaseDesc. Be concise and factual."

if ($Clean) {
    Write-Host "CLEAN: removing overview outputs..." -ForegroundColor Cyan
    Get-ChildItem -Path $archDir -Filter '*architecture.md' -ErrorAction SilentlyContinue | Remove-Item -Force
    Get-ChildItem -Path $archDir -Filter '*diagram_data.md' -ErrorAction SilentlyContinue | Remove-Item -Force
}

# ── Helpers ──────────────────────────────────────────────────

function Test-OverviewDocIncluded($name, $fullName) {
    if ($fullName -match '[/\\]\.(archgen|overview|pass2)_state[/\\]') { return $false }
    if ($name -match '^(architecture|diagram_data|xref_index|callgraph)') { return $false }
    if ($name -match '\.pass2\.md$') { return $false }
    return $true
}

function Get-PerFileDocs($root) {
    return @(Get-ChildItem -Path $root -Recurse -Filter '*.md' -File -ErrorAction SilentlyContinue |
        Where-Object { Test-OverviewDocIncluded $_.Name $_.FullName } | Sort-Object FullName)
}

# Extract only # heading + ## Purpose from each doc (token-efficient)
function Extract-DocSummary($lines) {
    $sb = [System.Text.StringBuilder]::new()
    if (-not $lines) { return '' }
    $inPurpose = $false
    foreach ($line in $lines) {
        if ($line -match '^# ') {
            $sb.AppendLine($line) | Out-Null
            continue
        }
        if ($line -match '^## (Purpose|File Purpose)') {
            $inPurpose = $true
            continue
        }
        if ($line -match '^## ' -and $inPurpose) {
            $inPurpose = $false
            $sb.AppendLine('') | Out-Null
            continue
        }
        if ($inPurpose -and $line.Trim() -ne '') {
            $sb.AppendLine("  $($line.Trim())") | Out-Null
        }
    }
    return $sb.ToString()
}

function Build-SummaryData($root) {
    $docs = @(Get-PerFileDocs $root)
    $sb = [System.Text.StringBuilder]::new()
    foreach ($doc in $docs) {
        $lines = @(Get-Content $doc.FullName -ErrorAction SilentlyContinue)
        if ($lines.Count -eq 0) { continue }
        $summary = Extract-DocSummary $lines
        if ($summary.Trim() -ne '') {
            $sb.Append($summary) | Out-Null
            $sb.AppendLine('') | Out-Null
        }
    }
    return @{ Text = $sb.ToString(); DocCount = $docs.Count }
}

# Recursively discover subsystem directories for chunking
function Get-Subsystems($docRoot, $relPath, $threshold, $depth = 0) {
    $absPath = if ($relPath) { Join-Path $docRoot $relPath } else { $docRoot }
    $result  = [System.Collections.Generic.List[string]]::new()

    if (-not (Test-Path $absPath)) {
        $label = if ($relPath) { $relPath } else { '.' }
        $result.Add($label)
        return ,$result
    }

    $data = Build-SummaryData $absPath
    $textLines = @($data['Text'] -split "`n")
    $lineCount = [int]$textLines.Count

    if ($lineCount -le $threshold -or $depth -ge 4) {
        $label = if ($relPath) { $relPath } else { '.' }
        $result.Add($label)
        return ,$result
    }

    $children = @(Get-ChildItem -Path $absPath -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -notmatch '^\.' } | Sort-Object Name)

    if ([int]$children.Count -eq 0) {
        $label = if ($relPath) { $relPath } else { '.' }
        $result.Add($label)
        return ,$result
    }

    # Descend through single-child directories
    if ([int]$children.Count -eq 1) {
        $childRel = if ($relPath) { "$relPath/$($children[0].Name)" } else { $children[0].Name }
        $expanded = @(Get-Subsystems $docRoot $childRel $threshold $depth)
        foreach ($item in $expanded) { $result.Add($item) }
        return ,$result
    }

    foreach ($child in $children) {
        $childRel = if ($relPath) { "$relPath/$($child.Name)" } else { $child.Name }
        $expanded = @(Get-Subsystems $docRoot $childRel $threshold ($depth + 1))
        foreach ($item in $expanded) { $result.Add($item) }
    }
    return ,$result
}

# ── Main ─────────────────────────────────────────────────────

$docRoot   = if ($TargetDir -ne 'all' -and $TargetDir -ne '.') { Join-Path $archDir $TargetDir } else { $archDir }
$outPrefix = if ($TargetDir -ne 'all' -and $TargetDir -ne '.') { (Split-Path $TargetDir -Leaf) + ' ' } else { '' }
$outArch   = Join-Path $archDir ($outPrefix + 'architecture.md')

$topData      = Build-SummaryData $docRoot
$docCount     = $topData['DocCount']
$summaryLines = @($topData['Text'] -split "`n").Count

# Auto-detect mode
$mode = if ($Single) { 'single' } elseif ($summaryLines -gt $chunkThreshold) { 'chunked' } else { 'single' }

Write-Host '============================================' -ForegroundColor Yellow
Write-Host '  arch_overview_local.ps1 -- Architecture'    -ForegroundColor Yellow
Write-Host '============================================' -ForegroundColor Yellow
Write-Host "Codebase:       $codebaseDesc"
Write-Host "LLM:            $llmModel @ $llmEndpoint"
Write-Host "Target:         $TargetDir"
Write-Host "Mode:           $mode"
Write-Host "Per-file docs:  $docCount"
Write-Host "Summary lines:  $summaryLines (threshold: $chunkThreshold)"
Write-Host "Output:         $outArch"
Write-Host ''
Write-Host 'Press Ctrl+Q to cancel (checked between subsystems).' -ForegroundColor DarkGray
Write-Host ''

if ($docCount -eq 0) {
    Write-Host "No per-file docs found. Run archgen_local.ps1 first." -ForegroundColor Red
    exit 1
}

if ($mode -eq 'single') {
    # ── Single-pass mode ─────────────────────────────────────
    Write-Host "Generating overview in single pass..." -ForegroundColor Cyan

    $userPrompt = @"
$overviewPromptSchema

Below are summaries of all analyzed files:

$($topData['Text'])
"@

    try {
        $resp = Invoke-LocalLLM `
            -SystemPrompt $systemPrompt `
            -UserPrompt   $userPrompt `
            -Endpoint     $llmEndpoint `
            -Model        $llmModel `
            -Temperature  $llmTemperature `
            -MaxTokens    1200 `
            -Timeout      $llmTimeout

        $resp | Set-Content -Path $outArch -Encoding UTF8
        Write-Host "Done. Overview written to: $outArch" -ForegroundColor Green
    }
    catch {
        Write-Host "LLM call failed: $($_.Exception.Message)" -ForegroundColor Red
        [System.IO.File]::AppendAllText($errorLog, "$(Get-Date -Format u) | FAIL | single-pass | $($_.Exception.Message)`n")
        exit 1
    }
}
else {
    # ── Chunked mode ─────────────────────────────────────────
    Write-Host "Discovering subsystems for chunking..." -ForegroundColor Cyan
    $rawSubs = Get-Subsystems $docRoot '' $chunkThreshold 0
    # Flatten: Get-Subsystems returns List[string] which PowerShell may nest; extract all strings
    $subsystems = [System.Collections.Generic.List[string]]::new()
    foreach ($item in $rawSubs) {
        if ($item -is [string]) { $subsystems.Add($item) }
        elseif ($item -is [System.Collections.IEnumerable]) { foreach ($s in $item) { $subsystems.Add([string]$s) } }
    }
    Write-Host "Found $($subsystems.Count) subsystem chunks" -ForegroundColor Cyan

    $overviews = [System.Text.StringBuilder]::new()
    $idx = 0

    foreach ($sub in $subsystems) {
        Test-CancelKey
        $idx++
        $subPath = if ($sub -eq '.') { $docRoot } else { Join-Path $docRoot $sub }
        if (-not (Test-Path $subPath)) { continue }

        $subData = Build-SummaryData $subPath
        if ($subData['DocCount'] -eq 0) { continue }

        Write-Host "  [$idx/$($subsystems.Count)] $sub ($($subData['DocCount']) docs)..." -ForegroundColor DarkCyan

        $subPrompt = @"
You are generating a subsystem overview for the "$sub" part of a $codebaseDesc.

Output schema:
# Subsystem: $sub

## Purpose
1-3 sentences.

## Key Files
- file: role (one line each)

## Responsibilities
- 3-6 bullets

## Dependencies
- What this subsystem uses from other subsystems

Rules: No speculation. Keep output under 500 tokens.

BEGIN FILE SUMMARIES
$($subData['Text'])
END FILE SUMMARIES
"@

        try {
            $subResp = Invoke-LocalLLM `
                -SystemPrompt $systemPrompt `
                -UserPrompt   $subPrompt `
                -Endpoint     $llmEndpoint `
                -Model        $llmModel `
                -Temperature  $llmTemperature `
                -MaxTokens    600 `
                -Timeout      $llmTimeout

            $overviews.AppendLine($subResp) | Out-Null
            $overviews.AppendLine('') | Out-Null

            # Also save individual subsystem overview
            $subOutName = ($sub -replace '[/\\]', '_') + ' architecture.md'
            $subOutPath = Join-Path $archDir $subOutName
            $subResp | Set-Content -Path $subOutPath -Encoding UTF8
        }
        catch {
            Write-Host "    [FAIL] $sub -- $($_.Exception.Message)" -ForegroundColor Red
            [System.IO.File]::AppendAllText($errorLog, "$(Get-Date -Format u) | FAIL | $sub | $($_.Exception.Message)`n")
        }
    }

    # ── Tier 2: Synthesize final overview ────────────────────
    Write-Host ''
    Write-Host "Synthesizing final overview from $($subsystems.Count) subsystem overviews..." -ForegroundColor Cyan

    $synthPrompt = @"
$overviewPromptSchema

Below are per-subsystem overviews. Synthesize them into a unified architecture document.
Cross-reference subsystems where they interact.

BEGIN SUBSYSTEM OVERVIEWS
$($overviews.ToString())
END SUBSYSTEM OVERVIEWS
"@

    try {
        $synthResp = Invoke-LocalLLM `
            -SystemPrompt $systemPrompt `
            -UserPrompt   $synthPrompt `
            -Endpoint     $llmEndpoint `
            -Model        $llmModel `
            -Temperature  $llmTemperature `
            -MaxTokens    1200 `
            -Timeout      $llmTimeout

        $synthResp | Set-Content -Path $outArch -Encoding UTF8
        Write-Host "Done. Overview written to: $outArch" -ForegroundColor Green
    }
    catch {
        Write-Host "Synthesis failed: $($_.Exception.Message)" -ForegroundColor Red
        [System.IO.File]::AppendAllText($errorLog, "$(Get-Date -Format u) | FAIL | synthesis | $($_.Exception.Message)`n")

        # Fallback: concatenate subsystem overviews as the output
        $fallback = "# Architecture Overview`n`n(Synthesis failed -- subsystem overviews concatenated)`n`n" + $overviews.ToString()
        $fallback | Set-Content -Path $outArch -Encoding UTF8
        Write-Host "Fallback: concatenated subsystem overviews saved to: $outArch" -ForegroundColor Yellow
    }
}
