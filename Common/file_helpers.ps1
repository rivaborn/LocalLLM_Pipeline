# ============================================================
# file_helpers.ps1 -- File processing, presets, and display helpers
#
# Location: LocalLLM_Pipeline/Common/file_helpers.ps1
# Split from llm_common.ps1. Contains:
#   Get-SHA1            - SHA1 hash of a file
#   Get-Preset          - Engine preset definitions (file patterns)
#   Get-FenceLang       - Map file extension to markdown fence language
#   Test-TrivialFile    - Detect generated/trivial files
#   Write-TrivialStub   - Write a stub doc for trivial files
#   Get-OutputBudget    - Adaptive output token budget
#   Truncate-Source     - Head+tail source truncation
#   Resolve-ArchFile    - Find file under ARCHITECTURE_DIR
#   Get-SerenaContextDir- Resolve SERENA_CONTEXT_DIR
#   Load-CompressedLSP  - Load LSP context (Symbol Overview only)
#   Show-SimpleProgress - Single-line progress display
# ============================================================

function Get-SHA1($filePath) {
    $sha   = [System.Security.Cryptography.SHA1]::Create()
    $bytes = [System.IO.File]::ReadAllBytes($filePath)
    return ($sha.ComputeHash($bytes) | ForEach-Object { $_.ToString('x2') }) -join ''
}

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

function Get-OutputBudget($lineCount) {
    if ($lineCount -lt 50)  { return 300 }
    if ($lineCount -lt 200) { return 400 }
    if ($lineCount -lt 500) { return 600 }
    return 800
}

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

function Load-CompressedLSP($serenaContextDir, $rel) {
    if (-not $serenaContextDir -or $serenaContextDir -eq '') { return '' }
    $ctxPath = Join-Path $serenaContextDir (($rel -replace '/','\') + '.serena_context.txt')
    if (-not (Test-Path $ctxPath)) { return '' }

    $content = Get-Content $ctxPath -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
    if (-not $content) { return '' }

    $match = [regex]::Match($content, '(?s)(## Symbol Overview.*?)(?=\n## (?!Symbol)|$)')
    if ($match.Success) {
        return $match.Groups[1].Value.Trim()
    }
    return ''
}

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
