# ============================================================
# bughunt_iter_helpers.ps1 -- Utility functions for
#                             bughunt_iterative_local.ps1
#
# Extracted so the main orchestrator stays focused on the
# procedural source-loop and test-loop logic. Dot-source:
#   . (Join-Path $PSScriptRoot 'bughunt_iter_helpers.ps1')
# ============================================================

function Count-Severity($text, $tag) {
    return ([regex]::Matches($text, [regex]::Escape($tag))).Count
}

function Needs-Fix($report) {
    return (Count-Severity $report '[HIGH]') -gt 0 -or (Count-Severity $report '[MEDIUM]') -gt 0
}

function Extract-CodeBlock($response, $fence) {
    $tb = '``' + '`'   # triple-backtick (avoids literal fence in source)
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

function Find-TestFile($srcRel) {
    $stem  = [System.IO.Path]::GetFileNameWithoutExtension($srcRel)
    $dir   = ([System.IO.Path]::GetDirectoryName($srcRel) -replace '\\', '/').Trim('/')
    $parts = @($dir -split '/' | Where-Object { $_ -ne '' -and $_ -ne 'src' })

    $c1 = Join-Path $testRoot ('test_' + (($parts + @($stem)) -join '_') + '.py')
    $c2 = Join-Path $testRoot ('test_' + $stem + '.py')
    $stem3 = $stem -replace '_(source|base|impl|provider|backend)$', ''
    $c3 = Join-Path $testRoot ('test_' + (($parts + @($stem3)) -join '_') + '.py')

    foreach ($c in @($c1, $c2, $c3)) {
        if (Test-Path $c) { return $c }
    }
    return ''
}

function Sum-Field($collection, $field) {
    $total = 0
    foreach ($item in $collection) { $total += [int]$item.$field }
    return $total
}
