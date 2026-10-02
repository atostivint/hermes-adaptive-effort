param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RuffArgs
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$Ruff = Join-Path $RepoRoot ".venv\Scripts\ruff.exe"
if (-not (Test-Path $Ruff)) {
    throw "No Windows venv found. Run .\scripts\bootstrap_test_env.ps1 first."
}

& $Ruff check . @RuffArgs
exit $LASTEXITCODE
