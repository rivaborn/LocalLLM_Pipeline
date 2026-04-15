# ============================================================
# llm_common.ps1 -- Shared helper module for local LLM pipelines
#
# Location: LocalLLM_Pipeline/Common/llm_common.ps1
# Dot-source with:
#   . (Join-Path $PSScriptRoot '..\Common\llm_common.ps1')
#
# Provides:
#   Invoke-LocalLLM    - Call Ollama (OpenAI-compat or native /api/chat)
#   Get-LLMEndpoint    - Resolve endpoint (LLM_ENDPOINT, else LLM_HOST+PORT)
#   Read-EnvFile       - Parse .env key=value files
#   Get-SHA1           - SHA1 hash of a file
#   Get-Preset         - Engine preset definitions
#   Get-FenceLang      - Map file extension to markdown fence language
#   Test-TrivialFile   - Detect generated/trivial files
#   Write-TrivialStub  - Write a stub doc for trivial files
#   Get-OutputBudget   - Adaptive output token budget
# ============================================================

# ---------------------------------------------------------------------------
# Get-LLMEndpoint -- Resolve endpoint URL.
#
# Precedence:
#   1. $env:LLM_ENDPOINT
#   2. LLM_ENDPOINT in .env
#   3. LLM_HOST + LLM_PORT in .env
#   4. Hardcoded default http://192.168.1.126:11434
# ---------------------------------------------------------------------------

function Get-LLMEndpoint {
    if ($env:LLM_ENDPOINT) { return $env:LLM_ENDPOINT.TrimEnd('/') }
    $ep = Cfg 'LLM_ENDPOINT' ''
    if ($ep -ne '') { return $ep.TrimEnd('/') }
    $host_ = Cfg 'LLM_HOST' '192.168.1.126'
    $port  = Cfg 'LLM_PORT' '11434'
    return "http://${host_}:${port}"
}

# ---------------------------------------------------------------------------
# Test-CancelKey -- Poll for Ctrl+Q between long operations
#
# Call at the top of per-file loops / iteration loops. Cannot interrupt
# mid-HTTP-call (Ctrl+C still works for that); this provides an additional
# "quiet exit" that only triggers on explicit Ctrl+Q. Safe to call in tight
# loops -- it's non-blocking and returns immediately if no key is pending.
#
# Silently no-ops when stdin is redirected (e.g. piped input, CI), so
# scripts remain non-interactive-safe.
# ---------------------------------------------------------------------------

function Test-CancelKey {
    try {
        if ([Console]::IsInputRedirected) { return }
    } catch {
        return
    }
    while ([Console]::KeyAvailable) {
        $k = [Console]::ReadKey($true)
        if ($k.Key -eq [ConsoleKey]::Q -and
            ($k.Modifiers -band [ConsoleModifiers]::Control)) {
            Write-Host ''
            Write-Host '[Ctrl+Q] User cancelled. Exiting cleanly...' -ForegroundColor Yellow
            exit 130
        }
    }
}

# ---------------------------------------------------------------------------
# Invoke-LocalLLM -- Call Ollama chat API.
#
# Two modes:
#   NumCtx == 0  -> OpenAI-compat /v1/chat/completions (legacy, unchanged)
#   NumCtx  > 0  -> Native /api/chat with options.num_ctx (required for
#                   per-request context window override)
# ---------------------------------------------------------------------------

function Invoke-LocalLLM {
    param(
        [string]$SystemPrompt,
        [string]$UserPrompt,
        [string]$Endpoint    = '',
        [string]$Model       = 'qwen2.5-coder:14b',
        [double]$Temperature = 0.1,
        [int]   $MaxTokens   = 800,
        # NumCtx: -1 = auto from .env LLM_NUM_CTX, 0 = legacy OpenAI-compat path, >0 = native /api/chat
        [int]   $NumCtx      = -1,
        [int]   $Timeout     = 120,
        [int]   $MaxRetries  = 3,
        [int]   $RetryDelay  = 5,
        # Thinking-model support (native /api/chat only). When $true, sends
        # think:true and splits message.thinking from message.content.
        [bool]  $Think       = $false,
        # Optional sidecar path: if set and the response contains thinking,
        # reasoning tokens are written here (UTF-8). Content is returned as usual.
        [string]$ThinkingFile = ''
    )

    if ($NumCtx -lt 0) {
        $NumCtx = [int](Cfg 'LLM_NUM_CTX' '0')
    }

    if (-not $Endpoint -or $Endpoint -eq '') {
        $Endpoint = Get-LLMEndpoint
    }
    $Endpoint = $Endpoint.TrimEnd('/')

    $messages = @()
    if ($SystemPrompt -and $SystemPrompt.Trim() -ne '') {
        $messages += @{ role = 'system'; content = $SystemPrompt }
    }
    $messages += @{ role = 'user'; content = $UserPrompt }

    if ($NumCtx -gt 0) {
        # Native Ollama endpoint -- supports options.num_ctx
        $uri = "$Endpoint/api/chat"
        $bodyHash = @{
            model    = $Model
            messages = $messages
            stream   = $false
            options  = @{
                num_ctx     = $NumCtx
                temperature = $Temperature
                num_predict = $MaxTokens
            }
        }
        if ($Think) { $bodyHash.think = $true }
    }
    else {
        # Legacy OpenAI-compat endpoint (preserves existing analysis-script behavior)
        $uri = "$Endpoint/v1/chat/completions"
        $bodyHash = @{
            model       = $Model
            messages    = $messages
            stream      = $false
            temperature = $Temperature
            max_tokens  = $MaxTokens
        }
    }

    $body = $bodyHash | ConvertTo-Json -Depth 5

    $attempt = 0
    while ($true) {
        $attempt++
        try {
            $resp = Invoke-RestMethod -Uri $uri `
                -Method Post `
                -ContentType 'application/json; charset=utf-8' `
                -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) `
                -TimeoutSec $Timeout `
                -ErrorAction Stop

            $thinking = $null
            if ($NumCtx -gt 0) {
                $output   = $resp.message.content
                # Ollama omits "thinking" when the model didn't emit any; accessing a
                # missing property throws under strict mode, so probe defensively.
                if ($resp.message -and ($resp.message.PSObject.Properties.Name -contains 'thinking')) {
                    $thinking = $resp.message.thinking
                }
                if ($ThinkingFile -and $thinking -and $thinking.Trim() -ne '') {
                    try {
                        $thinking | Out-File -FilePath $ThinkingFile -Encoding utf8
                    } catch {
                        Write-Host "  [warn] Could not write thinking sidecar '$ThinkingFile': $($_.Exception.Message)" -ForegroundColor DarkYellow
                    }
                }
            }
            else {
                $output = $resp.choices[0].message.content
            }
            if (-not $output -or $output.Trim() -eq '') {
                if ($NumCtx -gt 0 -and $thinking -and $thinking.Trim() -ne '') {
                    $tLen = $thinking.Length
                    throw "Model exhausted budget inside <thinking> (thinking=$tLen chars, num_predict=$MaxTokens). Raise LLM_PLANNING_MAX_TOKENS."
                }
                throw "Empty response from LLM"
            }
            $trimmed = $output.Trim()
            # Sanity: thinking models sometimes burn their full num_predict on reasoning
            # and then emit a single stray stop-token as "content" (e.g. one CJK char).
            # Ollama still reports done=stop, so the only signal is the output's shape.
            # Reject anything too short or lacking ASCII letters/digits -- every real
            # pipeline response contains at least one structured line, far above this bar.
            $hasAscii = $trimmed -match '[A-Za-z0-9]'
            if ($trimmed.Length -lt 20 -or -not $hasAscii) {
                $preview = $trimmed.Substring(0, [Math]::Min(60, $trimmed.Length))
                $msg = "LLM returned suspiciously short/garbled content ($($trimmed.Length) chars: '$preview')"
                if ($NumCtx -gt 0 -and $thinking -and $thinking.Trim() -ne '') {
                    $msg += " -- thinking=$($thinking.Length) chars suggests budget exhaustion during reasoning."
                }
                throw $msg
            }
            return $trimmed
        }
        catch {
            if ($attempt -ge $MaxRetries) {
                throw "LLM call failed after $MaxRetries attempts: $($_.Exception.Message)"
            }
            Write-Host "  [retry $attempt/$MaxRetries] $($_.Exception.Message)" -ForegroundColor Yellow
            Start-Sleep -Seconds $RetryDelay
        }
    }
}

# ---------------------------------------------------------------------------
# Read-EnvFile -- Parse a .env file into a hashtable
# ---------------------------------------------------------------------------

function Read-EnvFile($path) {
    $vars = @{}
    if (Test-Path $path) {
        Get-Content $path | ForEach-Object {
            $line = $_.Trim()
            if ($line -match '^#' -or $line -eq '') { return }
            if ($line -match '^([^=]+)=(.*)$') {
                $key = $Matches[1].Trim()
                $val = $Matches[2].Trim().Trim('"').Trim("'")
                $val = $val -replace [regex]::Escape('$HOME'), $env:USERPROFILE
                $val = $val -replace '^~', $env:USERPROFILE
                $vars[$key] = $val
            }
        }
    }
    return $vars
}

# ---------------------------------------------------------------------------
# Cfg -- Read a config key with a default fallback
# ---------------------------------------------------------------------------

function Cfg($key, $default = '') {
    if ($script:cfg -and $script:cfg.ContainsKey($key) -and $script:cfg[$key] -ne '') { return $script:cfg[$key] }
    return $default
}

# ---------------------------------------------------------------------------
# Get-SHA1 -- SHA1 hash of a file (for incremental skip logic)
# ---------------------------------------------------------------------------

function Get-SHA1($filePath) {
    $sha   = [System.Security.Cryptography.SHA1]::Create()
    $bytes = [System.IO.File]::ReadAllBytes($filePath)
    return ($sha.ComputeHash($bytes) | ForEach-Object { $_.ToString('x2') }) -join ''
}

# ---------------------------------------------------------------------------
# Get-Preset -- Engine preset definitions (include/exclude patterns)
# ---------------------------------------------------------------------------

function Get-Preset($name) {
    switch ($name.ToLower()) {
        { $_ -in @('quake','quake2','quake3','doom','idtech') } {
            return @{
                Include = '\.(c|cc|cpp|cxx|h|hh|hpp|inl|inc)$'
                Exclude = '[/\\](\.git|architecture|build|out|dist|obj|bin|Debug|Release|x64|Win32|\.vs|\.vscode|baseq2|baseq3|base)([/\\]|$)'
                Desc    = 'C game engine codebase (id Software / Quake-family)'
                Fence   = 'c'
            }
        }
        { $_ -in @('unreal','ue4','ue5') } {
            return @{
                Include = '\.(cpp|h|hpp|cc|cxx|inl|cs)$'
                Exclude = '[/\\](\.git|architecture|Binaries|Build|DerivedDataCache|Intermediate|Saved|\.vs|ThirdParty|GeneratedFiles|AutomationTool)([/\\]|$)'
                Desc    = 'Unreal Engine C++/C# source (Epic Games)'
                Fence   = 'cpp'
            }
        }
        'godot' {
            return @{
                Include = '\.(cpp|h|hpp|c|cc|gd|gdscript|tscn|tres|cs)$'
                Exclude = '[/\\](\.git|architecture|\.godot|\.import|build|export)([/\\]|$)'
                Desc    = 'Godot engine codebase (C++/GDScript/C#)'
                Fence   = 'cpp'
            }
        }
        'unity' {
            return @{
                Include = '\.(cs|shader|cginc|hlsl|compute|glsl|cpp|c|h)$'
                Exclude = '[/\\](\.git|architecture|Library|Temp|Obj|Build|Builds|Logs|UserSettings|\.vs)([/\\]|$)'
                Desc    = 'Unity game codebase (C#/shader)'
                Fence   = 'csharp'
            }
        }
        { $_ -in @('source','valve') } {
            return @{
                Include = '\.(cpp|h|hpp|c|cc|cxx|inl|inc|vpc|vgc)$'
                Exclude = '[/\\](\.git|architecture|build|out|obj|bin|Debug|Release|lib|thirdparty)([/\\]|$)'
                Desc    = 'Source Engine codebase (Valve / C++)'
                Fence   = 'cpp'
            }
        }
        'rust' {
            return @{
                Include = '\.(rs|toml)$'
                Exclude = '[/\\](\.git|architecture|target|\.cargo)([/\\]|$)'
                Desc    = 'Rust game engine codebase'
                Fence   = 'rust'
            }
        }
        { $_ -in @('python','py') } {
            return @{
                Include = '\.(py|toml)$'
                Exclude = '[/\\](\.git|architecture|__pycache__|\.egg-info|\.tox|\.venv|venv|dist|build|\.pytest_cache|\.mypy_cache)([/\\]|$)'
                Desc    = 'Python codebase'
                Fence   = 'python'
            }
        }
        { $_ -in @('generals','cnc','sage') } {
            return @{
                Include = '\.(cpp|h|hpp|c|cc|cxx|inl|inc)$'
                Exclude = '[/\\](\.git|architecture|Debug|Release|x64|Win32|\.vs|Run|place_steam_build_here)([/\\]|$)'
                Desc    = 'Command & Conquer Generals / Zero Hour (SAGE engine, EA/Westwood, C++)'
                Fence   = 'cpp'
            }
        }
        '' {
            return @{
                Include = '\.(c|cc|cpp|cxx|h|hh|hpp|inl|inc|cs|java|py|rs|lua|gd|gdscript|m|mm|swift)$'
                Exclude = '[/\\](\.git|architecture|build|out|dist|obj|bin|Debug|Release|\.vs|\.vscode|node_modules|\.godot|Library|Temp)([/\\]|$)'
                Desc    = 'game engine / game codebase'
                Fence   = 'c'
            }
        }
        default {
            Write-Host "Unknown preset: $name. Available: quake, doom, unreal, godot, unity, source, rust, generals, python" -ForegroundColor Red
            exit 2
        }
    }
}

# ---------------------------------------------------------------------------
# Get-FenceLang -- Map file extension to markdown fence language
# ---------------------------------------------------------------------------

function Get-FenceLang($file, $def) {
    $ext = [System.IO.Path]::GetExtension($file).TrimStart('.').ToLower()
    switch ($ext) {
        { $_ -in @('c','h','inc') }                             { return 'c' }
        { $_ -in @('cpp','cc','cxx','hpp','hh','hxx','inl') }   { return 'cpp' }
        'cs'     { return 'csharp' }
        'java'   { return 'java' }
        'py'     { return 'python' }
        'rs'     { return 'rust' }
        'lua'    { return 'lua' }
        { $_ -in @('gd','gdscript') }                           { return 'gdscript' }
        'swift'  { return 'swift' }
        { $_ -in @('m','mm') }                                  { return 'objectivec' }
        { $_ -in @('shader','cginc','hlsl','glsl','compute') }  { return 'hlsl' }
        'toml'   { return 'toml' }
        { $_ -in @('tscn','tres') }                             { return 'ini' }
        default  { return $def }
    }
}

# ---------------------------------------------------------------------------
# Trivial file detection
# ---------------------------------------------------------------------------

$script:trivialPatterns = @(
    '\.generated\.h$',
    '\.gen\.cpp$',
    '^Module\.[A-Za-z0-9_]+\.cpp$',
    'Classes\.h$'
)

function Test-TrivialFile($rel, $fullPath, $minLines) {
    $leaf = Split-Path $rel -Leaf
    foreach ($pat in $script:trivialPatterns) {
        if ($leaf -match $pat) { return $true }
    }
    $lines = @(Get-Content $fullPath -ErrorAction SilentlyContinue)
    if ($lines.Count -lt $minLines) { return $true }
    $nonInclude = $lines | Where-Object {
        $_.Trim() -ne '' -and
        $_ -notmatch '^\s*(#\s*(include|pragma|ifndef|define|endif)|//|/\*|\*/)'
    }
    if (@($nonInclude).Count -le 2) { return $true }
    return $false
}

function Write-TrivialStub($rel, $outPath) {
    $stub = "# $rel`n`n## Purpose`nAuto-generated or trivial file. No detailed analysis needed.`n`n## Responsibilities`n- Boilerplate / generated code`n"
    $stub | Set-Content -Path $outPath -Encoding UTF8
}

# ---------------------------------------------------------------------------
# Get-OutputBudget -- Adaptive output token budget based on file size
# ---------------------------------------------------------------------------

function Get-OutputBudget($lineCount) {
    if ($lineCount -lt 50)  { return 300 }
    if ($lineCount -lt 200) { return 400 }
    if ($lineCount -lt 500) { return 600 }
    return 800
}

# ---------------------------------------------------------------------------
# Truncate-Source -- Cap source lines with head+tail truncation
# ---------------------------------------------------------------------------

function Truncate-Source($srcLines, $maxLines) {
    if ($maxLines -le 0 -or $srcLines.Count -le $maxLines) {
        return ($srcLines -join "`n")
    }
    $half = [int]($maxLines / 2)
    $head = $srcLines | Select-Object -First $half
    $tail = $srcLines | Select-Object -Last  $half
    $note = "/* ... TRUNCATED: showing first $half and last $half of $($srcLines.Count) lines ... */"
    return ($head -join "`n") + "`n`n$note`n`n" + ($tail -join "`n")
}

# ---------------------------------------------------------------------------
# Resolve-ArchFile -- Find a file under the configured ARCHITECTURE_DIR.
#
# Returns the full path if the file exists, or an empty string if
# ARCHITECTURE_DIR is unset, the directory doesn't exist, or the file is
# missing. Used by Debug-pipeline scripts to optionally consume
# LocalLLMAnalysis outputs (xref_index.md, architecture.md, etc.) without
# failing when Analysis hasn't been run.
# ---------------------------------------------------------------------------

function Resolve-ArchFile {
    param(
        [string]$Name,
        [string]$BaseDir = ''
    )
    $archDir = Cfg 'ARCHITECTURE_DIR' ''
    if (-not $archDir) { return '' }
    if (-not $BaseDir) { $BaseDir = (Get-Location).Path }

    if ([System.IO.Path]::IsPathRooted($archDir)) {
        $candidate = Join-Path $archDir $Name
    } else {
        $candidate = Join-Path (Join-Path $BaseDir $archDir) $Name
    }
    if (Test-Path $candidate -PathType Leaf) {
        return (Resolve-Path $candidate).Path
    }
    return ''
}

# ---------------------------------------------------------------------------
# Get-SerenaContextDir -- Resolve the SERENA_CONTEXT_DIR from config.
#
# Returns the absolute directory path if configured and exists, else empty
# string. Paired with Load-CompressedLSP for per-file LSP context injection.
# ---------------------------------------------------------------------------

function Get-SerenaContextDir {
    param([string]$BaseDir = '')
    $d = Cfg 'SERENA_CONTEXT_DIR' ''
    if (-not $d) { return '' }
    if (-not $BaseDir) { $BaseDir = (Get-Location).Path }
    if (-not [System.IO.Path]::IsPathRooted($d)) {
        $d = Join-Path $BaseDir $d
    }
    if (Test-Path $d -PathType Container) { return $d }
    return ''
}

# ---------------------------------------------------------------------------
# Load-CompressedLSP -- Load LSP context, keeping only Symbol Overview section
# ---------------------------------------------------------------------------

function Load-CompressedLSP($serenaContextDir, $rel) {
    if (-not $serenaContextDir -or $serenaContextDir -eq '') { return '' }
    $ctxPath = Join-Path $serenaContextDir (($rel -replace '/','\') + '.serena_context.txt')
    if (-not (Test-Path $ctxPath)) { return '' }

    $content = Get-Content $ctxPath -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
    if (-not $content) { return '' }

    # Extract only Symbol Overview section (drop references, trimmed source to save tokens)
    $match = [regex]::Match($content, '(?s)(## Symbol Overview.*?)(?=\n## (?!Symbol)|$)')
    if ($match.Success) {
        return $match.Groups[1].Value.Trim()
    }
    return ''
}

# ---------------------------------------------------------------------------
# Show-SimpleProgress -- Single-line progress display for synchronous loop
# ---------------------------------------------------------------------------

function Show-SimpleProgress($done, $total, $startTime) {
    $elapsed = ([datetime]::Now - $startTime).TotalSeconds
    $rate    = if ($elapsed -gt 0 -and $done -gt 0) { [math]::Round($done / $elapsed, 2) } else { 0 }
    $etaSec  = if ($rate -gt 0) { [math]::Round(($total - $done) / $rate) } else { 0 }
    if ($etaSec -gt 0) {
        $etaH = [int][math]::Floor($etaSec / 3600)
        $etaM = [int][math]::Floor(($etaSec % 3600) / 60)
        $etaS = [int]($etaSec % 60)
        $eta  = '{0}h{1:D2}m{2:D2}s' -f $etaH, $etaM, $etaS
    } else { $eta = '?' }
    $line = "PROGRESS: $done/$total  rate=${rate}/s  eta=$eta"
    [Console]::Write("`r" + $line.PadRight(80))
}
