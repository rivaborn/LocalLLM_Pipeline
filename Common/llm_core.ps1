# ============================================================
# llm_core.ps1 -- LLM invocation + environment infrastructure
#
# Location: LocalLLM_Pipeline/Common/llm_core.ps1
# Split from llm_common.ps1. Contains:
#   Get-LLMEndpoint    - Resolve Ollama endpoint URL
#   Test-CancelKey     - Poll for Ctrl+Q (quiet exit)
#   Invoke-LocalLLM    - Call Ollama chat API (native + OpenAI compat)
#   Read-EnvFile       - Parse .env key=value files
#   Cfg                - Config key lookup with default
# ============================================================

function Get-LLMEndpoint {
    if ($env:LLM_ENDPOINT) { return $env:LLM_ENDPOINT.TrimEnd('/') }
    $ep = Cfg 'LLM_ENDPOINT' ''
    if ($ep -ne '') { return $ep.TrimEnd('/') }
    $host_ = Cfg 'LLM_HOST' '192.168.1.126'
    $port  = Cfg 'LLM_PORT' '11434'
    return "http://${host_}:${port}"
}

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

function Invoke-LocalLLM {
    param(
        [string]$SystemPrompt,
        [string]$UserPrompt,
        [string]$Endpoint    = '',
        [string]$Model       = 'qwen2.5-coder:14b',
        [double]$Temperature = 0.1,
        [int]   $MaxTokens   = 800,
        [int]   $NumCtx      = -1,
        [int]   $Timeout     = 120,
        [int]   $MaxRetries  = 3,
        [int]   $RetryDelay  = 5,
        [bool]  $Think       = $false,
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

function Cfg($key, $default = '') {
    if ($script:cfg -and $script:cfg.ContainsKey($key) -and $script:cfg[$key] -ne '') { return $script:cfg[$key] }
    return $default
}
