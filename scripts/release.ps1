param(
    [Parameter(Mandatory = $true)]
    [string]$Command,

    [Parameter(Mandatory = $false)]
    [string]$Version
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

# Ensure gh is available
try {
    $null = & gh --version 2>&1
} catch {
    throw "gh (GitHub CLI) is not installed"
}

# Determine Python to use
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

try {
    $null = & $Python --version 2>&1
} catch {
    throw "Python not found"
}

function Write-Usage {
    Write-Host "Usage:"
    Write-Host "  .\scripts\release.ps1 prepare <version>"
    Write-Host "  .\scripts\release.ps1 tag <version>"
    exit 2
}

function Prepare-Release {
    param([string]$Version)

    # Check clean working tree
    $status = & git status --porcelain
    if ($status) {
        Write-Error "Working tree is not clean" -ErrorAction Stop
    }

    # Fetch origin
    & git fetch origin
    if ($LASTEXITCODE -ne 0) { throw "git fetch failed" }

    # Create release branch
    $branch = "release/v$Version"
    Write-Host "Creating branch $branch from origin/master..."
    & git checkout -b $branch "origin/master"
    if ($LASTEXITCODE -ne 0) { throw "git checkout failed" }

    # Run the bump script
    Write-Host "Bumping version to $Version..."
    & $Python .github/scripts/release_notes.py bump $Version
    if ($LASTEXITCODE -ne 0) { throw "Bump failed" }

    # Run tests
    Write-Host "Running tests..."
    & (Join-Path $PSScriptRoot "run_tests.ps1")
    if ($LASTEXITCODE -ne 0) { throw "Tests failed" }

    # Commit
    $commitMsg = "release: v$Version"
    & git add -A
    if ($LASTEXITCODE -ne 0) { throw "git add failed" }

    & git commit -m $commitMsg
    if ($LASTEXITCODE -ne 0) { throw "git commit failed" }

    # Push branch
    Write-Host "Pushing branch $branch..."
    & git push -u origin $branch
    if ($LASTEXITCODE -ne 0) { throw "git push failed" }

    # Get release notes
    Write-Host "Creating pull request..."
    $notesFile = New-TemporaryFile
    & $Python .github/scripts/release_notes.py notes $Version | Out-File -FilePath $notesFile -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { throw "Failed to get release notes" }

    # Open PR
    & gh pr create --base master --title "release: v$Version" --body-file $notesFile
    if ($LASTEXITCODE -ne 0) { throw "gh pr create failed" }

    Remove-Item -Force $notesFile

    Write-Host ""
    Write-Host "After merge: .\scripts\release.ps1 tag $Version"
}

function Tag-Release {
    param([string]$Version)

    # Check clean working tree
    $status = & git status --porcelain
    if ($status) {
        Write-Error "Working tree is not clean" -ErrorAction Stop
    }

    # Checkout master
    & git checkout master
    if ($LASTEXITCODE -ne 0) { throw "git checkout failed" }

    # Pull latest
    & git pull --ff-only
    if ($LASTEXITCODE -ne 0) { throw "git pull failed" }

    # Verify version matches
    $pluginVersion = & $Python .github/scripts/release_notes.py version
    if ($LASTEXITCODE -ne 0) { throw "Failed to get plugin version" }

    if ($pluginVersion -ne $Version) {
        throw "plugin.yaml version '$pluginVersion' != '$Version'"
    }

    # Check tag doesn't exist locally
    $localTag = & git rev-parse "v$Version" 2>&1
    if ($LASTEXITCODE -eq 0) {
        throw "Tag v$Version already exists locally"
    }

    # Check tag doesn't exist on origin
    $remoteTag = & git ls-remote --tags origin "v$Version" 2>&1
    if ($remoteTag -match "refs/tags/v$Version") {
        throw "Tag v$Version already exists on origin"
    }

    # Create and push tag
    $tagMsg = "Hermes Adaptive Effort v$Version"
    & git tag -a "v$Version" -m $tagMsg
    if ($LASTEXITCODE -ne 0) { throw "git tag failed" }

    & git push origin "v$Version"
    if ($LASTEXITCODE -ne 0) { throw "git push tag failed" }

    Write-Host ""
    Write-Host "Tag v$Version pushed. Release workflow will publish it."
}

switch -Exact ($Command) {
    "prepare" {
        if (-not $Version) {
            Write-Usage
        }
        Prepare-Release $Version
    }
    "tag" {
        if (-not $Version) {
            Write-Usage
        }
        Tag-Release $Version
    }
    default {
        Write-Usage
    }
}
