# ============================================================
# bughunt_iterative_local.ps1 -- Iterative Bug Hunt + Auto-Fix
#
# For each source file, runs up to four analysis passes per iteration:
#   [bugs]      General bug detection (crashes, data loss, logic errors)
#   [dataflow]  Data flow errors (type mismatches, missing validation,
#               race conditions on shared state)
#   [contracts] Interface contract violations (resource leaks, broken
#               postconditions, invariant violations)
# All three feed a single combined fix call each iteration.
#
# For each source file, also locates the matching test file and runs:
#   [tests]     Test quality bugs (trivially-passing tests, mock
#               divergence, weak assertions, missing HIGH-risk coverage)
# in a separate iterative loop targeting the test file.
#
# All four analysis types are enabled by default.
# Use -SkipBugs / -SkipDataflow / -SkipContracts / -SkipTests to disable.
#
# Stop conditions per file: CLEAN (no HIGH/MEDIUM), MAX_ITER, STUCK,
#   SYNTAX_ERR (Python files), ERROR (LLM failure), DIVERGING (HIGH not
#   improving for N consecutive iterations), BLOAT (fix grew file beyond
#   BUGHUNT_BLOAT_RATIO). MAX_ITER / DIVERGING / BLOAT / STUCK all revert
#   to the best (lowest-HIGH, tie-broken by lowest-MED) version seen.
#
# Output:
#   bug_fixes/<rel>                -- fixed source file (if changed)
#   bug_fixes/<rel>.iter_log.md   -- per-iteration analysis + fix log
#   bug_fixes/<testRel>           -- fixed test file (if changed)
#   bug_fixes/<testRel>.iter_log.md
#   bug_fixes/SUMMARY.md          -- combined summary table
#
# Cache: hash DB in bug_fixes/.bughunt_iter_state/hashes.tsv
#   Source entries:  <sha> TAB <rel>
#   Test entries:    <sha> TAB test:<testRel>
#
# Usage:
#   .\LocalLLMDebug\bughunt_iterative_local.ps1 [-TargetDir <path>]
#       [-TestDir <path>] [-MaxIterations <n>] [-ApplyFixes]
#       [-SkipBugs] [-SkipDataflow] [-SkipContracts] [-SkipTests]
#       [-Clean] [-Force]
# ============================================================

[CmdletBinding()]
param(
    [string]$TargetDir     = ".",
    [string]$TestDir       = "tests",
    [int]   $MaxIterations = 3,
    [switch]$ApplyFixes,
    [switch]$SkipBugs,
    [switch]$SkipDataflow,
    [switch]$SkipContracts,
    [switch]$SkipTests,
    [switch]$Clean,
    [switch]$Force,
    [string]$EnvFile       = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($EnvFile -eq "") { $EnvFile = Join-Path $PSScriptRoot '..\Common\.env' }

# ── Guard: at least one analysis type must be active ─────────

if ($SkipBugs -and $SkipDataflow -and $SkipContracts -and $SkipTests) {
    Write-Host "ERROR: all analysis types skipped (-SkipBugs -SkipDataflow -SkipContracts -SkipTests). Nothing to do." -ForegroundColor Red
    exit 1
}

$runSourceLoop = -not ($SkipBugs -and $SkipDataflow -and $SkipContracts)

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

$llmEndpoint      = Get-LLMEndpoint
# Model resolution via Get-LLMModel:
#   LLM_MODEL -> LLM_DEFAULT_MODEL -> 'qwen3-coder:30b' fallback.
# (The former LLM_MODEL_HIGH_CTX preference is retired; per-request num_ctx
# handles the 12k fix-call window.)
$llmModel         = Get-LLMModel -RoleKey 'LLM_MODEL'
$llmTemperature   = [double](Cfg 'LLM_TEMPERATURE'   '0.1')
$llmTimeout       = [int](Cfg 'LLM_TIMEOUT'          '300')   # 4000-token fix calls on a 32B model regularly exceed 120s
$analyseMaxTokens = [int](Cfg 'BUGHUNT_ANALYSE_TOKENS' '900')
$fixMaxTokens     = [int](Cfg 'BUGHUNT_FIX_TOKENS'    '4000')

# Convergence guards ─────────────────────────────────────────
# Reject runaway LLM fixes. See header comment for rationale.
# Tuned against qwen2.5-coder:32b: legitimate fixes hover at 25-35% growth,
# hallucinated bulk is 100%+. 1.5 (50%) is comfortably between.
$bloatRatio    = [double](Cfg 'BUGHUNT_BLOAT_RATIO'  '1.5')    # reject fix if file grows beyond this ratio vs. original
$bloatMinSlack = [int](Cfg 'BUGHUNT_BLOAT_MIN_SLACK' '15')     # absolute line floor so tiny files aren't over-constrained
$divergeAfter  = [int](Cfg 'BUGHUNT_DIVERGE_AFTER'   '2')      # abort if HIGH fails to improve for N consecutive iterations

# ── Paths ────────────────────────────────────────────────────

$repoRoot = (Get-Location).Path
try {
    $g = & git rev-parse --show-toplevel 2>$null
    if ($LASTEXITCODE -eq 0 -and $g) { $repoRoot = $g.Trim() }
} catch {}

$fixDir   = Join-Path $repoRoot 'bug_fixes'
$stateDir = Join-Path $fixDir   '.bughunt_iter_state'

foreach ($d in @($fixDir, $stateDir)) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}

$hashDbPath = Join-Path $stateDir 'hashes.tsv'
$errorLog   = Join-Path $stateDir 'last_error.log'
$summaryLog = Join-Path $fixDir   'SUMMARY.md'

$testRoot = Join-Path $repoRoot $TestDir

# Backtick literal for building code-fence strings in here-strings
$tb = '```'

# ── Load prompt files ────────────────────────────────────────

$analysePromptFile  = Join-Path $PSScriptRoot 'bughunt_prompt.txt'
$fixPromptFile      = Join-Path $PSScriptRoot 'bughunt_fix_prompt.txt'
$dataflowPromptFile = Join-Path $PSScriptRoot 'bughunt_dataflow_prompt.txt'
$contractsPromptFile = Join-Path $PSScriptRoot 'bughunt_contracts_prompt.txt'
$testsPromptFile    = Join-Path $PSScriptRoot 'bughunt_tests_prompt.txt'

foreach ($f in @($analysePromptFile, $fixPromptFile)) {
    if (-not (Test-Path $f)) { Write-Host "Missing required prompt file: $f" -ForegroundColor Red; exit 2 }
}
if (-not $SkipDataflow  -and -not (Test-Path $dataflowPromptFile))  { Write-Host "Missing prompt: $dataflowPromptFile"  -ForegroundColor Red; exit 2 }
if (-not $SkipContracts -and -not (Test-Path $contractsPromptFile)) { Write-Host "Missing prompt: $contractsPromptFile" -ForegroundColor Red; exit 2 }
if (-not $SkipTests     -and -not (Test-Path $testsPromptFile))     { Write-Host "Missing prompt: $testsPromptFile"     -ForegroundColor Red; exit 2 }

$analysePromptSchema  = Get-Content $analysePromptFile  -Raw
$fixPromptSchema      = Get-Content $fixPromptFile      -Raw
$dataflowPromptSchema = if (-not $SkipDataflow)  { Get-Content $dataflowPromptFile  -Raw } else { '' }
$contractsPromptSchema= if (-not $SkipContracts) { Get-Content $contractsPromptFile -Raw } else { '' }
$testsPromptSchema    = if (-not $SkipTests)     { Get-Content $testsPromptFile     -Raw } else { '' }

# ── System prompts ───────────────────────────────────────────

$analyseSysPrompt  = "You are a senior engineer reviewing source files from a $codebaseDesc. Find bugs, not style issues. Follow the output schema exactly."
$dataflowSysPrompt = "You are a senior engineer reviewing data flow in source files from a $codebaseDesc. Find data flow bugs only. Follow the output schema exactly."
$contractsSysPrompt= "You are a senior engineer reviewing interface contracts in source files from a $codebaseDesc. Find contract violations only. Follow the output schema exactly."
$testsSysPrompt    = "You are a senior engineer reviewing test quality in a $codebaseDesc. Find test bugs that allow production defects to go undetected. Follow the output schema exactly."
$fixSysPrompt      = "You are a senior engineer fixing bugs in source files from a $codebaseDesc. Follow the output rules exactly."

# ── Clean ────────────────────────────────────────────────────

if ($Clean) {
    Write-Host "CLEAN: removing bug_fixes and state..." -ForegroundColor Cyan
    Remove-Item -Path $fixDir -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $fixDir   | Out-Null
    New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
}

'' | Set-Content $errorLog -Encoding UTF8
if (-not (Test-Path $hashDbPath)) { '' | Set-Content $hashDbPath -Encoding UTF8 }

# ── Hash DB ──────────────────────────────────────────────────

$oldSha     = @{}   # srcRel  -> sha
$oldTestSha = @{}   # testRel -> sha

if (-not $Force) {
    Get-Content $hashDbPath | ForEach-Object {
        $parts = $_ -split "`t", 2
        if ($parts.Count -eq 2 -and $parts[1] -ne '') {
            if ($parts[1].StartsWith('test:')) {
                $oldTestSha[$parts[1].Substring(5)] = $parts[0]
            } else {
                $oldSha[$parts[1]] = $parts[0]
            }
        }
    }
}

# ── Helpers ──────────────────────────────────────────────────

function Count-Severity($text, $tag) {
    return ([regex]::Matches($text, [regex]::Escape($tag))).Count
}

function Needs-Fix($report) {
    return (Count-Severity $report '[HIGH]') -gt 0 -or (Count-Severity $report '[MEDIUM]') -gt 0
}

function Extract-CodeBlock($response, $fence) {
    $attempts = @($fence, 'python', '')
    foreach ($lang in $attempts) {
        $openFence = if ($lang -ne '') { "${tb}${lang}" } else { $tb }
        $startIdx  = $response.IndexOf($openFence)
        if ($startIdx -lt 0) { continue }
        $lineEnd = $response.IndexOf("`n", $startIdx)
        if ($lineEnd -lt 0) { continue }
        $contentStart = $lineEnd + 1
        $endIdx = $response.IndexOf($tb, $contentStart)
        if ($endIdx -le $contentStart) { continue }
        $code = $response.Substring($contentStart, $endIdx - $contentStart).TrimEnd("`n", "`r", " ")
        if ($code.Trim() -ne '') { return $code }
    }
    return ''
}

function Test-PythonSyntax($code) {
    # Under $ErrorActionPreference='Stop', any stderr from a native command
    # is escalated to a terminating error before we can check $LASTEXITCODE.
    # py_compile writes diagnostics to stderr on syntax failure, which is
    # exactly the path we need to survive — so we (a) suppress stderr with
    # 2>$null, (b) locally drop EAP to Continue, and (c) catch anything that
    # still slips through and treat it as "syntax invalid".
    $tmpFile = [System.IO.Path]::GetTempFileName() + '.py'
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        [System.IO.File]::WriteAllText($tmpFile, $code, [System.Text.Encoding]::UTF8)
        try {
            & python -m py_compile $tmpFile 2>$null | Out-Null
        } catch {
            return $false
        }
        return ($LASTEXITCODE -eq 0)
    } finally {
        $ErrorActionPreference = $prevEAP
        Remove-Item $tmpFile -ErrorAction SilentlyContinue
    }
}

function Should-SyntaxCheck($rel) {
    return $rel -match '\.py$'
}

# Find a test file matching a source file. Returns full path or empty string.
function Find-TestFile($srcRel) {
    $stem  = [System.IO.Path]::GetFileNameWithoutExtension($srcRel)
    $dir   = ([System.IO.Path]::GetDirectoryName($srcRel) -replace '\\', '/').Trim('/')
    # Strip leading src/<package> path segments to get meaningful name parts
    $parts = @($dir -split '/' | Where-Object { $_ -ne '' -and $_ -ne 'src' })

    # Candidate 1: test_ + all remaining path parts + stem
    $c1 = Join-Path $testRoot ('test_' + (($parts + @($stem)) -join '_') + '.py')
    # Candidate 2: test_ + stem only
    $c2 = Join-Path $testRoot ('test_' + $stem + '.py')
    # Candidate 3: strip common suffix words, rejoin
    $stem3 = $stem -replace '_(source|base|impl|provider|backend)$', ''
    $c3 = Join-Path $testRoot ('test_' + (($parts + @($stem3)) -join '_') + '.py')

    foreach ($c in @($c1, $c2, $c3)) {
        if (Test-Path $c) { return $c }
    }
    return ''
}

# ── Collect source files (exclude test directory) ────────────

$scanRoot = if ($TargetDir -eq '.') { $repoRoot } else { Join-Path $repoRoot $TargetDir }
if (-not (Test-Path $scanRoot)) {
    Write-Host "Target directory not found: $scanRoot" -ForegroundColor Red
    exit 1
}

$testDirRel = ($TestDir -replace '\\', '/').Trim('/')

$allFiles = Get-ChildItem -Path $scanRoot -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object {
        $rel = $_.FullName.Substring($repoRoot.Length).TrimStart('\', '/') -replace '\\', '/'
        if ($rel -match '^(architecture|bug_reports|bug_fixes)/' -or
            $rel -match '/(architecture|bug_reports|bug_fixes)/') { return $false }
        if ($testDirRel -ne '' -and $rel -match ('^' + [regex]::Escape($testDirRel) + '/')) { return $false }
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
if ($total -eq 0 -and $runSourceLoop) {
    Write-Host "No matching source files found under '$scanRoot'" -ForegroundColor Red
    exit 1
}

# ── Build queue and discover test files ──────────────────────

$queue         = [System.Collections.Generic.List[string]]::new()
$skipUnchanged = 0
$testFileMap   = @{}   # srcRel -> testRel (relative path)

foreach ($rel in $files) {
    $src    = Join-Path $repoRoot ($rel -replace '/', '\')
    $logOut = Join-Path $fixDir   (($rel -replace '/', '\') + '.iter_log.md')
    $sha    = Get-SHA1 $src

    if ($runSourceLoop) {
        if ($oldSha.ContainsKey($rel) -and $oldSha[$rel] -eq $sha -and (Test-Path $logOut)) {
            $skipUnchanged++
        } else {
            $queue.Add($rel)
        }
    } else {
        $queue.Add($rel)   # need queue to drive test discovery even if source loop skipped
    }

    # Test file discovery (always - needed for test loop)
    if (-not $SkipTests) {
        $testFullPath = Find-TestFile $rel
        if ($testFullPath -ne '') {
            $testRel = $testFullPath.Substring($repoRoot.Length).TrimStart('\', '/') -replace '\\', '/'
            $testFileMap[$rel] = $testRel
        }
    }
}

$toDo = $queue.Count

# ── Determine enabled analysis type labels for display ───────

$enabledSrc = @()
if (-not $SkipBugs)      { $enabledSrc += 'bugs'      }
if (-not $SkipDataflow)  { $enabledSrc += 'dataflow'  }
if (-not $SkipContracts) { $enabledSrc += 'contracts' }
$srcTypeStr = if ($enabledSrc.Count -gt 0) { $enabledSrc -join ', ' } else { '(none)' }
$testTypeStr = if (-not $SkipTests) { 'tests' } else { '(skipped)' }

# ── Banner ───────────────────────────────────────────────────

Write-Host '============================================'         -ForegroundColor Cyan
Write-Host '  bughunt_iterative_local.ps1'                        -ForegroundColor Cyan
Write-Host '  Iterative Bug Hunt + Auto-Fix'                      -ForegroundColor Cyan
Write-Host '============================================'         -ForegroundColor Cyan
Write-Host "Repo root:       $repoRoot"
Write-Host "Codebase:        $codebaseDesc"
Write-Host "Target:          $TargetDir"
Write-Host "Test dir:        $TestDir"
Write-Host "LLM:             $llmModel @ $llmEndpoint"
Write-Host "LLM timeout:     ${llmTimeout}s per request"
Write-Host "Max iterations:  $MaxIterations"
Write-Host "Source analyses: $srcTypeStr"
Write-Host "Test analysis:   $testTypeStr"
Write-Host "Analyse tokens:  $analyseMaxTokens"
Write-Host "Fix tokens:      $fixMaxTokens"
Write-Host "Bloat ratio:     $bloatRatio (min slack: +$bloatMinSlack lines)"
Write-Host "Diverge after:   $divergeAfter non-improving iterations"
Write-Host "Source files:    $total total  |  unchanged=$skipUnchanged  |  process: $toDo"
Write-Host "Test files:      $($testFileMap.Count) matched"
Write-Host "Output dir:      $fixDir"
$applyMode = if ($ApplyFixes) { 'YES -- fixes will be written back to source' } else { 'NO  -- fixes staged in bug_fixes/ only (use -ApplyFixes to write back)' }
Write-Host "Apply to source: $applyMode" -ForegroundColor $(if ($ApplyFixes) { 'Yellow' } else { 'Cyan' })
Write-Host ''
Write-Host 'Press Ctrl+Q to cancel (checked between iterations; mid-LLM-call use Ctrl+C).' -ForegroundColor DarkGray
Write-Host ''

if ($toDo -eq 0 -and $testFileMap.Count -eq 0) {
    Write-Host 'Nothing to do. All outputs are up to date.' -ForegroundColor Green
    exit 0
}

# ── Per-file tracking for SUMMARY ────────────────────────────

# PSCustomObject (not hashtable) so `Measure-Object -Property X` works in
# Windows PowerShell 5.x — it only walks real object properties, not hash keys.
$fileResults = [System.Collections.Generic.List[psobject]]::new()
$testResults = [System.Collections.Generic.List[psobject]]::new()
$globalFailed = 0

# ── Source loop ───────────────────────────────────────────────

if ($runSourceLoop -and $toDo -gt 0) {
    Write-Host "Source file loop ($toDo files)..." -ForegroundColor Yellow

    $fileIdx = 0
    foreach ($rel in $queue) {
        Test-CancelKey
        $fileIdx++
        $src      = Join-Path $repoRoot ($rel -replace '/', '\')
        $fixedOut = Join-Path $fixDir   ($rel -replace '/', '\')
        $logOut   = Join-Path $fixDir   (($rel -replace '/', '\') + '.iter_log.md')
        $fence    = Get-FenceLang $rel $defaultFence

        Write-Host ''
        Write-Host "[$fileIdx/$toDo] $rel" -ForegroundColor Yellow

        $outDir = Split-Path $fixedOut -Parent
        New-Item -ItemType Directory -Force -Path $outDir | Out-Null

        $origLines       = @(Get-Content $src -ErrorAction SilentlyContinue)
        $origContent     = $origLines -join "`n"
        $origLineCount   = $origLines.Count
        $maxAllowedLines = [math]::Max([int][math]::Ceiling($origLineCount * $bloatRatio), $origLineCount + $bloatMinSlack)
        $workingContent  = $origContent
        $bestContent     = $origContent
        $bestHigh        = 0
        $bestMed         = 0
        $bestLow         = 0
        $bestIter        = 0
        $bestSet         = $false
        $nonImprovingStreak = 0
        $iterIdx         = 0
        $status          = 'IN_PROGRESS'
        $finalHigh       = 0
        $finalMed        = 0
        $finalLow        = 0
        $changed         = $false

        $logLines = [System.Collections.Generic.List[string]]::new()
        $logLines.Add("# Iteration Log: $rel")
        $logLines.Add('')
        $logLines.Add("Codebase: $codebaseDesc")
        $logLines.Add("Analysis: $srcTypeStr")
        $logLines.Add("Max iterations: $MaxIterations")
        $logLines.Add("Started: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
        $logLines.Add('')

        # ── Iteration loop ───────────────────────────────────
        while ($iterIdx -lt $MaxIterations -and $status -eq 'IN_PROGRESS') {
            Test-CancelKey
            $iterIdx++

            $workingLines = @($workingContent -split "`n")
            $truncated    = Truncate-Source $workingLines $maxFileLines

            Write-Host ("  Iter {0}/{1}: analysing ({2} lines)" -f $iterIdx, $MaxIterations, $workingLines.Count)

            $reportParts = [System.Collections.Generic.List[string]]::new()

            # --- Bug analysis ---
            if (-not $SkipBugs) {
                Write-Host "    [bugs]..." -NoNewline
                $prompt = @"
$analysePromptSchema

FILE PATH: $rel

FILE CONTENT ($($workingLines.Count) lines):
${tb}$fence
$truncated
${tb}
"@
                try {
                    $r = Invoke-LocalLLM `
                        -SystemPrompt $analyseSysPrompt -UserPrompt $prompt `
                        -Endpoint $llmEndpoint -Model $llmModel `
                        -Temperature $llmTemperature -MaxTokens $analyseMaxTokens -Timeout $llmTimeout
                    $h = Count-Severity $r '[HIGH]'
                    $m = Count-Severity $r '[MEDIUM]'
                    $l = Count-Severity $r '[LOW]'
                    $col = if ($h -gt 0) { 'Red' } elseif ($m -gt 0) { 'Yellow' } else { 'Green' }
                    Write-Host (" H:$h M:$m L:$l") -ForegroundColor $col
                    $reportParts.Add("## Bug Analysis`n`n$r")
                } catch {
                    [System.IO.File]::AppendAllText($errorLog, "$(Get-Date -Format u) | BUGS FAIL | $rel iter $iterIdx | $($_.Exception.Message)`n")
                    Write-Host " [FAIL]" -ForegroundColor Red
                }
            }

            # --- Dataflow analysis ---
            if (-not $SkipDataflow) {
                Write-Host "    [dataflow]..." -NoNewline
                $prompt = @"
$dataflowPromptSchema

FILE PATH: $rel

FILE CONTENT ($($workingLines.Count) lines):
${tb}$fence
$truncated
${tb}
"@
                try {
                    $r = Invoke-LocalLLM `
                        -SystemPrompt $dataflowSysPrompt -UserPrompt $prompt `
                        -Endpoint $llmEndpoint -Model $llmModel `
                        -Temperature $llmTemperature -MaxTokens $analyseMaxTokens -Timeout $llmTimeout
                    $h = Count-Severity $r '[HIGH]'
                    $m = Count-Severity $r '[MEDIUM]'
                    $l = Count-Severity $r '[LOW]'
                    $col = if ($h -gt 0) { 'Red' } elseif ($m -gt 0) { 'Yellow' } else { 'Green' }
                    Write-Host (" H:$h M:$m L:$l") -ForegroundColor $col
                    $reportParts.Add("## Data Flow Analysis`n`n$r")
                } catch {
                    [System.IO.File]::AppendAllText($errorLog, "$(Get-Date -Format u) | DF FAIL | $rel iter $iterIdx | $($_.Exception.Message)`n")
                    Write-Host " [FAIL]" -ForegroundColor Red
                }
            }

            # --- Contract analysis ---
            if (-not $SkipContracts) {
                Write-Host "    [contracts]..." -NoNewline
                $prompt = @"
$contractsPromptSchema

FILE PATH: $rel

FILE CONTENT ($($workingLines.Count) lines):
${tb}$fence
$truncated
${tb}
"@
                try {
                    $r = Invoke-LocalLLM `
                        -SystemPrompt $contractsSysPrompt -UserPrompt $prompt `
                        -Endpoint $llmEndpoint -Model $llmModel `
                        -Temperature $llmTemperature -MaxTokens $analyseMaxTokens -Timeout $llmTimeout
                    $h = Count-Severity $r '[HIGH]'
                    $m = Count-Severity $r '[MEDIUM]'
                    $l = Count-Severity $r '[LOW]'
                    $col = if ($h -gt 0) { 'Red' } elseif ($m -gt 0) { 'Yellow' } else { 'Green' }
                    Write-Host (" H:$h M:$m L:$l") -ForegroundColor $col
                    $reportParts.Add("## Contract Analysis`n`n$r")
                } catch {
                    [System.IO.File]::AppendAllText($errorLog, "$(Get-Date -Format u) | CTR FAIL | $rel iter $iterIdx | $($_.Exception.Message)`n")
                    Write-Host " [FAIL]" -ForegroundColor Red
                }
            }

            # Abort if all analysis types failed this iteration
            if ($reportParts.Count -eq 0) {
                $logLines.Add("## Iteration $iterIdx")
                $logLines.Add('')
                $logLines.Add("**All analysis types failed (LLM errors). Aborting.**")
                $logLines.Add('')
                $status = 'ERROR'
                $globalFailed++
                break
            }

            $combinedReport = $reportParts -join "`n`n---`n`n"

            $iterHigh = Count-Severity $combinedReport '[HIGH]'
            $iterMed  = Count-Severity $combinedReport '[MEDIUM]'
            $iterLow  = Count-Severity $combinedReport '[LOW]'
            $finalHigh = $iterHigh
            $finalMed  = $iterMed
            $finalLow  = $iterLow

            # Best-version tracking: lowest HIGH, tie-broken by lowest MED.
            # We revert to this on DIVERGING / MAX_ITER / BLOAT / rejected-fix statuses.
            $improved = $false
            if (-not $bestSet) {
                $bestSet  = $true
                $improved = $true
            } elseif ($iterHigh -lt $bestHigh -or ($iterHigh -eq $bestHigh -and $iterMed -lt $bestMed)) {
                $improved = $true
            }
            if ($improved) {
                $bestContent = $workingContent
                $bestHigh    = $iterHigh
                $bestMed     = $iterMed
                $bestLow     = $iterLow
                $bestIter    = $iterIdx
                $nonImprovingStreak = 0
            } else {
                $nonImprovingStreak++
            }

            $severityStr = "HIGH:$iterHigh  MED:$iterMed  LOW:$iterLow"
            $bestStr     = "best@iter ${bestIter}: H:$bestHigh M:$bestMed"
            $sumColor = if ($iterHigh -gt 0) { 'Red' } elseif ($iterMed -gt 0) { 'Yellow' } else { 'Green' }
            Write-Host ("    Combined: $severityStr  ($bestStr)") -ForegroundColor $sumColor

            $logLines.Add("## Iteration $iterIdx")
            $logLines.Add('')
            $logLines.Add("**Severity:** $severityStr")
            $logLines.Add("**Best so far:** iter $bestIter (HIGH:$bestHigh MED:$bestMed)")
            if (-not $improved) {
                $logLines.Add("**Non-improving streak:** $nonImprovingStreak / $divergeAfter")
            }
            $logLines.Add('')
            $logLines.Add($combinedReport)
            $logLines.Add('')

            # Stop if clean
            if (-not (Needs-Fix $combinedReport)) {
                $status = 'CLEAN'
                $logLines.Add("*Stopped: no HIGH or MEDIUM findings.*")
                $logLines.Add('')
                break
            }

            # Stop if diverging (HIGH failed to improve for N consecutive iterations)
            if ($nonImprovingStreak -ge $divergeAfter) {
                $status = 'DIVERGING'
                Write-Host ("    DIVERGING: no improvement for $nonImprovingStreak iterations. Reverting to iter $bestIter.") -ForegroundColor Red
                $logLines.Add("*Stopped: DIVERGING. HIGH did not improve for $nonImprovingStreak consecutive iterations. Reverting to best version (iter $bestIter, HIGH:$bestHigh MED:$bestMed).*")
                $logLines.Add('')
                break
            }

            # Stop if last iteration
            if ($iterIdx -ge $MaxIterations) {
                $status = 'MAX_ITER'
                $logLines.Add("*Stopped: reached MaxIterations ($MaxIterations). Reverting to best version (iter $bestIter, HIGH:$bestHigh MED:$bestMed).*")
                $logLines.Add('')
                break
            }

            # Fix
            Write-Host ("  Iter {0}/{1}: fixing..." -f $iterIdx, $MaxIterations) -NoNewline

            $fixPrompt = @"
$fixPromptSchema

BUG REPORT:
$combinedReport

FILE PATH: $rel

CURRENT SOURCE ($($workingLines.Count) lines):
${tb}$fence
$truncated
${tb}
"@

            $fixResponse = ''
            try {
                $fixResponse = Invoke-LocalLLM `
                    -SystemPrompt $fixSysPrompt -UserPrompt $fixPrompt `
                    -Endpoint $llmEndpoint -Model $llmModel `
                    -Temperature $llmTemperature -MaxTokens $fixMaxTokens -Timeout $llmTimeout
            } catch {
                [System.IO.File]::AppendAllText($errorLog, "$(Get-Date -Format u) | FIX FAIL | $rel iter $iterIdx | $($_.Exception.Message)`n")
                Write-Host " [LLM ERROR]" -ForegroundColor Red
                $logLines.Add("**Fix failed (LLM error):** $($_.Exception.Message)")
                $logLines.Add('')
                $status = 'ERROR'
                $globalFailed++
                break
            }

            $fixedCode = Extract-CodeBlock $fixResponse $fence

            if ($fixedCode -eq '') {
                Write-Host " [NO CODE BLOCK]" -ForegroundColor Red
                $logLines.Add("**Fix rejected:** LLM response contained no valid code block.")
                $logLines.Add('')
                $status = 'STUCK'
                break
            }

            if ($fixedCode -eq $workingContent) {
                Write-Host " [NO CHANGE]" -ForegroundColor Yellow
                $logLines.Add("**Fix rejected:** LLM returned identical source. No progress possible.")
                $logLines.Add('')
                $status = 'STUCK'
                break
            }

            # Bloat guard: reject fixes that balloon the file beyond the allowed ratio
            $fixedLineCount = @($fixedCode -split "`n").Count
            if ($fixedLineCount -gt $maxAllowedLines) {
                Write-Host (" [BLOAT {0}>{1} lines]" -f $fixedLineCount, $maxAllowedLines) -ForegroundColor Red
                $logLines.Add("**Fix rejected:** Fixed code grew to $fixedLineCount lines (original: $origLineCount, limit: $maxAllowedLines). LLM is adding too much code. Reverting to best version.")
                $logLines.Add('')
                $status = 'BLOAT'
                break
            }

            if (Should-SyntaxCheck $rel) {
                if (-not (Test-PythonSyntax $fixedCode)) {
                    Write-Host " [SYNTAX ERROR]" -ForegroundColor Red
                    $logLines.Add("**Fix rejected:** Fixed code failed Python syntax check.")
                    $logLines.Add('')
                    $status = 'SYNTAX_ERR'
                    break
                }
            }

            $linesAfter = @($fixedCode -split "`n").Count
            Write-Host (" applied ({0} -> {1} lines)" -f $workingLines.Count, $linesAfter) -ForegroundColor Green
            $logLines.Add("**Fix applied.** Lines: $($workingLines.Count) -> $linesAfter")
            $logLines.Add('')

            $workingContent = $fixedCode
            $changed        = $true
        }

        # Collapse to best version seen across all iterations.
        # For CLEAN runs this is the final (clean) content; for MAX_ITER /
        # DIVERGING / BLOAT / STUCK it is whichever earlier iteration had the
        # lowest HIGH count.
        $finalContent = $bestContent
        $finalHigh    = $bestHigh
        $finalMed     = $bestMed
        $finalLow     = $bestLow
        $changed      = ($finalContent -ne $origContent)

        # Write fixed file
        $applied = $false
        if ($changed) {
            [System.IO.File]::WriteAllText($fixedOut, $finalContent, [System.Text.Encoding]::UTF8)
            if ($ApplyFixes) {
                [System.IO.File]::WriteAllText($src, $finalContent, [System.Text.Encoding]::UTF8)
                $applied = $true
                Write-Host ("  -> Applied best version (iter $bestIter) to source: $rel") -ForegroundColor Yellow
            }
        }

        # Write iteration log
        $logLines.Add("---")
        $logLines.Add('')
        $logLines.Add("**Final status:** $status")
        $logLines.Add("**Iterations run:** $iterIdx")
        $logLines.Add("**Best iteration:** $bestIter (HIGH:$bestHigh MED:$bestMed LOW:$bestLow)")
        $logLines.Add("**Remaining (best):** HIGH:$finalHigh  MED:$finalMed  LOW:$finalLow")
        if ($changed) {
            $fixedRel = $fixedOut.Substring($repoRoot.Length).TrimStart('\', '/') -replace '\\', '/'
            $logLines.Add("**Fixed file:** ``$fixedRel`` (content from iteration $bestIter)")
            if ($applied) {
                $logLines.Add("**Written back to source:** yes (``-ApplyFixes``)")
            } else {
                $logLines.Add("**Written back to source:** no (review ``$fixedRel`` and copy manually)")
            }
        } else {
            $logLines.Add("**Fixed file:** none (best version is identical to original)")
        }

        ($logLines -join "`n") | Set-Content $logOut -Encoding UTF8

        # Record hash
        $origSha = Get-SHA1 $src
        [System.IO.File]::AppendAllText($hashDbPath, "$origSha`t$rel`n")

        $fileResults.Add([PSCustomObject]@{
            Rel        = $rel
            Iterations = $iterIdx
            Status     = $status
            FinalHigh  = $finalHigh
            FinalMed   = $finalMed
            FinalLow   = $finalLow
            Changed    = $changed
            Applied    = $applied
        })

        $statusColor = switch ($status) {
            'CLEAN'      { 'Green'  }
            'MAX_ITER'   { 'Red'    }
            'STUCK'      { 'Yellow' }
            'SYNTAX_ERR' { 'Red'    }
            'ERROR'      { 'Red'    }
            'DIVERGING'  { 'Red'    }
            'BLOAT'      { 'Red'    }
            default      { 'White'  }
        }
        Write-Host ("  -> {0} after {1} iteration(s)  HIGH:{2}  MED:{3}  LOW:{4}" -f
            $status, $iterIdx, $finalHigh, $finalMed, $finalLow) -ForegroundColor $statusColor
    }
}

# ── Test loop ─────────────────────────────────────────────────

if (-not $SkipTests -and $testFileMap.Count -gt 0) {
    Write-Host ''
    Write-Host "Test file loop..." -ForegroundColor Yellow

    $processedTests = @{}
    $testIdx   = 0
    $testTotal = @($testFileMap.Values | Sort-Object -Unique).Count

    foreach ($srcRel in $queue) {
        Test-CancelKey
        if (-not $testFileMap.ContainsKey($srcRel)) { continue }
        $testRel = $testFileMap[$srcRel]
        if ($processedTests.ContainsKey($testRel)) { continue }
        $processedTests[$testRel] = $true
        $testIdx++

        $srcPath    = Join-Path $repoRoot ($srcRel  -replace '/', '\')
        $testPath   = Join-Path $repoRoot ($testRel -replace '/', '\')
        $testFixOut = Join-Path $fixDir   ($testRel -replace '/', '\')
        $testLogOut = Join-Path $fixDir   (($testRel -replace '/', '\') + '.iter_log.md')
        $testFence  = Get-FenceLang $testRel $defaultFence

        # Skip if test file unchanged and already processed
        $testSha = Get-SHA1 $testPath
        if ($oldTestSha.ContainsKey($testRel) -and $oldTestSha[$testRel] -eq $testSha -and (Test-Path $testLogOut)) {
            Write-Host ''
            Write-Host "[$testIdx/$testTotal] (test) $testRel [cached]" -ForegroundColor DarkGray
            continue
        }

        Write-Host ''
        Write-Host "[$testIdx/$testTotal] (test) $testRel" -ForegroundColor Cyan
        Write-Host "  Source: $srcRel"

        $testOutDir = Split-Path $testFixOut -Parent
        New-Item -ItemType Directory -Force -Path $testOutDir | Out-Null

        $srcLines            = @(Get-Content $srcPath -ErrorAction SilentlyContinue)
        $testOrigLines       = @(Get-Content $testPath -ErrorAction SilentlyContinue)
        $testOrigContent     = $testOrigLines -join "`n"
        $testOrigLineCount   = $testOrigLines.Count
        $testMaxAllowedLines = [math]::Max([int][math]::Ceiling($testOrigLineCount * $bloatRatio), $testOrigLineCount + $bloatMinSlack)
        $testWorkingContent  = $testOrigContent
        $testBestContent     = $testOrigContent
        $testBestHigh        = 0
        $testBestMed         = 0
        $testBestLow         = 0
        $testBestIter        = 0
        $testBestSet         = $false
        $testNonImprovingStreak = 0
        $srcTruncated        = Truncate-Source $srcLines $maxFileLines

        $testIterIdx  = 0
        $testStatus   = 'IN_PROGRESS'
        $testFinalHigh = 0
        $testFinalMed  = 0
        $testFinalLow  = 0
        $testChanged   = $false

        $testLogLines = [System.Collections.Generic.List[string]]::new()
        $testLogLines.Add("# Test Iteration Log: $testRel")
        $testLogLines.Add('')
        $testLogLines.Add("Source file: $srcRel")
        $testLogLines.Add("Max iterations: $MaxIterations")
        $testLogLines.Add("Started: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
        $testLogLines.Add('')

        while ($testIterIdx -lt $MaxIterations -and $testStatus -eq 'IN_PROGRESS') {
            Test-CancelKey
            $testIterIdx++

            $testWorkingLines = @($testWorkingContent -split "`n")
            $testTruncated    = Truncate-Source $testWorkingLines $maxFileLines

            Write-Host ("  Iter {0}/{1}: [tests] analysing ({2} lines)..." -f $testIterIdx, $MaxIterations, $testWorkingLines.Count) -NoNewline

            $testPrompt = @"
$testsPromptSchema

SOURCE FILE PATH: $srcRel

SOURCE CONTENT ($($srcLines.Count) lines):
${tb}python
$srcTruncated
${tb}

TEST FILE PATH: $testRel

TEST CONTENT ($($testWorkingLines.Count) lines):
${tb}$testFence
$testTruncated
${tb}
"@

            $testReport = ''
            try {
                $testReport = Invoke-LocalLLM `
                    -SystemPrompt $testsSysPrompt -UserPrompt $testPrompt `
                    -Endpoint $llmEndpoint -Model $llmModel `
                    -Temperature $llmTemperature -MaxTokens $analyseMaxTokens -Timeout $llmTimeout
            } catch {
                [System.IO.File]::AppendAllText($errorLog, "$(Get-Date -Format u) | TEST FAIL | $testRel iter $testIterIdx | $($_.Exception.Message)`n")
                Write-Host " [LLM ERROR]" -ForegroundColor Red
                $testLogLines.Add("## Iteration $testIterIdx")
                $testLogLines.Add('')
                $testLogLines.Add("**Analysis failed:** $($_.Exception.Message)")
                $testLogLines.Add('')
                $testStatus = 'ERROR'
                $globalFailed++
                break
            }

            $tH = Count-Severity $testReport '[HIGH]'
            $tM = Count-Severity $testReport '[MEDIUM]'
            $tL = Count-Severity $testReport '[LOW]'
            $testFinalHigh = $tH
            $testFinalMed  = $tM
            $testFinalLow  = $tL

            # Best-version tracking (same logic as source loop)
            $testImproved = $false
            if (-not $testBestSet) {
                $testBestSet  = $true
                $testImproved = $true
            } elseif ($tH -lt $testBestHigh -or ($tH -eq $testBestHigh -and $tM -lt $testBestMed)) {
                $testImproved = $true
            }
            if ($testImproved) {
                $testBestContent = $testWorkingContent
                $testBestHigh    = $tH
                $testBestMed     = $tM
                $testBestLow     = $tL
                $testBestIter    = $testIterIdx
                $testNonImprovingStreak = 0
            } else {
                $testNonImprovingStreak++
            }

            $tCol = if ($tH -gt 0) { 'Red' } elseif ($tM -gt 0) { 'Yellow' } else { 'Green' }
            Write-Host (" H:$tH M:$tM L:$tL  (best@iter ${testBestIter}: H:$testBestHigh M:$testBestMed)") -ForegroundColor $tCol

            $testLogLines.Add("## Iteration $testIterIdx")
            $testLogLines.Add('')
            $testLogLines.Add("**Severity:** HIGH:$tH  MED:$tM  LOW:$tL")
            $testLogLines.Add("**Best so far:** iter $testBestIter (HIGH:$testBestHigh MED:$testBestMed)")
            if (-not $testImproved) {
                $testLogLines.Add("**Non-improving streak:** $testNonImprovingStreak / $divergeAfter")
            }
            $testLogLines.Add('')
            $testLogLines.Add($testReport)
            $testLogLines.Add('')

            if (-not (Needs-Fix $testReport)) {
                $testStatus = 'CLEAN'
                $testLogLines.Add("*Stopped: no HIGH or MEDIUM findings.*")
                $testLogLines.Add('')
                break
            }

            # Stop if diverging
            if ($testNonImprovingStreak -ge $divergeAfter) {
                $testStatus = 'DIVERGING'
                Write-Host ("  DIVERGING: no improvement for $testNonImprovingStreak iterations. Reverting to iter $testBestIter.") -ForegroundColor Red
                $testLogLines.Add("*Stopped: DIVERGING. HIGH did not improve for $testNonImprovingStreak consecutive iterations. Reverting to best version (iter $testBestIter, HIGH:$testBestHigh MED:$testBestMed).*")
                $testLogLines.Add('')
                break
            }

            if ($testIterIdx -ge $MaxIterations) {
                $testStatus = 'MAX_ITER'
                $testLogLines.Add("*Stopped: reached MaxIterations ($MaxIterations). Reverting to best version (iter $testBestIter, HIGH:$testBestHigh MED:$testBestMed).*")
                $testLogLines.Add('')
                break
            }

            # Fix the test file
            Write-Host ("  Iter {0}/{1}: fixing test..." -f $testIterIdx, $MaxIterations) -NoNewline

            $testFixPrompt = @"
$fixPromptSchema

BUG REPORT:
$testReport

FILE PATH: $testRel

CURRENT SOURCE ($($testWorkingLines.Count) lines):
${tb}$testFence
$testTruncated
${tb}
"@

            $testFixResponse = ''
            try {
                $testFixResponse = Invoke-LocalLLM `
                    -SystemPrompt $fixSysPrompt -UserPrompt $testFixPrompt `
                    -Endpoint $llmEndpoint -Model $llmModel `
                    -Temperature $llmTemperature -MaxTokens $fixMaxTokens -Timeout $llmTimeout
            } catch {
                [System.IO.File]::AppendAllText($errorLog, "$(Get-Date -Format u) | TEST FIX FAIL | $testRel iter $testIterIdx | $($_.Exception.Message)`n")
                Write-Host " [LLM ERROR]" -ForegroundColor Red
                $testLogLines.Add("**Fix failed (LLM error):** $($_.Exception.Message)")
                $testLogLines.Add('')
                $testStatus = 'ERROR'
                $globalFailed++
                break
            }

            $testFixedCode = Extract-CodeBlock $testFixResponse $testFence

            if ($testFixedCode -eq '') {
                Write-Host " [NO CODE BLOCK]" -ForegroundColor Red
                $testLogLines.Add("**Fix rejected:** LLM response contained no valid code block.")
                $testLogLines.Add('')
                $testStatus = 'STUCK'
                break
            }

            if ($testFixedCode -eq $testWorkingContent) {
                Write-Host " [NO CHANGE]" -ForegroundColor Yellow
                $testLogLines.Add("**Fix rejected:** LLM returned identical source. No progress possible.")
                $testLogLines.Add('')
                $testStatus = 'STUCK'
                break
            }

            # Bloat guard: reject fixes that balloon the test file
            $testFixedLineCount = @($testFixedCode -split "`n").Count
            if ($testFixedLineCount -gt $testMaxAllowedLines) {
                Write-Host (" [BLOAT {0}>{1} lines]" -f $testFixedLineCount, $testMaxAllowedLines) -ForegroundColor Red
                $testLogLines.Add("**Fix rejected:** Fixed code grew to $testFixedLineCount lines (original: $testOrigLineCount, limit: $testMaxAllowedLines). LLM is adding too much code. Reverting to best version.")
                $testLogLines.Add('')
                $testStatus = 'BLOAT'
                break
            }

            if (Should-SyntaxCheck $testRel) {
                if (-not (Test-PythonSyntax $testFixedCode)) {
                    Write-Host " [SYNTAX ERROR]" -ForegroundColor Red
                    $testLogLines.Add("**Fix rejected:** Fixed code failed Python syntax check.")
                    $testLogLines.Add('')
                    $testStatus = 'SYNTAX_ERR'
                    break
                }
            }

            $tLinesAfter = @($testFixedCode -split "`n").Count
            Write-Host (" applied ({0} -> {1} lines)" -f $testWorkingLines.Count, $tLinesAfter) -ForegroundColor Green
            $testLogLines.Add("**Fix applied.** Lines: $($testWorkingLines.Count) -> $tLinesAfter")
            $testLogLines.Add('')

            $testWorkingContent = $testFixedCode
            $testChanged        = $true
        }

        # Collapse to best version seen
        $testFinalContent = $testBestContent
        $testFinalHigh    = $testBestHigh
        $testFinalMed     = $testBestMed
        $testFinalLow     = $testBestLow
        $testChanged      = ($testFinalContent -ne $testOrigContent)

        # Write fixed test file
        $testApplied = $false
        if ($testChanged) {
            [System.IO.File]::WriteAllText($testFixOut, $testFinalContent, [System.Text.Encoding]::UTF8)
            if ($ApplyFixes) {
                [System.IO.File]::WriteAllText($testPath, $testFinalContent, [System.Text.Encoding]::UTF8)
                $testApplied = $true
                Write-Host ("  -> Applied best test version (iter $testBestIter) to source: $testRel") -ForegroundColor Yellow
            }
        }

        # Write test iteration log
        $testLogLines.Add("---")
        $testLogLines.Add('')
        $testLogLines.Add("**Final status:** $testStatus")
        $testLogLines.Add("**Iterations run:** $testIterIdx")
        $testLogLines.Add("**Best iteration:** $testBestIter (HIGH:$testBestHigh MED:$testBestMed LOW:$testBestLow)")
        $testLogLines.Add("**Remaining (best):** HIGH:$testFinalHigh  MED:$testFinalMed  LOW:$testFinalLow")
        if ($testChanged) {
            $testFixedRel = $testFixOut.Substring($repoRoot.Length).TrimStart('\', '/') -replace '\\', '/'
            $testLogLines.Add("**Fixed file:** ``$testFixedRel`` (content from iteration $testBestIter)")
            if ($testApplied) {
                $testLogLines.Add("**Written back to source:** yes (``-ApplyFixes``)")
            } else {
                $testLogLines.Add("**Written back to source:** no (review ``$testFixedRel`` and copy manually)")
            }
        } else {
            $testLogLines.Add("**Fixed file:** none (best version is identical to original)")
        }

        ($testLogLines -join "`n") | Set-Content $testLogOut -Encoding UTF8

        # Record test hash
        [System.IO.File]::AppendAllText($hashDbPath, "$testSha`ttest:$testRel`n")

        $testResults.Add([PSCustomObject]@{
            TestRel    = $testRel
            SrcRel     = $srcRel
            Iterations = $testIterIdx
            Status     = $testStatus
            FinalHigh  = $testFinalHigh
            FinalMed   = $testFinalMed
            FinalLow   = $testFinalLow
            Changed    = $testChanged
            Applied    = $testApplied
        })

        $tStatusColor = switch ($testStatus) {
            'CLEAN'      { 'Green'  }
            'MAX_ITER'   { 'Red'    }
            'STUCK'      { 'Yellow' }
            'SYNTAX_ERR' { 'Red'    }
            'ERROR'      { 'Red'    }
            'DIVERGING'  { 'Red'    }
            'BLOAT'      { 'Red'    }
            default      { 'White'  }
        }
        Write-Host ("  -> {0} after {1} iteration(s)  HIGH:{2}  MED:{3}  LOW:{4}" -f
            $testStatus, $testIterIdx, $testFinalHigh, $testFinalMed, $testFinalLow) -ForegroundColor $tStatusColor
    }
}

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

# Local helper: sum a property across a result list, safe on empty input.
# Avoids the PS 5.x Measure-Object pitfalls (errors on missing properties,
# warnings escalated by $ErrorActionPreference='Stop').
function Sum-Field($collection, $field) {
    $total = 0
    foreach ($item in $collection) { $total += [int]$item.$field }
    return $total
}

$srcClean   = @($fileResults | Where-Object { $_.Status -eq 'CLEAN' }).Count
$srcMaxIter = @($fileResults | Where-Object { $_.Status -eq 'MAX_ITER' }).Count
$srcStuck   = @($fileResults | Where-Object { $_.Status -in @('STUCK','SYNTAX_ERR','ERROR','DIVERGING','BLOAT') }).Count
$srcChanged = @($fileResults | Where-Object { $_.Changed }).Count
$srcApplied = @($fileResults | Where-Object { $_.Applied }).Count
$srcHigh    = Sum-Field $fileResults 'FinalHigh'
$srcMed     = Sum-Field $fileResults 'FinalMed'

$tstClean   = @($testResults | Where-Object { $_.Status -eq 'CLEAN' }).Count
$tstMaxIter = @($testResults | Where-Object { $_.Status -eq 'MAX_ITER' }).Count
$tstStuck   = @($testResults | Where-Object { $_.Status -in @('STUCK','SYNTAX_ERR','ERROR','DIVERGING','BLOAT') }).Count
$tstChanged = @($testResults | Where-Object { $_.Changed }).Count
$tstApplied = @($testResults | Where-Object { $_.Applied }).Count
$tstHigh    = Sum-Field $testResults 'FinalHigh'
$tstMed     = Sum-Field $testResults 'FinalMed'

$changedCount = $srcChanged + $tstChanged
$appliedCount = $srcApplied + $tstApplied

$sumLines = [System.Collections.Generic.List[string]]::new()
$sumLines.Add("# Bug Hunt Iterative Summary")
$sumLines.Add('')
$sumLines.Add("Generated: $(Get-Date -Format 'yyyy-MM-dd HH:mm')")
$sumLines.Add("Codebase: $codebaseDesc")
$sumLines.Add("Source analyses: $srcTypeStr")
$sumLines.Add("Test analysis: $testTypeStr")
$sumLines.Add("Max iterations per file: $MaxIterations")
$sumLines.Add("Apply to source: $(if ($ApplyFixes) { 'yes (-ApplyFixes)' } else { 'no (staged only)' })")
$sumLines.Add('')

if ($fileResults.Count -gt 0) {
    $sumLines.Add("## Source File Results")
    $sumLines.Add('')
    $sumLines.Add("| File | Iters | Status | HIGH | MED | LOW | Fixed | Applied |")
    $sumLines.Add("|------|-------|--------|------|-----|-----|-------|---------|")
    foreach ($r in $fileResults) {
        $fixedFlag   = if ($r.Changed) { 'yes' } else { 'no' }
        $appliedFlag = if ($r.Applied) { 'yes' } else { 'no' }
        $statusLabel = switch ($r.Status) {
            'CLEAN'      { 'CLEAN'    }
            'MAX_ITER'   { 'MAX_ITER' }
            'STUCK'      { 'STUCK'    }
            'SYNTAX_ERR' { 'SYN_ERR'  }
            'ERROR'      { 'ERROR'    }
            'DIVERGING'  { 'DIVERGE'  }
            'BLOAT'      { 'BLOAT'    }
            default      { $r.Status  }
        }
        $sumLines.Add("| ``$($r.Rel)`` | $($r.Iterations) | $statusLabel | $($r.FinalHigh) | $($r.FinalMed) | $($r.FinalLow) | $fixedFlag | $appliedFlag |")
    }
    $sumLines.Add('')
}

if ($testResults.Count -gt 0) {
    $sumLines.Add("## Test File Results")
    $sumLines.Add('')
    $sumLines.Add("| Test File | Source | Iters | Status | HIGH | MED | LOW | Fixed | Applied |")
    $sumLines.Add("|-----------|--------|-------|--------|------|-----|-----|-------|---------|")
    foreach ($r in $testResults) {
        $fixedFlag   = if ($r.Changed) { 'yes' } else { 'no' }
        $appliedFlag = if ($r.Applied) { 'yes' } else { 'no' }
        $statusLabel = switch ($r.Status) {
            'CLEAN'      { 'CLEAN'    }
            'MAX_ITER'   { 'MAX_ITER' }
            'STUCK'      { 'STUCK'    }
            'SYNTAX_ERR' { 'SYN_ERR'  }
            'ERROR'      { 'ERROR'    }
            'DIVERGING'  { 'DIVERGE'  }
            'BLOAT'      { 'BLOAT'    }
            default      { $r.Status  }
        }
        $sumLines.Add("| ``$($r.TestRel)`` | ``$($r.SrcRel)`` | $($r.Iterations) | $statusLabel | $($r.FinalHigh) | $($r.FinalMed) | $($r.FinalLow) | $fixedFlag | $appliedFlag |")
    }
    $sumLines.Add('')
}

$sumLines.Add("## Totals")
$sumLines.Add('')
$sumLines.Add("| Outcome | Source | Tests |")
$sumLines.Add("|---------|--------|-------|")
$sumLines.Add("| CLEAN                         | $srcClean   | $tstClean   |")
$sumLines.Add("| MAX_ITER (issues remain)       | $srcMaxIter | $tstMaxIter |")
$sumLines.Add("| STUCK / ERROR / DIVERGE / BLOAT | $srcStuck   | $tstStuck   |")
$sumLines.Add("| Files with fixes staged        | $srcChanged | $tstChanged |")
$sumLines.Add("| Files written back to source   | $srcApplied | $tstApplied |")
$sumLines.Add("| Remaining HIGH                | $srcHigh    | $tstHigh    |")
$sumLines.Add("| Remaining MEDIUM              | $srcMed     | $tstMed     |")
$sumLines.Add('')

$needsReview = @($fileResults | Where-Object { $_.Status -ne 'CLEAN' }) + @($testResults | Where-Object { $_.Status -ne 'CLEAN' })
if ($needsReview.Count -gt 0) {
    $sumLines.Add("## Needs Manual Review")
    $sumLines.Add('')
    foreach ($r in ($fileResults | Where-Object { $_.Status -ne 'CLEAN' })) {
        $sumLines.Add("- ``$($r.Rel)`` -- $($r.Status)  HIGH:$($r.FinalHigh)  MED:$($r.FinalMed)")
        $sumLines.Add("  Log: ``$($r.Rel).iter_log.md``")
    }
    foreach ($r in ($testResults | Where-Object { $_.Status -ne 'CLEAN' })) {
        $sumLines.Add("- ``$($r.TestRel)`` (test) -- $($r.Status)  HIGH:$($r.FinalHigh)  MED:$($r.FinalMed)")
        $sumLines.Add("  Log: ``$($r.TestRel).iter_log.md``")
    }
    $sumLines.Add('')
}

if ($ApplyFixes) {
    $sumLines.Add("## Fixes applied")
    $sumLines.Add('')
    $sumLines.Add("``-ApplyFixes`` was set. Fixed files were written directly back to source.")
    $sumLines.Add("Staged copies remain in ``bug_fixes/`` for reference.")
} elseif ($changedCount -gt 0) {
    $sumLines.Add("## How to apply fixes")
    $sumLines.Add('')
    $sumLines.Add("Fixed files are staged in ``bug_fixes/`` and have NOT been written back to source.")
    $sumLines.Add("Review each fixed file, then copy to source if satisfied.")
    $sumLines.Add('')
    $sumLines.Add("Or re-run with ``-ApplyFixes`` to write all fixes back to source automatically.")
}

($sumLines -join "`n") | Set-Content $summaryLog -Encoding UTF8

# ── Final console output ──────────────────────────────────────

Write-Host ''
Write-Host '============================================' -ForegroundColor Cyan
Write-Host '  Complete'                                   -ForegroundColor Cyan
Write-Host '============================================' -ForegroundColor Cyan
if ($fileResults.Count -gt 0) {
    Write-Host "Source files processed: $($fileResults.Count)"
    Write-Host ("  CLEAN:           {0}" -f $srcClean)  -ForegroundColor $(if ($srcClean -eq $fileResults.Count) { 'Green' } else { 'White' })
    Write-Host ("  Fixes staged:    {0}" -f $srcChanged) -ForegroundColor $(if ($srcChanged -gt 0) { 'Cyan' } else { 'White' })
    if ($ApplyFixes) { Write-Host ("  Applied to src:  {0}" -f $srcApplied) -ForegroundColor $(if ($srcApplied -gt 0) { 'Yellow' } else { 'White' }) }
    Write-Host ("  Remaining HIGH:  {0}" -f $srcHigh) -ForegroundColor $(if ($srcHigh -gt 0) { 'Red' } else { 'Green' })
    Write-Host ("  Remaining MED:   {0}" -f $srcMed)  -ForegroundColor $(if ($srcMed  -gt 0) { 'Yellow' } else { 'Green' })
}
if ($testResults.Count -gt 0) {
    Write-Host "Test files processed:   $($testResults.Count)"
    Write-Host ("  CLEAN:           {0}" -f $tstClean)  -ForegroundColor $(if ($tstClean -eq $testResults.Count) { 'Green' } else { 'White' })
    Write-Host ("  Fixes staged:    {0}" -f $tstChanged) -ForegroundColor $(if ($tstChanged -gt 0) { 'Cyan' } else { 'White' })
    if ($ApplyFixes) { Write-Host ("  Applied to src:  {0}" -f $tstApplied) -ForegroundColor $(if ($tstApplied -gt 0) { 'Yellow' } else { 'White' }) }
    Write-Host ("  Remaining HIGH:  {0}" -f $tstHigh) -ForegroundColor $(if ($tstHigh -gt 0) { 'Red' } else { 'Green' })
    Write-Host ("  Remaining MED:   {0}" -f $tstMed)  -ForegroundColor $(if ($tstMed  -gt 0) { 'Yellow' } else { 'Green' })
}
if ($globalFailed -gt 0) {
    Write-Host ("LLM errors: {0}  (see: $errorLog)" -f $globalFailed) -ForegroundColor Red
}
Write-Host ''
Write-Host "Summary:  $summaryLog" -ForegroundColor Cyan
Write-Host "Fixes in: $fixDir"    -ForegroundColor Cyan
if (-not $ApplyFixes -and $changedCount -gt 0) {
    Write-Host ''
    Write-Host "Tip: re-run with -ApplyFixes to write fixes back to source automatically." -ForegroundColor DarkCyan
}
