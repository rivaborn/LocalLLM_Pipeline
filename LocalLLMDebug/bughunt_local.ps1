# ============================================================
# bughunt_local.ps1 -- Per-File Bug Hunt (Local LLM)
#
# Runs a bug-focused LLM analysis pass over every source file.
# Output goes to bug_reports/ at the repo root (parallel to architecture/).
# Uses SHA1-based skip so only changed files are re-analysed.
#
# Requires: llm_common.ps1 in the same directory.
#
# Usage:
#   .\LocalLLMDebug\bughunt_local.ps1 [-TargetDir <path>] [-Clean] [-Force]
#
# Examples:
#   .\LocalLLMDebug\bughunt_local.ps1
#   .\LocalLLMDebug\bughunt_local.ps1 -TargetDir src/nmon/gpu
#   .\LocalLLMDebug\bughunt_local.ps1 -Clean
#   .\LocalLLMDebug\bughunt_local.ps1 -Force        # re-analyse all, ignore hashes
# ============================================================

[CmdletBinding()]
param(
    [string]$TargetDir = ".",
    [switch]$Clean,
    [switch]$Force,
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
$llmModel       = Get-LLMModel -RoleKey 'LLM_MODEL'
$llmTemperature = [double](Cfg 'LLM_TEMPERATURE' '0.1')
$llmTimeout     = [int](Cfg 'LLM_TIMEOUT'        '120')
# Bug reports need more tokens than arch docs
$llmMaxTokens   = [int](Cfg 'BUGHUNT_MAX_TOKENS' '900')

# ── Paths ────────────────────────────────────────────────────

$repoRoot = (Get-Location).Path
try {
    $g = & git rev-parse --show-toplevel 2>$null
    if ($LASTEXITCODE -eq 0 -and $g) { $repoRoot = $g.Trim() }
} catch {}

$bugDir   = Join-Path $repoRoot 'bug_reports'
$stateDir = Join-Path $bugDir   '.bughunt_state'
New-Item -ItemType Directory -Force -Path $bugDir   | Out-Null
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null

# Prompt file
$promptFile = Join-Path $PSScriptRoot 'bughunt_prompt.txt'
if (-not (Test-Path $promptFile)) {
    Write-Host "Missing prompt file: $promptFile" -ForegroundColor Red
    exit 2
}
$promptSchema = Get-Content $promptFile -Raw

$systemPrompt = "You are a senior engineer reviewing source files from a $codebaseDesc. Find bugs, not style issues. Follow the output schema exactly."

$hashDbPath = Join-Path $stateDir 'hashes.tsv'
$errorLog   = Join-Path $stateDir 'last_error.log'
$summaryLog = Join-Path $bugDir   'SUMMARY.md'

# ── Clean ────────────────────────────────────────────────────

if ($Clean) {
    Write-Host "CLEAN: removing bug_reports and state..." -ForegroundColor Cyan
    Remove-Item -Path $bugDir -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $bugDir   | Out-Null
    New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
}

'' | Set-Content $errorLog -Encoding UTF8
if (-not (Test-Path $hashDbPath)) { '' | Set-Content $hashDbPath -Encoding UTF8 }

# ── Hash DB ──────────────────────────────────────────────────

$oldSha = @{}
if (-not $Force) {
    Get-Content $hashDbPath | ForEach-Object {
        $parts = $_ -split "`t", 2
        if ($parts.Count -eq 2 -and $parts[1] -ne '') { $oldSha[$parts[1]] = $parts[0] }
    }
}

# ── Collect files ────────────────────────────────────────────

$scanRoot = if ($TargetDir -eq '.') { $repoRoot } else { Join-Path $repoRoot $TargetDir }
if (-not (Test-Path $scanRoot)) {
    Write-Host "Target directory not found: $scanRoot" -ForegroundColor Red
    exit 1
}

$allFiles = Get-ChildItem -Path $scanRoot -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object {
        $rel = $_.FullName.Substring($repoRoot.Length).TrimStart('\', '/') -replace '\\', '/'
        if ($rel -match '^(architecture|bug_reports)/' -or $rel -match '/(architecture|bug_reports)/') { return $false }
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
if ($total -eq 0) {
    Write-Host "No matching source files found under '$scanRoot'" -ForegroundColor Red
    exit 1
}

# ── Build queue (skip unchanged) ─────────────────────────────

$queue         = [System.Collections.Generic.List[string]]::new()
$skipUnchanged = 0

foreach ($rel in $files) {
    $src = Join-Path $repoRoot ($rel -replace '/', '\')
    $out = Join-Path $bugDir   (($rel -replace '/', '\') + '.md')
    $sha = Get-SHA1 $src

    if ($oldSha.ContainsKey($rel) -and $oldSha[$rel] -eq $sha -and (Test-Path $out)) {
        $skipUnchanged++
        continue
    }
    $queue.Add($rel)
}

$toDo = $queue.Count

# ── Optional: load xref_index.md from Analysis pipeline ──────
# If ARCHITECTURE_DIR points at a valid Analysis output and xref_index.md
# exists, inject it into every per-file bughunt prompt so the model sees
# callers/callees and can spot integration bugs that a single-file view
# would miss. Gracefully skips when Analysis hasn't been run.

$xrefContent = ''
$xrefPath = Resolve-ArchFile 'xref_index.md' $repoRoot
if ($xrefPath) {
    $xrefContent = Get-Content $xrefPath -Raw
    Write-Host ("  [integration] xref_index.md loaded: {0:N1} KB ({1})" -f ($xrefContent.Length / 1KB), $xrefPath) -ForegroundColor DarkCyan
}

# ── Banner ───────────────────────────────────────────────────

Write-Host '============================================' -ForegroundColor Cyan
Write-Host '  bughunt_local.ps1 -- LLM Bug Hunt'         -ForegroundColor Cyan
Write-Host '============================================' -ForegroundColor Cyan
Write-Host "Repo root:      $repoRoot"
Write-Host "Codebase:       $codebaseDesc"
Write-Host "Target:         $TargetDir"
Write-Host "LLM:            $llmModel @ $llmEndpoint"
Write-Host "Max file lines: $maxFileLines"
Write-Host "Max output tok: $llmMaxTokens"
Write-Host "Files:          $total total  |  unchanged=$skipUnchanged  |  process: $toDo"
Write-Host "Output dir:     $bugDir"
Write-Host ''
Write-Host 'Press Ctrl+Q to cancel (checked between files).' -ForegroundColor DarkGray
Write-Host ''

if ($toDo -eq 0) {
    Write-Host 'Nothing to do. All reports are up to date.' -ForegroundColor Green
    exit 0
}

# ── Process files ─────────────────────────────────────────────

$startTime  = [datetime]::Now
$done       = 0
$failed     = 0
$issueFiles = [System.Collections.Generic.List[string]]::new()

foreach ($rel in $queue) {
    Test-CancelKey
    $src     = Join-Path $repoRoot ($rel -replace '/', '\')
    $outPath = Join-Path $bugDir   (($rel -replace '/', '\') + '.md')
    $outDir  = Split-Path $outPath -Parent
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null

    $fence    = Get-FenceLang $rel $defaultFence
    $srcLines = @(Get-Content $src -ErrorAction SilentlyContinue)
    if (-not $srcLines) { $srcLines = @() }

    $sourceContent = Truncate-Source $srcLines $maxFileLines

    $xrefSection = ''
    if ($xrefContent) {
        $xrefSection = @"
CODEBASE CROSS-REFERENCE INDEX (function->file map, call edges, global state ownership, header deps):

``````
$xrefContent
``````

Use this to spot integration bugs: mismatched contracts between a caller and callee, unchecked preconditions at a boundary, or global state touched from an unexpected place. Flag only issues that the file under review participates in -- do not report bugs in other files.

"@
    }

    $userPrompt = @"
$promptSchema

${xrefSection}FILE PATH: $rel

FILE CONTENT ($($srcLines.Count) lines):
``````$fence
$sourceContent
``````
"@

    try {
        $resp = Invoke-LocalLLM `
            -SystemPrompt $systemPrompt `
            -UserPrompt   $userPrompt `
            -Endpoint     $llmEndpoint `
            -Model        $llmModel `
            -Temperature  $llmTemperature `
            -MaxTokens    $llmMaxTokens `
            -Timeout      $llmTimeout

        # Ensure response starts with a heading
        if ($resp -notmatch '^#') {
            $headingIdx = $resp.IndexOf("`n#")
            if ($headingIdx -ge 0) {
                $resp = $resp.Substring($headingIdx + 1)
            } else {
                $resp = "# $rel`n`n$resp"
            }
        }

        $resp | Set-Content -Path $outPath -Encoding UTF8

        # Track files with issues for summary
        if ($resp -match '\[HIGH\]|\[MEDIUM\]') {
            $issueFiles.Add($rel)
        }

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

# ── Deduplicate hash DB ───────────────────────────────────────

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

# ── Write SUMMARY.md ─────────────────────────────────────────

$allReports = @(Get-ChildItem -Path $bugDir -Recurse -Filter '*.md' -File |
    Where-Object { $_.Name -ne 'SUMMARY.md' -and $_.FullName -notmatch '\.bughunt_state' })

$highCount   = 0
$medCount    = 0
$lowCount    = 0
$cleanCount  = 0
$summaryLines = [System.Collections.Generic.List[string]]::new()
$summaryLines.Add("# Bug Hunt Summary")
$summaryLines.Add("")
$summaryLines.Add("Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm')")
$summaryLines.Add("Codebase: $codebaseDesc")
$summaryLines.Add("")
$summaryLines.Add("## Files with HIGH / MEDIUM findings")
$summaryLines.Add("")

foreach ($report in ($allReports | Sort-Object FullName)) {
    $content = Get-Content $report.FullName -Raw -ErrorAction SilentlyContinue
    if (-not $content) { continue }
    $h = ([regex]::Matches($content, '\[HIGH\]')).Count
    $m = ([regex]::Matches($content, '\[MEDIUM\]')).Count
    $l = ([regex]::Matches($content, '\[LOW\]')).Count
    $highCount += $h
    $medCount  += $m
    $lowCount  += $l
    if ($content -match 'Verdict\s*\r?\n\s*\r?\nCLEAN') { $cleanCount++ }
    if ($h -gt 0 -or $m -gt 0) {
        $relPath = $report.FullName.Substring($bugDir.Length).TrimStart('\','/') -replace '\\','/'
        $tag = if ($h -gt 0) { "HIGH:$h" } else { '' }
        if ($m -gt 0) { $tag += if ($tag) { " MED:$m" } else { "MED:$m" } }
        $summaryLines.Add("- ``$relPath`` -- $tag")
    }
}

if ($summaryLines.Count -eq 9) {
    $summaryLines.Add("None -- all files CLEAN.")
}

$summaryLines.Add("")
$summaryLines.Add("## Totals")
$summaryLines.Add("")
$summaryLines.Add("| Severity | Count |")
$summaryLines.Add("|----------|-------|")
$summaryLines.Add("| HIGH     | $highCount |")
$summaryLines.Add("| MEDIUM   | $medCount |")
$summaryLines.Add("| LOW      | $lowCount |")
$summaryLines.Add("| CLEAN    | $cleanCount files |")
$summaryLines.Add("")
$summaryLines.Add("Reports in: ``$bugDir``")

($summaryLines -join "`n") | Set-Content -Path $summaryLog -Encoding UTF8

# ── Result ───────────────────────────────────────────────────

Write-Host ''
if ($failed -gt 0) {
    Write-Host "Completed with $failed failures. See: $errorLog" -ForegroundColor Yellow
} else {
    Write-Host "Done. $done files analysed." -ForegroundColor Green
}
Write-Host "HIGH: $highCount  MEDIUM: $medCount  LOW: $lowCount" -ForegroundColor $(if ($highCount -gt 0) { 'Red' } elseif ($medCount -gt 0) { 'Yellow' } else { 'Green' })
Write-Host "Summary: $summaryLog" -ForegroundColor Cyan
