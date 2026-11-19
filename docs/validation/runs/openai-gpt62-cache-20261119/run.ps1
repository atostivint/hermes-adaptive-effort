# Resolve OPENAI_API_KEY from Bitwarden Secrets Manager, run the GPT-6.2 cache probe,
# then remove the env var. The secret value is never printed and never written to a file.
$ErrorActionPreference = 'Stop'

$Probe = Join-Path $PSScriptRoot 'probe_gpt62_cache.py'
$Out = Join-Path $PSScriptRoot 'cache.jsonl'
$PyCandidate = Join-Path (Split-Path (Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent) -Parent) '.venv\Scripts\python.exe'
if (Test-Path $PyCandidate) { $Py = $PyCandidate } else { $Py = 'python' }

$env_removed = $false

try {
    $raw = bws secret list --output json
    if ($LASTEXITCODE -ne 0) {
        Write-Output 'STOP: bws secret list failed; key unavailable. Nothing sent.'
        exit 2
    }
    $entries = @($raw | ConvertFrom-Json | Where-Object { $_.key -eq 'OPENAI_API_KEY' })
    Write-Output "credential_entries=$($entries.Count)"
    if ($entries.Count -eq 0) {
        Write-Output 'STOP: OPENAI_API_KEY not present in Bitwarden Secrets Manager. Nothing sent.'
        exit 2
    }

    # Resolve values for up to two candidate entries. Values stay in variables only.
    $keys = @()
    foreach ($entry in ($entries | Select-Object -First 2)) {
        $secretJson = bws secret get $entry.id --output json
        if ($LASTEXITCODE -ne 0) { continue }
        try {
            $secret = $secretJson | ConvertFrom-Json
        } catch {
            continue
        }
        $value = $secret.value
        if ($value) { $keys += $value }
    }
    Write-Output "keys_resolved=$($keys.Count)"
    if ($keys.Count -eq 0) {
        Write-Output 'STOP: could not read any OPENAI_API_KEY value from Bitwarden. Nothing sent.'
        exit 2
    }

    $code = -1
    $attempt = 0
    foreach ($key in $keys) {
        $attempt += 1
        $env:OPENAI_API_KEY = $key
        Write-Output "probe_attempt=$attempt"
        & $Py $Probe --out $Out --model 'gpt-6.2-sol' --fallback 'gpt-6.2-astra'
        $code = $LASTEXITCODE
        Remove-Item Env:OPENAI_API_KEY -ErrorAction SilentlyContinue
        if ($code -eq 3) {
            Write-Output 'attempt_result=401; trying next key once'
            continue
        }
        break
    }

    if ($code -eq 3) {
        Write-Output 'STOP: OPENAI_API_KEY rejected (401) for every candidate entry.'
        exit 3
    }
    if ($code -eq 4) {
        Write-Output 'STOP: model inaccessible (gpt-6.2-sol and gpt-6.2-astra both unavailable).'
        exit 4
    }
    if ($code -ne 0) {
        Write-Output "STOP: probe exited with code $code."
        exit $code
    }
    Write-Output 'probe_result=ok'
}
finally {
    Remove-Item Env:OPENAI_API_KEY -ErrorAction SilentlyContinue
    $env_removed = $true
}

Write-Output "env_removed=$env_removed"
