# Resolve NACE_API_KEY from Bitwarden into this process only, run the live probe, then remove it.
# The secret value is never printed and never written to a file.
$ErrorActionPreference = 'Stop'
$repo = 'C:\Users\elekt\Projets\jev-auto-effort'
$outDir = Join-Path $repo 'docs\validation\runs\nace-drex-max-20261119'
$env:HERMES_SOURCE_ROOT = 'C:\Users\elekt\AppData\Local\hermes\hermes-agent'

try {
    $listRaw = & bws secret list --output json
    if ($LASTEXITCODE -ne 0) {
        if ("$listRaw" -match '401') { Write-Output 'STOP: bws secret list returned 401' }
        throw "bws secret list failed (exit $LASTEXITCODE)"
    }
    $list = $listRaw | ConvertFrom-Json
    $match = @($list | Where-Object { $_.key -ceq 'NACE_API_KEY' })
    if ($match.Count -eq 0) { throw 'NACE_API_KEY not found in Bitwarden' }
    $secretId = [string]$match[0].id

    $secretRaw = & bws secret get $secretId --output json
    if ($LASTEXITCODE -ne 0) {
        if ("$secretRaw" -match '401') { Write-Output 'STOP: bws secret get returned 401' }
        throw "bws secret get failed (exit $LASTEXITCODE)"
    }
    $secret = $secretRaw | ConvertFrom-Json
    $token = [string]$secret.value
    if ([string]::IsNullOrWhiteSpace($token)) { throw 'NACE_API_KEY value is empty' }
    $env:NACE_API_KEY = $token
    Write-Output "key_resolved=True key_id=$secretId"

    & "$repo\.venv\Scripts\python.exe" (Join-Path $outDir 'probe.py') $repo $outDir
    $probeExit = $LASTEXITCODE
    Write-Output "probe_exit=$probeExit"
    if ($probeExit -ne 0) { throw "probe failed (exit $probeExit)" }
}
finally {
    if (Test-Path Env:NACE_API_KEY) { Remove-Item Env:NACE_API_KEY }
    Write-Output 'env_removed=True'
}
