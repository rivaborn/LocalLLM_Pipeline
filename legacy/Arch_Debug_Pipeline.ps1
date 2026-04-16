<#
.SYNOPSIS
    Automated debug pipeline: analyze codebase, fix bugs with Claude, archive changes.

.DESCRIPTION
    Runs the four LocalLLMDebug analysis scripts against a target directory, then
    invokes Claude Code to read the analysis results and fix all identified bugs.
    Archives a summary of changes to "Implemented Plans/" with timestamps.

    Steps:
      1. dataflow_local.ps1    - Data flow analysis
      2. interfaces_local.ps1  - Interface contract extraction
      3. testgap_local.ps1     - Test gap analysis
      4. bughunt_local.ps1     - Per-file bug hunt
      5. Claude Code           - Read reports and fix bugs
      6. Archive               - Write Bug Fix Changes N.md to Implemented Plans/

    The pipeline is resumable: a .debug_progress file in the project root tracks
    completed steps. Re-running after an error skips completed steps.

.PARAMETER TargetDir
    Source code directory to analyze (e.g. src/nmon). Required.

.PARAMETER TestDir
    Test directory for testgap analysis. Defaults to "tests".

.PARAMETER Claude
    Claude account to use. Defaults to "Claude1".

.PARAMETER Model
    Override the Claude model for ALL files in the bug-fixing step. When not
    specified, the script auto-selects: Opus for files with real bugs, Sonnet
    for files with no significant issues.

.PARAMETER Ultrathink
    Force extended thinking (ultrathink) for ALL files, overriding auto-selection.

.PARAMETER NoUltrathink
    Disable extended thinking for ALL files, overriding auto-selection.

.PARAMETER EnvFile
    Path to .env file for the analysis scripts. Optional.

.PARAMETER Restart
    Ignore saved progress and start from step 1.

.PARAMETER DryRun
    Preview all steps without running anything.

.EXAMPLE
    .\LocalLLMDebug\Arch_Debug_Pipeline.ps1 -TargetDir src/nmon
    .\LocalLLMDebug\Arch_Debug_Pipeline.ps1 -TargetDir src/nmon -Claude Claude2
    .\LocalLLMDebug\Arch_Debug_Pipeline.ps1 -TargetDir src/nmon -Model opus -Ultrathink
    .\LocalLLMDebug\Arch_Debug_Pipeline.ps1 -TargetDir src/nmon -Restart
    .\LocalLLMDebug\Arch_Debug_Pipeline.ps1 -TargetDir src/nmon -DryRun
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$TargetDir,
    [string]$TestDir = "tests",
    [string]$Claude = "Claude1",
    [string]$Model,
    [switch]$Ultrathink,
    [switch]$NoUltrathink,
    [string]$EnvFile = "",
    [switch]$Restart,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
# The toolkit (LocalLLM_Pipeline/) and the target project live in separate
# directories. Analysis/debug artefacts belong in the target project, so use
# the invocation cwd as project root -- matches Arch_Analysis_Pipeline.py's
# Path.cwd() convention. Steps 1-4 are subprocesses that inherit cwd; step 5
# runs inline in this script and used to resolve $ProjectRoot to the toolkit
# root, which is why it looked for INTERFACES.md under LocalLLM_Pipeline/.
$ProjectRoot = (Get-Location).Path
$ImplDir = Join-Path $ProjectRoot "Implemented Plans"

# ── Local LLM helper + shared .env ───────────────────────────────────
# Step 5 (per-file bug fixing) now runs on the local Ollama server instead
# of Claude Code. The model, endpoint, ctx, and timeout come from
# Common/.env (LLM_MODEL, LLM_ENDPOINT/LLM_HOST+LLM_PORT, LLM_NUM_CTX, LLM_TIMEOUT).
. (Join-Path $ScriptDir '..\Common\llm_common.ps1')
$script:cfg = Read-EnvFile (Join-Path $ScriptDir '..\Common\.env')

$localFixModel   = Cfg 'LLM_MODEL' 'qwen3-coder:30b'
$localFixEndpoint= Get-LLMEndpoint
$localFixNumCtx  = [int](Cfg 'LLM_NUM_CTX' '32768')
$localFixTimeout = [int](Cfg 'LLM_TIMEOUT' '600')
$localFixMaxTok  = [int](Cfg 'LLM_FIX_MAX_TOKENS' '16384')

# ── Progress tracking ────────────────────────────────────────────────

$ProgressFile = Join-Path $ProjectRoot ".debug_progress"

function Get-DebugProgress {
    if (-not (Test-Path $ProgressFile)) { return 0 }
    $lines = Get-Content $ProgressFile
    foreach ($line in $lines) {
        if ($line -match '^LastCompleted=(\d+)$') { return [int]$Matches[1] }
    }
    return 0
}

function Save-DebugProgress {
    param([int]$Step, [int]$SubStep = -1)
    $lines = @(
        "LastCompleted=$Step"
        "TargetDir=$TargetDir"
        "Timestamp=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    )
    if ($SubStep -ge 0) { $lines += "SubStep=$SubStep" }
    $lines | Out-File -FilePath $ProgressFile -Encoding utf8
}

function Get-DebugSubStep {
    if (-not (Test-Path $ProgressFile)) { return -1 }
    $lines = Get-Content $ProgressFile
    foreach ($line in $lines) {
        if ($line -match '^SubStep=(\d+)$') { return [int]$Matches[1] }
    }
    return -1
}

function Clear-DebugProgress {
    if (Test-Path $ProgressFile) { Remove-Item $ProgressFile }
}

# ── Claude account helper ────────────────────────────────────────────

$ClaudeConfigDirs = @{
    "claude1" = "$env:USERPROFILE\.clauderivalon"
    "claude2" = "$env:USERPROFILE\.claudefksogbetun"
}

function Set-ClaudeAccount {
    $key = $Claude.ToLower()
    if (-not $ClaudeConfigDirs.ContainsKey($key)) {
        Write-Host "  ERROR: Unknown Claude account '$Claude'. Expected: Claude1 or Claude2" -ForegroundColor Red
        exit 1
    }
    $script:PrevConfigDir = $env:CLAUDE_CONFIG_DIR
    $env:CLAUDE_CONFIG_DIR = $ClaudeConfigDirs[$key]
}

function Restore-ClaudeAccount {
    $env:CLAUDE_CONFIG_DIR = $script:PrevConfigDir
}

# ── Smart model selection for per-file bug fixing ────────────────────

# Patterns that indicate the bug report found no real issues
$NoBugPatterns = @(
    'no significant bugs',
    'no bugs found',
    'no issues found',
    'no significant issues',
    'no critical',
    'no bugs were',
    'clean',
    'no problems',
    'no defects'
)

function Test-HasRealBugs {
    <#
    .SYNOPSIS
        Returns $true if the bug report content indicates real bugs to fix.
        Returns $false if it looks like a clean report.
    #>
    param([string]$BugReportContent)
    $lower = $BugReportContent.ToLower()
    foreach ($pattern in $NoBugPatterns) {
        if ($lower -contains $pattern -or $lower -match [regex]::Escape($pattern)) {
            return $false
        }
    }
    # Also check if the report is very short (under 200 chars) - likely no real bugs
    if ($BugReportContent.Trim().Length -lt 200) { return $false }
    return $true
}

function Get-FileModel {
    <#
    .SYNOPSIS
        Select the model for a given file based on whether it has real bugs.
    #>
    param([bool]$HasBugs)
    if ($Model) { return $Model }
    if ($HasBugs) { return "opus" }
    return "sonnet"
}

function Get-FileThinkPrefix {
    <#
    .SYNOPSIS
        Get the ultrathink prefix for a given file based on whether it has real bugs.
    #>
    param([bool]$HasBugs)
    if ($Ultrathink) { return "ultrathink. " }
    if ($NoUltrathink) { return "" }
    # Default: ultrathink for files with bugs, none for clean files
    if ($HasBugs) { return "ultrathink. " }
    return ""
}

# ── Next bug fix number ──────────────────────────────────────────────

function Get-NextBugFixNumber {
    if (-not (Test-Path $ImplDir -PathType Container)) { return 1 }
    $existing = @(Get-ChildItem -Path $ImplDir -Filter "Bug Fix Changes *.md" -ErrorAction SilentlyContinue)
    if ($existing.Count -eq 0) { return 1 }
    $numbers = @()
    foreach ($f in $existing) {
        if ($f.Name -match 'Bug Fix Changes (\d+)\.md$') {
            $numbers += [int]$Matches[1]
        }
    }
    if ($numbers.Count -eq 0) { return 1 }
    return (($numbers | Measure-Object -Maximum).Maximum + 1)
}

# ── Add timestamps to existing Implemented Plans files ───────────────

function Add-TimestampToFile {
    param([string]$FilePath)
    if (-not (Test-Path $FilePath)) { return }
    $info = Get-Item $FilePath
    $content = Get-Content $FilePath -Raw
    # Only add timestamp if not already present
    if ($content -notmatch '^\s*<!-- Timestamp:') {
        $ts = "<!-- Timestamp: $($info.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss')) -->`n`n"
        ($ts + $content) | Out-File -FilePath $FilePath -Encoding utf8 -NoNewline
    }
}

# ── Display helpers ──────────────────────────────────────────────────

function Write-Step {
    param([int]$Number, [int]$Total, [string]$Title)
    $bar = "=" * 60
    Write-Host "`n$bar" -ForegroundColor Cyan
    Write-Host "  Step $Number/$Total - $Title" -ForegroundColor Cyan
    Write-Host $bar -ForegroundColor Cyan
}

# ── Resume check ─────────────────────────────────────────────────────

$lastCompleted = Get-DebugProgress

if ($Restart -and $lastCompleted -gt 0) {
    Write-Host "Restarting (ignoring saved progress through step $lastCompleted)" -ForegroundColor Yellow
    Clear-DebugProgress
    $lastCompleted = 0
}
elseif ($lastCompleted -gt 0 -and $lastCompleted -lt 6) {
    Write-Host "Resuming from step $($lastCompleted + 1) (steps 1-$lastCompleted completed previously)" -ForegroundColor Yellow
    Write-Host "  Use -Restart to start over" -ForegroundColor DarkGray
}
elseif ($lastCompleted -ge 6) {
    Write-Host "All steps were completed previously. Use -Restart to run again." -ForegroundColor Yellow
    exit 0
}

Write-Host "Target directory: $TargetDir" -ForegroundColor Gray
Write-Host "Test directory: $TestDir" -ForegroundColor Gray
Write-Host "Claude account: $Claude" -ForegroundColor Gray
if ($Model) {
    Write-Host "Model: $Model (all files)" -ForegroundColor Gray
}
else {
    Write-Host "Model: auto (Opus for bugs, Sonnet for clean files)" -ForegroundColor Gray
}
if ($Ultrathink) { Write-Host "Ultrathink: forced ON" -ForegroundColor Gray }
elseif ($NoUltrathink) { Write-Host "Ultrathink: forced OFF" -ForegroundColor Gray }
else { Write-Host "Ultrathink: auto (ON for bugs, OFF for clean files)" -ForegroundColor Gray }

$totalSteps = 6

# ── Build common args for analysis scripts ───────────────────────────

$envArgs = @()
if ($EnvFile) { $envArgs = @("-EnvFile", $EnvFile) }

# ══════════════════════════════════════════════════════════════════════
# Step 1: Data Flow Analysis
# ══════════════════════════════════════════════════════════════════════

if ($lastCompleted -lt 1) {
    Write-Step 1 $totalSteps "Data Flow Analysis"

    if ($DryRun) {
        Write-Host "  [DRY RUN] Would run: dataflow_local.ps1 -TargetDir $TargetDir" -ForegroundColor DarkGray
    }
    else {
        $dfScript = Join-Path $ScriptDir "dataflow_local.ps1"
        & $dfScript -TargetDir $TargetDir @envArgs
        if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {
            Write-Host "  ERROR: dataflow_local.ps1 failed (exit code $LASTEXITCODE)" -ForegroundColor Red
            exit 1
        }
        Save-DebugProgress 1
        Write-Host "  Step 1 complete - architecture/DATA_FLOW.md" -ForegroundColor Green
    }
}
else {
    Write-Host "`n  Step 1/6 - Data Flow Analysis [already done]" -ForegroundColor DarkGray
}

# ══════════════════════════════════════════════════════════════════════
# Step 2: Interface Contract Extraction
# ══════════════════════════════════════════════════════════════════════

if ($lastCompleted -lt 2) {
    Write-Step 2 $totalSteps "Interface Contract Extraction"

    if ($DryRun) {
        Write-Host "  [DRY RUN] Would run: interfaces_local.ps1 -TargetDir $TargetDir" -ForegroundColor DarkGray
    }
    else {
        $ifScript = Join-Path $ScriptDir "interfaces_local.ps1"
        & $ifScript -TargetDir $TargetDir @envArgs
        if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {
            Write-Host "  ERROR: interfaces_local.ps1 failed (exit code $LASTEXITCODE)" -ForegroundColor Red
            exit 1
        }
        Save-DebugProgress 2
        Write-Host "  Step 2 complete - architecture/INTERFACES.md" -ForegroundColor Green
    }
}
else {
    Write-Host "`n  Step 2/6 - Interface Contract Extraction [already done]" -ForegroundColor DarkGray
}

# ══════════════════════════════════════════════════════════════════════
# Step 3: Test Gap Analysis
# ══════════════════════════════════════════════════════════════════════

if ($lastCompleted -lt 3) {
    Write-Step 3 $totalSteps "Test Gap Analysis"

    if ($DryRun) {
        Write-Host "  [DRY RUN] Would run: testgap_local.ps1 -SrcDir $TargetDir -TestDir $TestDir" -ForegroundColor DarkGray
    }
    else {
        $tgScript = Join-Path $ScriptDir "testgap_local.ps1"
        & $tgScript -SrcDir $TargetDir -TestDir $TestDir @envArgs
        if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {
            Write-Host "  ERROR: testgap_local.ps1 failed (exit code $LASTEXITCODE)" -ForegroundColor Red
            exit 1
        }
        Save-DebugProgress 3
        Write-Host "  Step 3 complete - test_gaps/GAP_REPORT.md" -ForegroundColor Green
    }
}
else {
    Write-Host "`n  Step 3/6 - Test Gap Analysis [already done]" -ForegroundColor DarkGray
}

# ══════════════════════════════════════════════════════════════════════
# Step 4: Bug Hunt
# ══════════════════════════════════════════════════════════════════════

if ($lastCompleted -lt 4) {
    Write-Step 4 $totalSteps "Bug Hunt"

    if ($DryRun) {
        Write-Host "  [DRY RUN] Would run: bughunt_local.ps1 -TargetDir $TargetDir" -ForegroundColor DarkGray
    }
    else {
        $bhScript = Join-Path $ScriptDir "bughunt_local.ps1"
        & $bhScript -TargetDir $TargetDir @envArgs
        if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {
            Write-Host "  ERROR: bughunt_local.ps1 failed (exit code $LASTEXITCODE)" -ForegroundColor Red
            exit 1
        }
        Save-DebugProgress 4
        Write-Host "  Step 4 complete - bug_reports/SUMMARY.md" -ForegroundColor Green
    }
}
else {
    Write-Host "`n  Step 4/6 - Bug Hunt [already done]" -ForegroundColor DarkGray
}

# ══════════════════════════════════════════════════════════════════════
# Step 5: Claude Code - Fix bugs per file
# ══════════════════════════════════════════════════════════════════════

if ($lastCompleted -lt 5) {
    Write-Step 5 $totalSteps "Claude Code - Fix Bugs (per file)"

    # Verify all analysis outputs exist
    $interfacesFile = Join-Path (Join-Path $ProjectRoot "architecture") "INTERFACES.md"
    $dataflowFile   = Join-Path (Join-Path $ProjectRoot "architecture") "DATA_FLOW.md"
    $bugSummaryFile = Join-Path (Join-Path $ProjectRoot "bug_reports") "SUMMARY.md"
    $gapReportFile  = Join-Path (Join-Path $ProjectRoot "test_gaps") "GAP_REPORT.md"

    foreach ($f in @($interfacesFile, $dataflowFile, $bugSummaryFile, $gapReportFile)) {
        if (-not (Test-Path $f)) {
            Write-Host "  ERROR: Required file not found: $f" -ForegroundColor Red
            Write-Host "  Run the analysis steps first (steps 1-4)" -ForegroundColor Red
            exit 1
        }
    }

    # Load shared context (interface contracts + data flow) once
    $interfaces = Get-Content $interfacesFile -Raw
    $dataflow   = Get-Content $dataflowFile -Raw

    # Collect per-file bug reports
    $bugReportsDir = Join-Path (Join-Path $ProjectRoot "bug_reports") "src"
    $bugFiles = @()
    if (Test-Path $bugReportsDir -PathType Container) {
        $bugFiles = @(Get-ChildItem -Path $bugReportsDir -Filter "*.md" -Recurse |
                      Sort-Object FullName)
    }

    # Collect per-file test gap reports
    $testGapDir = Join-Path (Join-Path $ProjectRoot "test_gaps") "src"
    # Build a lookup: source relative path -> gap report content
    $gapLookup = @{}
    if (Test-Path $testGapDir -PathType Container) {
        $gapFiles = @(Get-ChildItem -Path $testGapDir -Filter "*.gap.md" -Recurse)
        foreach ($gf in $gapFiles) {
            # Key: relative path from test_gaps/src, e.g. nmon/config.py.gap.md
            $relKey = $gf.FullName.Substring($testGapDir.Length + 1) -replace '\\', '/'
            $gapLookup[$relKey] = Get-Content $gf.FullName -Raw
        }
    }

    # Collect per-file interface reports
    $ifaceDir = Join-Path (Join-Path $ProjectRoot "architecture") "interfaces"
    $ifaceLookup = @{}
    if (Test-Path $ifaceDir -PathType Container) {
        $ifaceFiles = @(Get-ChildItem -Path $ifaceDir -Filter "*.iface.md" -Recurse)
        foreach ($ifl in $ifaceFiles) {
            $relKey = $ifl.FullName.Substring($ifaceDir.Length + 1) -replace '\\', '/'
            $ifaceLookup[$relKey] = Get-Content $ifl.FullName -Raw
        }
    }

    if ($bugFiles.Count -eq 0) {
        Write-Host "  No per-file bug reports found - nothing to fix" -ForegroundColor DarkYellow
        Save-DebugProgress 5
    }
    else {
        $totalFiles = $bugFiles.Count
        $changeSummaryFile = Join-Path $ProjectRoot ".debug_changes.md"

        # Check for resume
        $resumeSubStep = -1
        if ((Get-DebugProgress) -eq 4) {
            $resumeSubStep = Get-DebugSubStep
        }

        if ($resumeSubStep -lt 0) {
            # Starting fresh - write the change summary header
            Write-Host "  Fixing bugs in $totalFiles file(s)..." -ForegroundColor Gray
            $summaryHeader = @"
# Bug Fix Changes - Detailed Log

Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
Target: $TargetDir

"@
            $summaryHeader | Out-File -FilePath $changeSummaryFile -Encoding utf8
        }
        else {
            Write-Host "  Resuming from file $($resumeSubStep + 1) ($resumeSubStep of $totalFiles done)" -ForegroundColor Yellow
        }

        $fileNum = 0
        foreach ($bf in $bugFiles) {
            $fileNum++

            # Skip completed files when resuming
            if ($fileNum -le $resumeSubStep) {
                $relPath = $bf.FullName.Substring((Join-Path (Join-Path $ProjectRoot "bug_reports") "src").Length + 1) -replace '\\', '/'
                Write-Host "    $fileNum/$totalFiles - $relPath [already done]" -ForegroundColor DarkGray
                continue
            }

            # Derive the source file path from the bug report path
            # bug_reports/src/nmon/config.py.md -> src/nmon/config.py
            $bugRelPath = $bf.FullName.Substring((Join-Path $ProjectRoot "bug_reports").Length + 1) -replace '\\', '/'
            $srcRelPath = $bugRelPath -replace '\.md$', ''
            $srcFullPath = Join-Path $ProjectRoot ($srcRelPath -replace '/', '\')

            # Derive lookup keys for gap and interface reports
            # src/nmon/config.py -> nmon/config.py.gap.md / nmon/config.py.iface.md
            $fileKey = ($srcRelPath -replace '^src/', '')
            $gapKey = "$fileKey.gap.md"
            $ifaceKey = "$srcRelPath.iface.md"

            # Read the bug report for this file
            $fileBugReport = Get-Content $bf.FullName -Raw

            # Determine if this file has real bugs; clean files are skipped
            # outright since the local model has no tools and nothing to do.
            $hasBugs = Test-HasRealBugs $fileBugReport

            if ($hasBugs) {
                Write-Host "    $fileNum/$totalFiles - $srcRelPath [$localFixModel]" -ForegroundColor Cyan
            }
            else {
                Write-Host "    $fileNum/$totalFiles - $srcRelPath [clean - skipped]" -ForegroundColor DarkGray
            }

            # Read the source file itself (if it exists)
            $sourceContent = ""
            if (Test-Path $srcFullPath) {
                $sourceContent = Get-Content $srcFullPath -Raw
            }
            else {
                Write-Host "      Warning: Source file not found: $srcFullPath" -ForegroundColor DarkYellow
                Save-DebugProgress 4 -SubStep $fileNum
                continue
            }

            # Get per-file interface contract if available
            $fileIface = ""
            if ($ifaceLookup.ContainsKey($ifaceKey)) {
                $fileIface = "`n## Interface Contract for $srcRelPath`n`n" + $ifaceLookup[$ifaceKey]
            }

            # Get per-file test gap if available
            $fileGap = ""
            if ($gapLookup.ContainsKey($gapKey)) {
                $fileGap = "`n## Test Gap Report for $srcRelPath`n`n" + $gapLookup[$gapKey]
            }

            # Clean files are skipped: the local model has no edit tools, so
            # there is nothing for it to do on a file without bugs. Record the
            # skip in the change log and move on.
            if (-not $hasBugs) {
                "`n---`n`nNo changes needed for $srcRelPath (bug report indicates clean file).`n" |
                    Out-File -FilePath $changeSummaryFile -Append -Encoding utf8
                Save-DebugProgress 4 -SubStep $fileNum
                continue
            }

            # Build the per-file prompt. The local model has no tools, so it
            # must return the full fixed file inside a fenced block; the
            # script then writes that content back to disk and appends the
            # accompanying summary to the change log.
            $filePrompt = @'
You are a senior software engineer fixing bugs in a single Python source
file. You have no tools; you must output the complete corrected file plus
a summary of changes.

Rules:
- Fix every issue in the bug report below
- Keep fixes consistent with the interface contracts and data flow context
- Do NOT add new features or refactor beyond what the bug fixes require
- Do NOT modify unrelated code
- Preserve all public symbol names (classes, functions, methods, module
  attributes) that other files already import

Output EXACTLY this structure and nothing else:

```python
<complete corrected contents of SRCPATH, no omissions, no "..." elisions>
```

### SRCPATH
| Change | Reason |
|--------|--------|
| <what changed> | <why> |

<free-form explanation of each fix>

## Bug Report

'@ + "`n`n" + $fileBugReport + $fileIface + $fileGap + @'

## Data Flow Context (system-wide)

'@ + "`n`n" + $dataflow + @'

## Source File: SRCPATH

```python
SOURCECONTENT
```
'@
            $filePrompt = $filePrompt -replace 'SRCPATH', $srcRelPath
            $filePrompt = $filePrompt -replace 'SOURCECONTENT', $sourceContent

            if ($DryRun) {
                Write-Host "      [DRY RUN] Would fix: $srcRelPath ($($filePrompt.Length) chars) [$localFixModel]" -ForegroundColor DarkGray
                continue
            }

            try {
                $result = Invoke-LocalLLM `
                    -UserPrompt  $filePrompt `
                    -Model       $localFixModel `
                    -Endpoint    $localFixEndpoint `
                    -NumCtx      $localFixNumCtx `
                    -MaxTokens   $localFixMaxTok `
                    -Timeout     $localFixTimeout `
                    -Temperature 0.1
            }
            catch {
                Write-Host "      ERROR: Local LLM failed on ${srcRelPath}: $($_.Exception.Message)" -ForegroundColor Red
                exit 1
            }

            # Extract the fixed file from the first ```python (or plain ```)
            # fence. Write it to disk; skip the write if parsing fails so a
            # bad response can't clobber the source.
            $codeMatch = [regex]::Match($result, '```(?:python|py)?\s*\r?\n(.*?)\r?\n```', 'Singleline')
            if (-not $codeMatch.Success) {
                Write-Host "      ERROR: Could not find fenced code block in LLM response for $srcRelPath" -ForegroundColor Red
                Write-Host "      Raw response saved to debug_response.txt" -ForegroundColor DarkYellow
                $result | Out-File -FilePath (Join-Path $ProjectRoot "debug_response.txt") -Encoding utf8
                exit 1
            }
            $fixedCode = $codeMatch.Groups[1].Value
            $fixedCode | Out-File -FilePath $srcFullPath -Encoding utf8

            # Everything AFTER the closing code fence is the summary.
            $summaryStart = $codeMatch.Index + $codeMatch.Length
            $fileChanges = $result.Substring($summaryStart).Trim()
            if (-not $fileChanges) {
                $fileChanges = "### $srcRelPath`n(fix applied; model produced no summary)"
            }
            "`n---`n`n$fileChanges`n" | Out-File -FilePath $changeSummaryFile -Append -Encoding utf8

            Write-Host "      $fileNum/$totalFiles - done" -ForegroundColor Green

            # Save per-file progress
            Save-DebugProgress 4 -SubStep $fileNum
        }

        if (-not $DryRun) {
            Save-DebugProgress 5
            Write-Host "  Step 5 complete - all $totalFiles file(s) processed" -ForegroundColor Green
        }
    }
}
else {
    Write-Host "`n  Step 5/6 - Claude Code Fix Bugs [already done]" -ForegroundColor DarkGray
}

# ══════════════════════════════════════════════════════════════════════
# Step 6: Archive Bug Fix Changes to Implemented Plans
# ══════════════════════════════════════════════════════════════════════

if ($lastCompleted -lt 6) {
    Write-Step 6 $totalSteps "Archive Bug Fix Changes"

    if ($DryRun) {
        $nextNum = Get-NextBugFixNumber
        Write-Host "  [DRY RUN] Would write: Implemented Plans/Bug Fix Changes $nextNum.md" -ForegroundColor DarkGray
    }
    else {
        # Ensure Implemented Plans directory exists
        if (-not (Test-Path $ImplDir -PathType Container)) {
            New-Item -ItemType Directory -Path $ImplDir | Out-Null
        }

        $nextNum = Get-NextBugFixNumber
        $timestamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
        $bugFixFile = Join-Path $ImplDir "Bug Fix Changes $nextNum.md"

        # Read the change summary from Step 5
        $changeSummaryFile = Join-Path $ProjectRoot ".debug_changes.md"
        if (Test-Path $changeSummaryFile) {
            $changeSummary = Get-Content $changeSummaryFile -Raw
        }
        else {
            $changeSummary = "(No change summary available - Claude may have applied fixes without generating a summary)"
        }

        # Write the archive file with timestamp
        $archiveContent = @"
<!-- Timestamp: $timestamp -->

# Bug Fix Changes $nextNum

**Date:** $timestamp
**Target:** $TargetDir
**Pipeline:** Arch_Debug_Pipeline.ps1

## Analysis Reports Used

- architecture/INTERFACES.md
- architecture/DATA_FLOW.md
- bug_reports/SUMMARY.md
- test_gaps/GAP_REPORT.md

$changeSummary
"@
        $archiveContent | Out-File -FilePath $bugFixFile -Encoding utf8
        Write-Host "  Saved: $bugFixFile" -ForegroundColor Green

        # Add timestamps to any existing Architecture Plan files that don't have them
        $archPlans = @(Get-ChildItem -Path $ImplDir -Filter "Architecture Plan *.md" -ErrorAction SilentlyContinue)
        foreach ($ap in $archPlans) {
            Add-TimestampToFile $ap.FullName
        }

        # Clean up temp files
        if (Test-Path $changeSummaryFile) { Remove-Item $changeSummaryFile }

        Save-DebugProgress 6
        Write-Host "  Step 6 complete" -ForegroundColor Green
    }
}
else {
    Write-Host "`n  Step 6/6 - Archive Bug Fix Changes [already done]" -ForegroundColor DarkGray
}

# ── Summary ──────────────────────────────────────────────────────────

# Clean up progress on full completion
if (-not $DryRun) { Clear-DebugProgress }

$bar = "=" * 60
Write-Host "`n$bar" -ForegroundColor Green
Write-Host "  Debug pipeline complete." -ForegroundColor Green
Write-Host "    Step 1: architecture/DATA_FLOW.md" -ForegroundColor Gray
Write-Host "    Step 2: architecture/INTERFACES.md" -ForegroundColor Gray
Write-Host "    Step 3: test_gaps/GAP_REPORT.md" -ForegroundColor Gray
Write-Host "    Step 4: bug_reports/SUMMARY.md" -ForegroundColor Gray
Write-Host "    Step 5: Claude Code bug fixes applied" -ForegroundColor Gray
if (-not $DryRun) {
    Write-Host "    Step 6: Implemented Plans/Bug Fix Changes $nextNum.md" -ForegroundColor Gray
}
Write-Host $bar -ForegroundColor Green
