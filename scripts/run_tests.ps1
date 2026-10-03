param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "No Windows venv found. Run .\scripts\bootstrap_test_env.ps1 first."
}

# The plugin imports agent.reasoning_effort for effort clamping. Without that core
# the mapping refuses (by design) and ~40 mapping/rewrite tests fail with an
# unrelated-looking assertion, so discover the source tree here -- the same way
# tests/conftest.py does -- and say so loudly instead of letting the run look broken.
if (-not $env:HERMES_SOURCE_ROOT) {
    $Candidates = @()
    if ($env:HERMES_HOME) {
        $Candidates += (Join-Path $env:HERMES_HOME "hermes-agent")
    }
    $Candidates += (Join-Path (Split-Path -Parent $RepoRoot) "hermes-agent")
    if ($env:LOCALAPPDATA) {
        $Candidates += (Join-Path $env:LOCALAPPDATA "hermes")
    }
    foreach ($Candidate in $Candidates) {
        if (Test-Path (Join-Path $Candidate "agent\reasoning_effort.py")) {
            $env:HERMES_SOURCE_ROOT = $Candidate
            break
        }
    }
}

if ($env:HERMES_SOURCE_ROOT -and -not (Test-Path (Join-Path $env:HERMES_SOURCE_ROOT "agent\reasoning_effort.py"))) {
    throw "HERMES_SOURCE_ROOT must point to a Hermes source tree containing agent\reasoning_effort.py."
}

if (-not $env:HERMES_SOURCE_ROOT) {
    Write-Warning "No Hermes source tree found (agent/reasoning_effort.py). Effort mapping will refuse and the mapping tests will fail; set HERMES_SOURCE_ROOT to a Hermes checkout."
}

# pytest's default %TEMP%\pytest-of-<user> and the repo .pytest_cache can be left
# behind by a process with a foreign ACL, which errors every tmp_path test (the
# dispatcher integration ones) on Windows. Fall back to scratch/cache-less paths
# this user owns.
function Test-ReadableDir([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $true }
    try {
        Get-ChildItem -LiteralPath $Path -ErrorAction Stop | Out-Null
        return $true
    } catch {
        return $false
    }
}

$EnvArgs = @()
$DefaultBaseTemp = Join-Path $env:TEMP "pytest-of-$env:USERNAME"
if (-not (Test-ReadableDir $DefaultBaseTemp)) {
    $Scratch = Join-Path $env:TEMP "hermes-adaptive-effort-pytest"
    New-Item -ItemType Directory -Force -Path $Scratch | Out-Null
    $EnvArgs += "--basetemp=$Scratch"
    Write-Warning "$DefaultBaseTemp is not readable; using --basetemp $Scratch."
}

if (-not (Test-ReadableDir (Join-Path $RepoRoot ".pytest_cache"))) {
    $EnvArgs += @("-p", "no:cacheprovider")
}

& $Python -m pytest tests @PytestArgs @EnvArgs
exit $LASTEXITCODE