<#
.SYNOPSIS
    Four-stage Claude pipeline: Summarize existing -> Improve prompt -> Architecture Plan -> aidercommands.md

.DESCRIPTION
    Stage 0: (Auto) If implemented plans exist in "Implemented Plans/", sends them to
             Claude to generate a consolidated "Codebase Summary.md". Skipped if no
             prior plans exist.
    Stage 1: Sends InitialPrompt.md to Claude for review. Claude improves the prompt
             and saves the result to "Implementation Planning Prompt.md". A criticism
             and changelog is saved to PromptUpdates.md.
    Stage 2: Sends "Implementation Planning Prompt.md" (plus Codebase Summary if it
             exists) to Claude to generate a full architecture plan, saved to
             "Architecture Plan.md".
    Stage 3: Sends "Architecture Plan.md" (plus Codebase Summary if it exists) to
             Claude to generate aider step commands, saved to aidercommands.md.

.PARAMETER SkipStage
    Skip one or more stages (1, 2, 3). Useful when resuming after a partial run.
    Example: -SkipStage 1 to skip prompt improvement and start from architecture planning.

.PARAMETER FromStage
    Start from a specific stage (1, 2, or 3). Skips all earlier stages.

.PARAMETER TargetDir
    Folder containing InitialPrompt.md and where all output files are written.
    Defaults to the script's own directory. Relative paths resolve against CWD.

.PARAMETER Claude
    Claude account to use. Defaults to "Claude1".
    Maps to CLAUDE_CONFIG_DIR: Claude1 = .clauderivalon, Claude2 = .claudefksogbetun.

.PARAMETER Model
    Override the Claude model for ALL stages. When not specified, each stage uses its
    optimal default: Sonnet for stages 0, 1, 3b; Opus for stages 2a, 2b, 3a.
    Examples: sonnet, opus, haiku, claude-sonnet-4-6, claude-opus-4-6

.PARAMETER Ultrathink
    Force extended thinking (ultrathink) for ALL stages, overriding per-stage defaults.

.PARAMETER NoUltrathink
    Disable extended thinking for ALL stages, overriding per-stage defaults.

.PARAMETER Restart
    Ignore saved progress and start from stage 1 (or the stage specified by -FromStage).
    Without this flag, the script auto-resumes from where it last stopped.

.PARAMETER Force
    Overwrite existing output files without prompting.

.PARAMETER DryRun
    Show what would be done without actually calling Claude.

.EXAMPLE
    .\Arch_Coding_Pipeline.ps1                        # run all stages with per-stage defaults
    .\Arch_Coding_Pipeline.ps1                        # re-run: auto-resumes from last stage
    .\Arch_Coding_Pipeline.ps1 -Restart               # ignore progress, start from scratch
    .\Arch_Coding_Pipeline.ps1 -Claude Claude2        # use second Claude account
    .\Arch_Coding_Pipeline.ps1 -Model opus            # force Opus for ALL stages
    .\Arch_Coding_Pipeline.ps1 -Ultrathink            # force ultrathink for ALL stages
    .\Arch_Coding_Pipeline.ps1 -NoUltrathink          # disable ultrathink for ALL stages
    .\Arch_Coding_Pipeline.ps1 -FromStage 2       # skip stage 1, start from architecture
    .\Arch_Coding_Pipeline.ps1 -FromStage 3       # only regenerate aidercommands.md
    .\Arch_Coding_Pipeline.ps1 -TargetDir .\MyProjectPrompts  # use a different folder
    .\Arch_Coding_Pipeline.ps1 -DryRun             # preview without running
#>
param(
    [string]$TargetDir,
    [string]$Claude = "Claude1",
    [string]$Model,
    [switch]$Ultrathink,
    [switch]$NoUltrathink,
    [int[]]$SkipStage = @(),
    [ValidateRange(1, 3)]
    [int]$FromStage = 1,
    [switch]$Restart,
    [switch]$Force,
    [switch]$DryRun,
    # Engine routing (three mutually-exclusive modes):
    #   (default)    Stage 1 -> Claude; Stages 0, 2a, 2b, 3a, 3b -> local
    #   -Local       every stage -> local (no Claude at all)
    #   -AllClaude   every stage -> Claude (original pre-Local behaviour)
    [switch]$Local,
    [switch]$AllClaude,
    [string]$LocalEndpoint,
    [string]$LocalModel
)

if ($Local -and $AllClaude) {
    Write-Host "ERROR: -Local and -AllClaude are mutually exclusive." -ForegroundColor Red
    exit 1
}

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# The toolkit lives at e.g. C:\Coding\LocalLLM_Pipeline\ — but the project's
# working data (Implemented Plans/, LocalLLMCodePrompts/, etc.) lives wherever
# the user invokes the script from. Use cwd as the project root by default.
# Run this script from the directory where you want Implemented Plans/ and
# LocalLLMCodePrompts/ to live (e.g. your nmon project root, or a dedicated
# per-project data folder).
$ProjectRoot = (Get-Location).Path

# ── Load shared LLM helper + Common/.env ─────────────────────────────

. (Join-Path $ScriptDir '..\Common\llm_common.ps1')
$script:cfg = Read-EnvFile (Join-Path $ScriptDir '..\Common\.env')

# ── Local LLM config ─────────────────────────────────────────────────
# Always resolved -- the default mode uses local for most stages, so these
# values are always needed unless -AllClaude forces every stage to Claude.

if (-not $AllClaude) {
    # Capture whether the user supplied -LocalModel before we overwrite the
    # variable. PowerShell variable names are case-insensitive, so $LocalModel
    # and $localModel share storage -- without this flag we can't distinguish
    # CLI input from the resolved default.
    $script:UserSuppliedLocalModel = [bool]$LocalModel
    $localModel  = if ($LocalModel)  { $LocalModel }  else { Cfg 'LLM_PLANNING_MODEL' 'gemma4:26b' }
    $localNumCtx = [int](Cfg 'LLM_PLANNING_NUM_CTX' '24576')
    $localTemp   = [double](Cfg 'LLM_TEMPERATURE' '0.1')
    # Planning stages use their own (longer) timeout; fall back to LLM_TIMEOUT for back-compat.
    $localTimeout= [int](Cfg 'LLM_PLANNING_TIMEOUT' (Cfg 'LLM_TIMEOUT' '1200'))
    $localMaxTok = [int](Cfg 'LLM_PLANNING_MAX_TOKENS' '16384')
    $localThink  = ((Cfg 'LLM_THINK' 'false').Trim().ToLower() -in @('1','true','yes','on'))
    $localSaveThinking = ((Cfg 'LLM_SAVE_THINKING' 'false').Trim().ToLower() -in @('1','true','yes','on'))
    if ($LocalEndpoint) {
        $localEp = $LocalEndpoint.TrimEnd('/')
    }
    elseif ($env:LLM_ENDPOINT) {
        $localEp = $env:LLM_ENDPOINT.TrimEnd('/')
    }
    else {
        $localEp = Get-LLMEndpoint
    }
}

# ── Resolve target directory ─────────────────────────────────────────

if ($TargetDir) {
    if (-not [System.IO.Path]::IsPathRooted($TargetDir)) {
        $TargetDir = Join-Path (Get-Location) $TargetDir
    }
    $TargetDir = [System.IO.Path]::GetFullPath($TargetDir)
    if (-not (Test-Path $TargetDir -PathType Container)) {
        Write-Host "ERROR: Target directory not found: $TargetDir" -ForegroundColor Red
        exit 1
    }
}
else {
    $TargetDir = Join-Path $ProjectRoot "LocalLLMCodePrompts"
    if (-not (Test-Path $TargetDir -PathType Container)) {
        Write-Host "ERROR: Default target directory not found: $TargetDir" -ForegroundColor Red
        exit 1
    }
}

# ── Progress tracking ────────────────────────────────────────────────

$ProgressFile = Join-Path $TargetDir ".progress"

function Get-SavedProgress {
    if (-not (Test-Path $ProgressFile)) { return -1 }
    $lines = Get-Content $ProgressFile
    foreach ($line in $lines) {
        if ($line -match '^LastCompleted=(\d+)$') { return [int]$Matches[1] }
    }
    return -1
}

function Get-CurrentMode {
    if ($AllClaude) { return 'allclaude' }
    if ($Local)     { return 'local' }
    return 'default'
}

function Save-Progress {
    param([int]$Stage, [int]$SubStep = -1)
    $mode = Get-CurrentMode
    $lines = @(
        "LastCompleted=$Stage"
        "Mode=$mode"
        "Timestamp=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    )
    if ($SubStep -ge 0) { $lines += "SubStep=$SubStep" }
    $lines | Out-File -FilePath $ProgressFile -Encoding utf8
}

function Get-SavedSubStep {
    if (-not (Test-Path $ProgressFile)) { return -1 }
    $lines = Get-Content $ProgressFile
    foreach ($line in $lines) {
        if ($line -match '^SubStep=(\d+)$') { return [int]$Matches[1] }
    }
    return -1
}

function Get-SavedMode {
    if (-not (Test-Path $ProgressFile)) { return '' }
    $lines = Get-Content $ProgressFile
    foreach ($line in $lines) {
        if ($line -match '^Mode=(.+)$') { return $Matches[1].Trim() }
    }
    # Backward compat: old progress files used Engine=claude|local (pre-AllClaude era)
    foreach ($line in $lines) {
        if ($line -match '^Engine=(.+)$') {
            $e = $Matches[1].Trim()
            # Old 'Engine=claude' meant "no -Local was passed" (= old default, all-Claude).
            if ($e -eq 'claude') { return 'allclaude' }
            # Old 'Engine=local' meant "-Local was passed" (= stages 0/1 Claude, rest local).
            # That is no longer an exact mode; nearest equivalent is 'default'.
            if ($e -eq 'local')  { return 'default' }
        }
    }
    return ''
}

function Clear-Progress {
    if (Test-Path $ProgressFile) { Remove-Item $ProgressFile }
}

# Mode-mismatch guard: refuse to resume when the current mode differs from the
# mode that produced the partial output. Without this, a restart with a
# different -Local/-AllClaude combination would quietly mix Claude and local
# stages in one document.
$savedMode   = Get-SavedMode
$currentMode = Get-CurrentMode
if (-not $Restart -and $savedMode -and $savedMode -ne $currentMode) {
    Write-Host "ERROR: saved progress used mode '$savedMode' but current run uses '$currentMode'." -ForegroundColor Red
    switch ($savedMode) {
        'allclaude' { $hint = 'Re-run with -AllClaude to resume' }
        'local'     { $hint = 'Re-run with -Local to resume' }
        'default'   { $hint = 'Re-run with no mode flags (the default) to resume' }
        default     { $hint = 'Use -Restart to start over' }
    }
    Write-Host "  $hint, or use -Restart to start over." -ForegroundColor Red
    exit 1
}

# Check for saved progress and adjust FromStage if not explicitly overridden
$savedProgress = Get-SavedProgress
if (-not $Restart -and $savedProgress -ge 0 -and $FromStage -eq 1) {
    $resumeFrom = $savedProgress + 1
    if ($resumeFrom -le 3) {
        $FromStage = $resumeFrom
        Write-Host "Resuming from Stage $FromStage (stages 0-$savedProgress completed previously)" -ForegroundColor Yellow
        Write-Host "  Use -Restart to start over" -ForegroundColor DarkGray
    }
    else {
        Write-Host "All stages were completed previously. Use -Restart to run again." -ForegroundColor Yellow
        exit 0
    }
}
elseif ($Restart -and $savedProgress -ge 0) {
    Write-Host "Restarting (ignoring saved progress through stage $savedProgress)" -ForegroundColor Yellow
    Clear-Progress
}

# ── Per-stage model and ultrathink defaults ─────────────────────────

# Default model per sub-stage: Sonnet for synthesis/formatting, Opus for architecture.
# LocalModel is an optional per-stage override for the local engine -- a thinking
# model (gemma4:26b) helps with architectural reasoning (2a/2b), but stages 3a/3b
# produce tightly-formatted lists and aider prompts where a coder model like
# qwen3-coder:30b is both faster and far more reliable. Empty string = use
# $localModel (LLM_PLANNING_MODEL from .env).
$StageDefaults = @{
    "0"  = @{ Model = "sonnet"; Think = $false; LocalModel = "" }                # Summarize existing codebase
    "1"  = @{ Model = "sonnet"; Think = $false; LocalModel = "" }                # Improve initial prompt
    "2a" = @{ Model = "opus";   Think = $true;  LocalModel = "" }                # Section planning
    "2b" = @{ Model = "opus";   Think = $true;  LocalModel = "" }                # Per-section architecture
    "3a" = @{ Model = "opus";   Think = $true;  LocalModel = "qwen3-coder:30b" } # Step planning -- structured list, coder model is more reliable
    "3b" = @{ Model = "sonnet"; Think = $false; LocalModel = "qwen3-coder:30b" } # Per-step aider commands -- structured output, coder model is more reliable
}

$ModelOverride = $Model  # empty string if not specified by user

function Get-StageEngine {
    param([string]$SubStage)
    # Three routing modes:
    #   -AllClaude            -> every stage uses Claude
    #   -Local                -> every stage uses the local LLM
    #   (default, no flags)   -> Stage 1 uses Claude; every other stage uses local
    #                           (Claude is reserved for prompt refinement only)
    if ($AllClaude) { return 'claude' }
    if ($Local)     { return 'local' }
    if ($SubStage -eq '1') { return 'claude' }
    return 'local'
}

function Get-StageModel {
    param([string]$SubStage)
    if ($ModelOverride) { return $ModelOverride }
    return $StageDefaults[$SubStage].Model
}

function Get-StageThinkPrefix {
    param([string]$SubStage)
    # Ultrathink is a Claude-only concept; local models have no extended-thinking mode.
    if ((Get-StageEngine $SubStage) -eq 'local') { return "" }
    $useThink = $StageDefaults[$SubStage].Think
    if ($Ultrathink) { $useThink = $true }
    if ($NoUltrathink) { $useThink = $false }
    if ($useThink) { return "ultrathink. " }
    return ""
}

Write-Host "Target directory: $TargetDir" -ForegroundColor Gray
Write-Host "Claude CLI: $Claude" -ForegroundColor Gray
if ($ModelOverride) {
    Write-Host "Claude model: $ModelOverride (all Claude stages)" -ForegroundColor Gray
}
else {
    Write-Host "Claude model: per-stage defaults (Sonnet: 0,1,3b  Opus: 2a,2b,3a)" -ForegroundColor Gray
}
if ($Ultrathink) { Write-Host "Ultrathink: forced ON (Claude stages)" -ForegroundColor Gray }
elseif ($NoUltrathink) { Write-Host "Ultrathink: forced OFF (Claude stages)" -ForegroundColor Gray }
else { Write-Host "Ultrathink: per-stage defaults (ON: 2a,2b,3a  OFF: 0,1,3b)" -ForegroundColor Gray }

switch (Get-CurrentMode) {
    'allclaude' {
        Write-Host "Mode: -AllClaude -- every stage uses Claude" -ForegroundColor Yellow
    }
    'local' {
        Write-Host "Mode: -Local -- every stage uses the local LLM (no Claude)" -ForegroundColor Yellow
        Write-Host "  Local endpoint: $localEp" -ForegroundColor Gray
        Write-Host "  Local model:    $localModel (num_ctx=$localNumCtx)" -ForegroundColor Gray
    }
    'default' {
        Write-Host "Mode: default -- Stage 1 = Claude; Stages 0, 2a, 2b, 3a, 3b = local" -ForegroundColor Yellow
        Write-Host "  Local endpoint: $localEp" -ForegroundColor Gray
        Write-Host "  Local model:    $localModel (num_ctx=$localNumCtx)" -ForegroundColor Gray
    }
}

# ── File paths ───────────────────────────────────────────────────────

$InitialPrompt             = Join-Path $TargetDir "InitialPrompt.md"
$ImplementationPlanPrompt  = Join-Path $TargetDir "Implementation Planning Prompt.md"
$PromptUpdates             = Join-Path $TargetDir "PromptUpdates.md"
$ArchitecturePlan          = Join-Path $TargetDir "Architecture Plan.md"
$AiderCommands             = Join-Path $TargetDir "aidercommands.md"
$CodebaseSummary           = Join-Path (Join-Path $ProjectRoot "Implemented Plans") "Codebase Summary.md"

# ── Helpers ──────────────────────────────────────────────────────────

function Write-Stage {
    param([int]$Number, [string]$Title)
    $bar = "=" * 60
    Write-Host "`n$bar" -ForegroundColor Cyan
    Write-Host "  Stage $Number - $Title" -ForegroundColor Cyan
    Write-Host $bar -ForegroundColor Cyan
}

function Assert-FileExists {
    param([string]$Path, [string]$Label)
    if (-not (Test-Path $Path)) {
        Write-Host "ERROR: $Label not found: $Path" -ForegroundColor Red
        exit 1
    }
}

function Invoke-ClaudePrint {
    <#
    .SYNOPSIS
        Calls claude --print with the given prompt, handling account selection.
        Returns the result as a string array.
    #>
    param(
        [string]$Prompt,
        [string]$StageModel
    )

    # Map account name to config dir (mirrors PS profile functions)
    $configDirs = @{
        "claude1" = "$env:USERPROFILE\.clauderivalon"
        "claude2" = "$env:USERPROFILE\.claudefksogbetun"
    }
    $key = $Claude.ToLower()
    if (-not $configDirs.ContainsKey($key)) {
        Write-Host "  ERROR: Unknown Claude account '$Claude'. Expected: Claude1 or Claude2" -ForegroundColor Red
        exit 1
    }

    Write-Host "    [$StageModel]" -ForegroundColor DarkGray -NoNewline
    Write-Host "" # newline

    $prev = $env:CLAUDE_CONFIG_DIR
    $env:CLAUDE_CONFIG_DIR = $configDirs[$key]
    try {
        $result = $Prompt | claude --print --output-format text --model $StageModel 2>&1
    }
    finally {
        $env:CLAUDE_CONFIG_DIR = $prev
    }

    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ERROR: Claude failed (exit code $LASTEXITCODE)" -ForegroundColor Red
        Write-Host $result -ForegroundColor Red
        exit 1
    }
    return $result
}

function Invoke-Claude {
    param(
        [string]$Prompt,
        [string]$OutputFile,
        [string]$Description,
        [string]$StageModel
    )

    Write-Host "  Calling Claude..." -ForegroundColor Yellow
    Write-Host "  Output: $OutputFile" -ForegroundColor Gray

    if ($DryRun) {
        Write-Host "  [DRY RUN] Would send prompt ($($Prompt.Length) chars) with model $StageModel" -ForegroundColor DarkGray
        Write-Host "  [DRY RUN] First 200 chars: $($Prompt.Substring(0, [Math]::Min(200, $Prompt.Length)))..." -ForegroundColor DarkGray
        return
    }

    $result = Invoke-ClaudePrint -Prompt $Prompt -StageModel $StageModel

    $result | Out-File -FilePath $OutputFile -Encoding utf8
    Write-Host "  Saved to $OutputFile" -ForegroundColor Green
}

function Invoke-StagePrint {
    <#
    .SYNOPSIS
        Routes a stage's prompt to Claude or the local LLM based on -Local and stage.
        Always returns a single string (callers may still -join "`n" — no-op on string).
    #>
    param(
        [string]$Prompt,
        [string]$SubStage,
        # Optional: write thinking-model reasoning to this file (local engine only).
        # Ignored when LLM_SAVE_THINKING is false, when the engine is Claude, or
        # when the model is not a thinking model.
        [string]$ThinkingFile = ''
    )

    $engine = Get-StageEngine $SubStage
    if ($engine -eq 'claude') {
        $stageModel = Get-StageModel $SubStage
        $result = Invoke-ClaudePrint -Prompt $Prompt -StageModel $stageModel
        return ($result -join "`n")
    }
    else {
        # Per-stage local model override wins over the global LLM_PLANNING_MODEL,
        # unless the user passed -LocalModel on the CLI (that's already captured
        # in $localModel and takes precedence over everything).
        $stageLocalModel = $localModel
        $stageOverride = ''
        if ($StageDefaults.ContainsKey($SubStage) -and $StageDefaults[$SubStage].ContainsKey('LocalModel')) {
            $stageOverride = [string]$StageDefaults[$SubStage]['LocalModel']
        }
        if (-not $script:UserSuppliedLocalModel -and $stageOverride -ne '') {
            $stageLocalModel = $stageOverride
        }
        # Thinking only makes sense for thinking-capable models; disable it
        # automatically when a stage overrides to a non-thinking coder model.
        $stageThink = $localThink
        if ($stageLocalModel -ne $localModel) { $stageThink = $false }

        Write-Host "    [local: $stageLocalModel @ $localEp ctx=$localNumCtx think=$stageThink]" -ForegroundColor DarkGray
        $tf = if ($localSaveThinking) { $ThinkingFile } else { '' }
        return Invoke-LocalLLM `
            -UserPrompt   $Prompt `
            -Model        $stageLocalModel `
            -NumCtx       $localNumCtx `
            -Temperature  $localTemp `
            -Timeout      $localTimeout `
            -Endpoint     $localEp `
            -MaxTokens    $localMaxTok `
            -Think        $stageThink `
            -ThinkingFile $tf
    }
}

function Invoke-Stage {
    <#
    .SYNOPSIS
        File-writing wrapper around Invoke-StagePrint (used by Stage 0).
    #>
    param(
        [string]$Prompt,
        [string]$OutputFile,
        [string]$Description,
        [string]$SubStage
    )

    $engine = Get-StageEngine $SubStage
    Write-Host "  Calling $engine..." -ForegroundColor Yellow
    Write-Host "  Output: $OutputFile" -ForegroundColor Gray

    if ($DryRun) {
        Write-Host "  [DRY RUN] Would send prompt ($($Prompt.Length) chars) via $engine" -ForegroundColor DarkGray
        return
    }

    $thinkingFile = "$OutputFile.thinking.md"
    $result = Invoke-StagePrint -Prompt $Prompt -SubStage $SubStage -ThinkingFile $thinkingFile
    $result | Out-File -FilePath $OutputFile -Encoding utf8
    Write-Host "  Saved to $OutputFile" -ForegroundColor Green
}

function Get-ArchitectureSlice {
    <#
    .SYNOPSIS
        Returns only the sections of Architecture Plan.md relevant to a list of
        files, plus always-include sections (Project Structure, Data Model,
        Configuration, Dependencies, Build/Run, Testing). Used by Stage 3b to
        keep prompts inside a local model's context window.
    #>
    param(
        [string]  $ArchContent,
        [string[]]$Files
    )

    $alwaysInclude = @(
        'Project Structure', 'Data Model', 'Data Pipeline',
        'Configuration', 'Dependencies', 'Build/Run', 'Build ', 'Testing'
    )

    $basenames = @()
    foreach ($f in $Files) {
        $leaf = Split-Path -Leaf ($f -replace '/', '\')
        if ($leaf) { $basenames += $leaf }
    }

    # Split so each ## heading starts a new chunk; preamble (H1 + intro) is the first chunk.
    $parts = [regex]::Split($ArchContent, '(?m)(?=^##\s)')
    $result = @()
    foreach ($part in $parts) {
        if ($part -match '(?m)^##\s+(.+?)\s*$') {
            $heading = $Matches[1].Trim()
            $keep = $false
            foreach ($a in $alwaysInclude) {
                if ($heading -match [regex]::Escape($a)) { $keep = $true; break }
            }
            if (-not $keep) {
                foreach ($bn in $basenames) {
                    if ($heading -match [regex]::Escape($bn)) { $keep = $true; break }
                }
            }
            if ($keep) { $result += $part }
        }
        else {
            # Preamble (before first ##) — always include
            $result += $part
        }
    }
    return ($result -join '')
}

function Confirm-Overwrite {
    param([string[]]$Files)
    if ($Force -or $DryRun) { return $true }
    $existing = $Files | Where-Object { Test-Path $_ }
    if (-not $existing) { return $true }

    Write-Host ""
    Write-Host "  The following output files already exist:" -ForegroundColor Yellow
    foreach ($f in $existing) {
        $info = Get-Item $f
        $modified = $info.LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss")
        $sizeKB = [math]::Round($info.Length / 1KB, 1)
        Write-Host "    $($info.Name)  ($sizeKB KB, modified $modified)" -ForegroundColor Yellow
    }
    $answer = Read-Host "  Overwrite? [y/N]"
    return ($answer.Trim().ToLower() -in @("y", "yes"))
}

function Should-RunStage {
    param([int]$Number)
    if ($Number -lt $FromStage) { return $false }
    if ($SkipStage -contains $Number) { return $false }
    return $true
}

function Get-ImplementedPlans {
    <#
    .SYNOPSIS
        Returns all Architecture Plan and Bug Fix Changes files from
        "Implemented Plans/" sorted by timestamp, or empty if none exist.
    #>
    $implDir = Join-Path $ProjectRoot "Implemented Plans"
    if (-not (Test-Path $implDir -PathType Container)) { return @() }

    $plans = Get-ChildItem -Path $implDir -Filter "*.md" |
             Where-Object { $_.Name -match '^(Architecture Plan|Bug Fix Changes) \d+\.md$' } |
             Sort-Object LastWriteTime
    if (-not $plans) { return @() }
    return $plans
}

function Get-CodebaseSummaryContext {
    <#
    .SYNOPSIS
        Returns the Codebase Summary as a context block for injection into prompts.
        Returns empty string if no summary exists.
    #>
    if (-not (Test-Path $CodebaseSummary)) { return "" }

    $content = Get-Content $CodebaseSummary -Raw
    Write-Host "  Injecting Codebase Summary.md as context" -ForegroundColor Gray

    return @"

## Existing Codebase Context

The following is a consolidated summary of all previously implemented architecture plans.
The codebase already contains the files, modules, data models, and infrastructure described
below. Your plan must build on this existing code — do not recreate or conflict with what
already exists. Reuse existing modules, types, and patterns where appropriate.

$content
"@
}

# ── Stage 0: Summarize implemented plans ─────────────────────────────

$implementedPlans = @(Get-ImplementedPlans)
if ($implementedPlans.Count -gt 0) {
    Write-Stage 0 "Summarize Existing Codebase"
    Write-Host "  Found $($implementedPlans.Count) implemented document(s):" -ForegroundColor Gray
    foreach ($p in $implementedPlans) {
        $sizeKB = [math]::Round($p.Length / 1KB, 1)
        Write-Host "    $($p.Name)  ($sizeKB KB)" -ForegroundColor Gray
    }

    if (-not (Confirm-Overwrite @($CodebaseSummary))) {
        Write-Host "  Skipping Stage 0 (user declined overwrite)" -ForegroundColor DarkYellow
    }
    else {

    # Concatenate all plans
    $allPlans = @()
    foreach ($p in $implementedPlans) {
        $content = Get-Content $p.FullName -Raw
        $allPlans += "### $($p.Name)`n`n$content"
    }
    $rawPlans = $allPlans -join "`n`n---`n`n"

    $stage0Think = Get-StageThinkPrefix "0"
    $stage0Prompt = "${stage0Think}" + @'
You are a software architect reviewing a series of architecture plans and bug fix
changelogs that have been applied to a working codebase. The documents are ordered
chronologically by timestamp. Produce a single consolidated summary of the current
state of the codebase.

Document types you will see:
- "Architecture Plan N.md" -- describes the design and implementation of a feature set
- "Bug Fix Changes N.md" -- describes bug fixes applied to existing code

Your summary must include:
1. **Project structure** -- the current directory tree with all files
2. **Data model** -- current database schema, key dataclasses and types
3. **Module inventory** -- for each module/file: its purpose, key classes/functions with
   signatures, and how it connects to other modules
4. **Dependencies** -- current PyPI packages with versions
5. **Configuration** -- current config schema and defaults
6. **Patterns and conventions** -- naming conventions, error handling patterns, threading
   model, or other architectural patterns established in the codebase
7. **Bug fixes applied** -- summary of bugs that were found and fixed, so future plans
   do not reintroduce them

Where later documents modified or extended earlier ones, reflect the FINAL state only --
do not include superseded designs. Be thorough but concise: include enough detail that
a developer (or LLM) could write new code that integrates cleanly with the existing
codebase.

Output the summary as a well-structured markdown document.

Here are the implemented documents (in chronological order):

'@ + "`n`n" + $rawPlans

    Invoke-Stage -Prompt $stage0Prompt -OutputFile $CodebaseSummary -Description "Codebase Summary" -SubStage "0"
    if (-not $DryRun) { Save-Progress 0 }

    } # end overwrite check
}
else {
    Write-Host "`n  No implemented plans found - skipping Stage 0 (Codebase Summary)" -ForegroundColor DarkGray
}

# ── Stage 1: Improve the initial prompt ──────────────────────────────

if (Should-RunStage 1) {
    Write-Stage 1 "Improve Initial Prompt"
    Assert-FileExists $InitialPrompt "InitialPrompt.md"

    if (-not (Confirm-Overwrite @($ImplementationPlanPrompt, $PromptUpdates))) {
        Write-Host "  Skipping Stage 1 (user declined overwrite)" -ForegroundColor DarkYellow
    }
    else {

    $initialContent = Get-Content $InitialPrompt -Raw

    # Ask Claude to improve the prompt AND produce a criticism/changelog
    $stage1Think = Get-StageThinkPrefix "1"
    $stage1Prompt = "${stage1Think}" + @'
I have an initial prompt for planning a software project. I need you to do two things:

1. **Review and improve** the prompt below. Produce a refined, detailed implementation
   planning prompt that is clear, unambiguous, and comprehensive. The improved prompt
   should specify tech stack, data model, UI requirements, testing strategy, and
   architecture deliverables. Output ONLY the improved prompt text (no preamble).

2. After the improved prompt, add a separator line "---PROMPT_UPDATES---" followed by
   a critique of the original prompt: what was unclear, contradictory, or missing, and
   what changes you made and why. Format this as markdown with headers.

Here is the initial prompt to improve:

'@ + "`n`n" + $initialContent

    Write-Host "  Processing InitialPrompt.md..." -ForegroundColor Gray

    if (-not $DryRun) {
        $fullResult = Invoke-StagePrint -Prompt $stage1Prompt -SubStage "1"

        # Split on the separator
        $separator = "---PROMPT_UPDATES---"
        $parts = ($fullResult -join "`n") -split [regex]::Escape($separator), 2

        $improvedPrompt = $parts[0].Trim()
        $improvedPrompt | Out-File -FilePath $ImplementationPlanPrompt -Encoding utf8
        Write-Host "  Saved improved prompt to: Implementation Planning Prompt.md" -ForegroundColor Green

        if ($parts.Count -gt 1) {
            $updates = $parts[1].Trim()
            $header = "# Prompt Updates`n`nGenerated: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')`n`n"
            ($header + $updates) | Out-File -FilePath $PromptUpdates -Encoding utf8
            Write-Host "  Saved critique to: PromptUpdates.md" -ForegroundColor Green
        }
        else {
            Write-Host "  Warning: No PROMPT_UPDATES separator found in output" -ForegroundColor DarkYellow
        }
        Save-Progress 1
    }
    else {
        Write-Host "  [DRY RUN] Would process InitialPrompt.md ($($initialContent.Length) chars)" -ForegroundColor DarkGray
        Write-Host "  [DRY RUN] Would save to: Implementation Planning Prompt.md" -ForegroundColor DarkGray
        Write-Host "  [DRY RUN] Would save to: PromptUpdates.md" -ForegroundColor DarkGray
    }

    } # end overwrite check
}
else {
    Write-Host "`n  Skipping Stage 1 (Improve Initial Prompt)" -ForegroundColor DarkGray
}

# ── Stage 2: Generate Architecture Plan (two-pass) ───────────────────

$SectionPlanFile = Join-Path $TargetDir ".section_plan.md"

if (Should-RunStage 2) {
    Write-Stage 2 "Generate Architecture Plan"
    Assert-FileExists $ImplementationPlanPrompt "Implementation Planning Prompt.md"

    $planningPrompt = Get-Content $ImplementationPlanPrompt -Raw
    $existingContext = Get-CodebaseSummaryContext

    # ── Stage 2a: Planning pass - generate section list ──────────────

    # Check if we're resuming mid-stage-2 (section plan already exists)
    $resumeSection = -1
    if ((Test-Path $SectionPlanFile) -and (Get-SavedProgress) -eq 1) {
        $resumeSection = Get-SavedSubStep
        if ($resumeSection -ge 0) {
            Write-Host "  Resuming Stage 2 from section $($resumeSection + 1) (sections 1-$resumeSection completed)" -ForegroundColor Yellow
        }
    }

    if ($resumeSection -lt 0) {
        # No resume - generate the section plan
        if (-not (Confirm-Overwrite @($ArchitecturePlan))) {
            Write-Host "  Skipping Stage 2 (user declined overwrite)" -ForegroundColor DarkYellow
        }
        else {

        Write-Host "  Stage 2a: Generating section plan..." -ForegroundColor Gray

        $s2aThink = Get-StageThinkPrefix "2a"
        $sectionPlanPrompt = "${s2aThink}" + @'
You are a software architect planning the sections of a comprehensive
architecture plan document. Given the implementation planning prompt below, list the
sections that the architecture plan should contain.

Output ONLY a numbered list in this exact format, one section per line:

SECTION 1 | Project Structure | full directory tree with every file path
SECTION 2 | Data Model | database schema, Python dataclasses and TypedDicts
SECTION 3 | Module: module_name.py | purpose, classes, function signatures, pseudocode, error handling
...

Rules:
- Include one SECTION entry for each module/file in the "Module breakdown" (one per file)
- Include separate sections for: Project Structure, Data Model, Data Pipeline,
  UI/TUI Layout, Configuration, Testing Strategy, Dependencies, Build/Run Instructions
- The description after the title should summarize what that section covers
- Do NOT output anything else. No headers, no explanations, no markdown formatting.
  Just the section list.

Here is the planning prompt:

'@ + "`n`n" + $planningPrompt + "`n" + $existingContext

        if ($DryRun) {
            Write-Host "  [DRY RUN] Would generate section plan" -ForegroundColor DarkGray
        }
        else {
            $sectionResult = Invoke-StagePrint -Prompt $sectionPlanPrompt -SubStage "2a" -ThinkingFile "$SectionPlanFile.thinking.md"
            $sectionText = ($sectionResult -join "`n").Trim()
            $sectionText | Out-File -FilePath $SectionPlanFile -Encoding utf8
            Write-Host "  Section plan saved to .section_plan.md" -ForegroundColor Green

            # Fresh start of Stage 2: overwrite Architecture Plan.md with just the
            # top-level heading. Each section the model generates in Stage 2b is
            # appended directly to this file -- no per-section Plans/Section N.md
            # files, no post-loop consolidation. If the run dies mid-way, the
            # partial file on disk already has every completed section.
            "# Architecture Plan`n" | Out-File -FilePath $ArchitecturePlan -Encoding utf8
        }

        } # end overwrite check
    }

    # ── Stage 2b: Generate each section ──────────────────────────────

    if ((Test-Path $SectionPlanFile) -and -not $DryRun) {
        $sectionLines = @(Get-Content $SectionPlanFile |
            Where-Object { $_ -match '^\s*SECTION\s+\d+' })

        $totalSections = $sectionLines.Count
        if ($totalSections -eq 0) {
            Write-Host "ERROR: Stage 2a produced no parseable SECTION lines." -ForegroundColor Red
            Write-Host "  Inspect $SectionPlanFile -- the model likely ignored the 'SECTION N | Title | Description' format." -ForegroundColor Red
            exit 1
        }
        Write-Host "  Stage 2b: Generating $totalSections section(s)..." -ForegroundColor Gray

        $secNum = 0
        foreach ($line in $sectionLines) {
            # Parse: SECTION N | Title | Description
            if ($line -match '^\s*SECTION\s+(\d+)\s*\|\s*(.+?)\s*\|\s*(.+)\s*$') {
                $secNum = [int]$Matches[1]
                $secTitle = $Matches[2].Trim()
                $secDesc = $Matches[3].Trim()
            }
            else {
                Write-Host "  Warning: Could not parse section line: $line" -ForegroundColor DarkYellow
                continue
            }

            # Skip already-completed sections when resuming
            if ($secNum -le $resumeSection) {
                Write-Host "    Section $secNum/$totalSections - $secTitle [already done]" -ForegroundColor DarkGray
                continue
            }

            Write-Host "    Section $secNum/$totalSections - $secTitle" -ForegroundColor Cyan

            $s2bThink = Get-StageThinkPrefix "2b"
            $sectionPrompt = "${s2bThink}" + @'
You are a software architect writing ONE section of an architecture plan.
Output ONLY the section content as markdown. Start with a ## heading. Be thorough and
detailed - include complete function signatures with parameter types and return types,
dataclass definitions, pseudocode logic, and error handling approach.

Do NOT output anything before the ## heading or after the section content. No preamble,
no summary, no follow-up questions.

The section to write:
Title: SECTITLE
Description: SECDESC

Here is the full implementation planning prompt for context:

'@
            $sectionPrompt = $sectionPrompt -replace 'SECTITLE', $secTitle
            $sectionPrompt = $sectionPrompt -replace 'SECDESC', $secDesc
            $sectionPrompt = $sectionPrompt + "`n`n" + $planningPrompt

            if ($existingContext) {
                $sectionPrompt = $sectionPrompt + "`n" + $existingContext
            }

            # Thinking sidecar lives next to the consolidated output file. One
            # sidecar per section so reasoning for a specific section can be
            # inspected without interleaving.
            $sectionThinkFile = "$ArchitecturePlan.section_$secNum.thinking.md"
            $sectionResult = Invoke-StagePrint -Prompt $sectionPrompt -SubStage "2b" -ThinkingFile $sectionThinkFile
            $sectionContent = ($sectionResult -join "`n").Trim()

            # Append this section directly to Architecture Plan.md with a blank
            # line separator. No per-section intermediate file; on crash the
            # partial file already contains everything produced so far.
            "`n$sectionContent`n" | Out-File -FilePath $ArchitecturePlan -Append -Encoding utf8
            Write-Host "    Section $secNum/$totalSections - done -> appended to Architecture Plan.md" -ForegroundColor Green

            # Save sub-step progress
            Save-Progress 1 -SubStep $secNum
        }

        if (Test-Path $SectionPlanFile) { Remove-Item $SectionPlanFile }
        Save-Progress 2
        Write-Host "  All $totalSections sections generated in Architecture Plan.md" -ForegroundColor Green
    }
    elseif ($DryRun) {
        Write-Host "  [DRY RUN] Would generate individual sections" -ForegroundColor DarkGray
    }
}
else {
    Write-Host "`n  Skipping Stage 2 (Generate Architecture Plan)" -ForegroundColor DarkGray
}

# ── Stage 3: Generate aidercommands.md (two-pass) ────────────────────

$StepPlanFile = Join-Path $TargetDir ".step_plan.md"

if (Should-RunStage 3) {
    Write-Stage 3 "Generate Aider Commands"
    Assert-FileExists $ArchitecturePlan "Architecture Plan.md"

    $archContent = Get-Content $ArchitecturePlan -Raw
    $existingContext = Get-CodebaseSummaryContext

    # ── Stage 3a: Planning pass - generate step list ─────────────────

    # Check if we're resuming mid-stage-3 (step plan already exists)
    $resumeSubStep = -1
    if ((Test-Path $StepPlanFile) -and (Get-SavedProgress) -eq 2) {
        $resumeSubStep = Get-SavedSubStep
        if ($resumeSubStep -ge 0) {
            Write-Host "  Resuming Stage 3 from step $($resumeSubStep + 1) (steps 1-$resumeSubStep completed)" -ForegroundColor Yellow
        }
    }

    if ($resumeSubStep -lt 0) {
        # No resume - generate the step plan
        if (-not (Confirm-Overwrite @($AiderCommands))) {
            Write-Host "  Skipping Stage 3 (user declined overwrite)" -ForegroundColor DarkYellow
        }
        else {

        Write-Host "  Stage 3a: Generating step plan..." -ForegroundColor Gray

        $s3aThink = Get-StageThinkPrefix "3a"
        # Structure: brief task framing -> architecture plan -> format spec at the
        # END with a priming line. Coder models (qwen3-coder) tend to continue
        # whatever content appears last in the prompt; putting the format spec +
        # "STEP 1 |" prime at the tail makes the correct output path the path of
        # least resistance.
        $planPassPrompt = "${s3aThink}" + @'
You are decomposing an architecture plan into discrete implementation steps
for aider (an AI coding tool). Each step creates or modifies ONE file (or a small,
tightly coupled set).

Architecture plan below:

===ARCHITECTURE_PLAN_START===

'@ + $archContent + "`n" + $existingContext + @'

===ARCHITECTURE_PLAN_END===

Now produce the step list. Rules:
- Order steps so dependencies are created before dependents
- Include test file steps alongside or immediately after the module they test
- Step 1 should be pyproject.toml + config files
- Final step should be the entry point that wires everything together

Output format: one step per line, pipe-delimited, with EXACTLY this shape:

STEP <n> | <title> | <comma-separated file paths>

Do NOT output anything else. No markdown headers (`#`), no code fences, no
explanations, no blank lines between steps. Every output line must begin with
the literal word "STEP " followed by a number.

Begin your response with "STEP 1 |" and continue through every file in the
architecture plan. First line of your response MUST match the regex
^STEP \d+ \| .+ \| .+$
'@

        if ($DryRun) {
            Write-Host "  [DRY RUN] Would generate step plan" -ForegroundColor DarkGray
        }
        else {
            $planResult = Invoke-StagePrint -Prompt $planPassPrompt -SubStage "3a" -ThinkingFile "$StepPlanFile.thinking.md"
            $planText = ($planResult -join "`n").Trim()
            $planText | Out-File -FilePath $StepPlanFile -Encoding utf8
            Write-Host "  Step plan saved to .step_plan.md" -ForegroundColor Green

            # Write the aidercommands.md header
            $header = @'
# Implementation - One File Per Session

Each step is a separate aider invocation. The prompt is self-contained -
do NOT --read Architecture Plan.md, it is too large. Run each command,
wait for it to finish, then move to the next step.

---

'@
            $header | Out-File -FilePath $AiderCommands -Encoding utf8
        }

        } # end overwrite check
    }

    # ── Stage 3b: Generate each step ─────────────────────────────────

    if ((Test-Path $StepPlanFile) -and -not $DryRun) {
        $stepPlanLines = @(Get-Content $StepPlanFile |
            Where-Object { $_ -match '^\s*STEP\s+\d+' })

        $totalSteps = $stepPlanLines.Count
        if ($totalSteps -eq 0) {
            Write-Host "ERROR: Stage 3a produced no parseable STEP lines." -ForegroundColor Red
            Write-Host "  Inspect $StepPlanFile -- the model likely ignored the 'STEP N | Title | files' format." -ForegroundColor Red
            Write-Host "  Re-run Stage 3 after deleting that file, or edit it to match the expected format." -ForegroundColor Red
            exit 1
        }
        Write-Host "  Stage 3b: Generating $totalSteps step(s)..." -ForegroundColor Gray

        $stepNum = 0
        foreach ($line in $stepPlanLines) {
            # Parse: STEP N | Title | files
            if ($line -match '^\s*STEP\s+(\d+)\s*\|\s*(.+?)\s*\|\s*(.+)\s*$') {
                $stepNum = [int]$Matches[1]
                $stepTitle = $Matches[2].Trim()
                $stepFiles = $Matches[3].Trim()
            }
            else {
                Write-Host "  Warning: Could not parse step line: $line" -ForegroundColor DarkYellow
                continue
            }

            # Skip already-completed steps when resuming
            if ($stepNum -le $resumeSubStep) {
                Write-Host "    Step $stepNum/$totalSteps - $stepTitle [already done]" -ForegroundColor DarkGray
                continue
            }

            Write-Host "    Step $stepNum/$totalSteps - $stepTitle" -ForegroundColor Cyan

            $fileList = ($stepFiles -split ',') | ForEach-Object { $_.Trim() }
            $aiderFiles = $fileList -join ' '

            $s3bThink = Get-StageThinkPrefix "3b"
            # The template previously contained a literal "<the full self-contained
            # prompt for the local LLM>" placeholder. Coder models (qwen3-coder)
            # took that as example output and echoed it verbatim. Restructured:
            # architecture context FIRST, then concrete output-shape rules at the
            # END, with prose describing what to generate (no placeholder string
            # the model can copy).
            $stepPrompt = "${s3bThink}" + @'
Your task: write one implementation step for aider (an AI coding tool), covering
the files listed below. The output is a markdown block containing (a) an aider
shell command and (b) a SELF-CONTAINED implementation prompt for a local LLM
that has no access to the architecture plan.

Step number: STEPNUM
Step title:  STEPTITLE
Files:       AIDERFILES

Relevant architecture plan context is between the delimiters below. Extract from
it every detail the local LLM needs -- types, function signatures with parameter
and return types, dataclass definitions, imports, pseudocode, error handling --
and put those details into the implementation prompt you write.

===ARCHITECTURE_CONTEXT_START===
'@
            # Replace placeholders
            $stepPrompt = $stepPrompt -replace 'STEPNUM', $stepNum
            $stepPrompt = $stepPrompt -replace 'STEPTITLE', $stepTitle
            $stepPrompt = $stepPrompt -replace 'AIDERFILES', $aiderFiles

            # In -Local mode, only inject the architecture sections relevant to
            # this step's files (plus always-include sections). Keeps the prompt
            # inside the local model's context window. Claude gets the full plan.
            if ($Local) {
                $archSlice = Get-ArchitectureSlice -ArchContent $archContent -Files $fileList
                $stepPrompt = $stepPrompt + "`n`n" + $archSlice
            }
            else {
                $stepPrompt = $stepPrompt + "`n`n" + $archContent
            }

            if ($existingContext) {
                $stepPrompt = $stepPrompt + "`n" + $existingContext
            }

            # Close the architecture-context delimiter and append the output-format
            # spec AFTER the context so the model's last instructions describe what
            # to produce, not what context to consume.
            $stepPrompt = $stepPrompt + "`n===ARCHITECTURE_CONTEXT_END===`n`n" + @"
Now produce the step output. It MUST consist of exactly these four blocks in
order, and nothing else (no preamble, no trailing commentary):

1. A markdown H2 heading line: ## Step $stepNum - $stepTitle
2. A blank line
3. A bash fenced code block containing exactly one line: aider --yes $aiderFiles
4. A plain (unfenced-language) triple-backtick code block whose BODY is a
   detailed implementation prompt you write for the local LLM. The body must
   contain real content -- concrete type signatures, imports, dataclass
   definitions, pseudocode, error-handling guidance -- extracted from the
   architecture context above, sized so a single local LLM call can implement
   the listed files end-to-end. Placeholder strings or angle-bracket stubs are
   NOT acceptable; only real generated prose and code.

CROSS-FILE CONSISTENCY (critical -- the aider steps are generated
independently and one step cannot see what another produced, so symbol names
must match the architecture context verbatim):
- Use the EXACT class names, function names, method names, attribute names,
  parameter names, and module paths that appear in the architecture context
  above. Do not invent synonyms (e.g. if the plan defines "Config", do not
  emit "ConfigLoader" or "AppConfig").
- Preserve the exact signatures shown in the plan -- same parameter order,
  same parameter types, same return type, same default values.
- Import paths must match the project structure in the plan (e.g. if the
  plan shows "from nmon.config import Config", use that exact path, not
  "from nmon.configuration import Config").
- If the plan is ambiguous about a name, choose the simplest literal form
  from the plan text and stick to it; do not elaborate.

Begin your response with the "## Step" heading.
"@

            $stepThinkFile = Join-Path $TargetDir (".step_{0}.thinking.md" -f $stepNum)
            $stepResult = Invoke-StagePrint -Prompt $stepPrompt -SubStage "3b" -ThinkingFile $stepThinkFile
            $stepText = ($stepResult -join "`n").Trim()

            # Append to aidercommands.md
            "`n$stepText`n`n---`n" | Out-File -FilePath $AiderCommands -Append -Encoding utf8
            Write-Host "    Step $stepNum/$totalSteps - done" -ForegroundColor Green

            # Save sub-step progress
            Save-Progress 2 -SubStep $stepNum
        }

        # All steps done - clean up step plan and mark stage 3 complete
        if (Test-Path $StepPlanFile) { Remove-Item $StepPlanFile }
        Save-Progress 3
        Write-Host "  All $totalSteps steps generated in aidercommands.md" -ForegroundColor Green
    }
    elseif ($DryRun) {
        Write-Host "  [DRY RUN] Would generate individual steps" -ForegroundColor DarkGray
    }
}
else {
    Write-Host "`n  Skipping Stage 3 (Generate Aider Commands)" -ForegroundColor DarkGray
}

# ── Summary ──────────────────────────────────────────────────────────

# Clean up progress file on successful completion
if (-not $DryRun) { Clear-Progress }

$bar = "=" * 60
Write-Host "`n$bar" -ForegroundColor Green
Write-Host "  Pipeline complete." -ForegroundColor Green
if ($implementedPlans.Count -gt 0) { Write-Host "    Stage 0: Codebase Summary.md ($($implementedPlans.Count) plan(s))" }
if (Should-RunStage 1) { Write-Host "    Stage 1: Implementation Planning Prompt.md + PromptUpdates.md" }
if (Should-RunStage 2) { Write-Host "    Stage 2: Architecture Plan.md" }
if (Should-RunStage 3) { Write-Host "    Stage 3: aidercommands.md" }
Write-Host $bar -ForegroundColor Green

if (-not $DryRun) {
    Write-Host "`n  Next: python LocalLLMCoding/run_aider.py" -ForegroundColor Yellow
}
