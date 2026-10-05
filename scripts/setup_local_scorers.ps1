param(
    [switch]$SkipPortCheck
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Root = Join-Path $RepoRoot '.local-scorer'
$Downloads = Join-Path $Root 'downloads'
$Runtime = Join-Path $Root 'runtime'
$Models = Join-Path $Root 'models'
$Port = 8099
$MinimumFreeBytes = 12GB

function Assert-FreePort([int]$PortNumber) {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $PortNumber)
    try { $listener.Start() }
    catch { throw "Port $PortNumber is already occupied; stop the owner before setup." }
    finally { $listener.Stop() }
}

function Get-VerifiedFile([string]$Uri, [string]$Path, [string]$Sha256) {
    if (-not (Test-Path -LiteralPath $Path)) {
        Write-Host "Downloading $([IO.Path]::GetFileName($Path))"
        Invoke-WebRequest -Uri $Uri -OutFile $Path
    }
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $Sha256.ToLowerInvariant()) {
        Remove-Item -LiteralPath $Path -Force
        throw "SHA-256 mismatch for $Path (got $actual). The partial/bad archive was removed."
    }
}

function Expand-Once([string]$Archive, [string]$Destination, [string]$Marker) {
    if (Test-Path -LiteralPath $Marker) { return }
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Expand-Archive -LiteralPath $Archive -DestinationPath $Destination -Force
    New-Item -ItemType File -Force -Path $Marker | Out-Null
}

if (-not $SkipPortCheck) { Assert-FreePort $Port }
$drive = Get-PSDrive -Name ([IO.Path]::GetPathRoot($RepoRoot).TrimEnd('\').TrimEnd(':'))
if ($drive.Free -lt $MinimumFreeBytes) {
    throw "Only $([math]::Round($drive.Free / 1GB, 1)) GB free; need at least 12 GB for archives, extraction, and model files."
}

New-Item -ItemType Directory -Force -Path $Downloads, $Runtime, $Models | Out-Null
$assets = @(
    @{ Name = 'llama-b11396-bin-win-cuda-12.4-x64.zip'; Url = 'https://github.com/ggml-org/llama.cpp/releases/download/b11396/llama-b11396-bin-win-cuda-12.4-x64.zip'; Hash = 'd89b3f65ba49631cef6e1657542f66c20c0ca8f3f78175c2d3818f58f394ba32' },
    @{ Name = 'cudart-llama-bin-win-cuda-12.4-x64.zip'; Url = 'https://github.com/ggml-org/llama.cpp/releases/download/b11396/cudart-llama-bin-win-cuda-12.4-x64.zip'; Hash = '8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6' },
    @{ Name = 'llama-swap_262_windows_amd64.zip'; Url = 'https://github.com/mostlygeek/llama-swap/releases/download/v262/llama-swap_262_windows_amd64.zip'; Hash = 'fae4859493efb4ab42434c7010ee1585c320682c603415389a8ba3c3474c1ab8' }
)
foreach ($asset in $assets) {
    $target = Join-Path $Downloads $asset.Name
    Get-VerifiedFile $asset.Url $target $asset.Hash
    if ((Get-Item -LiteralPath $target).Length -lt 100KB) { throw "Unexpectedly small release archive: $target" }
}

$llamaDir = Join-Path $Runtime 'llama-b11396-cuda124'
$swapDir = Join-Path $Runtime 'llama-swap-v262'
Expand-Once (Join-Path $Downloads $assets[0].Name) $llamaDir (Join-Path $llamaDir '.expanded')
Expand-Once (Join-Path $Downloads $assets[1].Name) $llamaDir (Join-Path $llamaDir '.cudart-expanded')
Expand-Once (Join-Path $Downloads $assets[2].Name) $swapDir (Join-Path $swapDir '.expanded')

$llamaServer = Get-ChildItem -LiteralPath $llamaDir -Filter 'llama-server.exe' -File -Recurse | Select-Object -First 1
$swapExe = Get-ChildItem -LiteralPath $swapDir -Filter 'llama-swap.exe' -File -Recurse | Select-Object -First 1
if (-not $llamaServer -or -not $swapExe) { throw 'Expected llama-server.exe or llama-swap.exe was not found in the extracted release archives.' }
& $llamaServer.FullName --version
if ($LASTEXITCODE -ne 0) { throw 'llama-server --version failed; check the CUDA runtime archive.' }
& $swapExe.FullName --version
if ($LASTEXITCODE -ne 0) { throw 'llama-swap --version failed.' }

$modelFiles = @(
    @{ Repo = 'ggml-org/Kev-0.8B-GGUF'; Revision = 'e551e319d483ff57e1ff208924b349d397cffc1c'; File = 'Kev-0.8B-Q8_0.gguf'; Hash = '27278f34eb3273bceea4c053dc50dd61a5161da21a718c4aacdf8fd5830771d0' },
    @{ Repo = 'ggml-org/Kev-4B-GGUF'; Revision = 'd924f2e2c3872da8b8aaf3eb4453b4126deceb79'; File = 'Kev-4B-Q8_0.gguf'; Hash = '7c2ebed90560522c2801389db482ac1dc4c36d828f201f6074c1d60e433948da' }
)
foreach ($model in $modelFiles) {
    $path = Join-Path $Models $model.File
    $url = "https://huggingface.co/$($model.Repo)/resolve/$($model.Revision)/$($model.File)?download=true"
    Get-VerifiedFile $url $path $model.Hash
}

$config = @"
healthCheckTimeout: 120
logLevel: info
logToStdout: proxy
proxy: http://127.0.0.1:$Port
models:
  kev-08b:
    aliases: [effort-kev-08b]
    cmd: >-
      `"$($llamaServer.FullName)`" --host 127.0.0.1 --port `${PORT}
      --model `"$(Join-Path $Models 'Kev-0.8B-Q8_0.gguf')`"
      --ctx-size 8192 --n-gpu-layers 99
    cmdStop: >-
      powershell.exe -NoProfile -NonInteractive -Command `"Stop-Process -Id `${PID} -Force`"
    ttl: 0
  kev-4b:
    aliases: [effort-kev-4b]
    cmd: >-
      `"$($llamaServer.FullName)`" --host 127.0.0.1 --port `${PORT}
      --model `"$(Join-Path $Models 'Kev-4B-Q8_0.gguf')`"
      --ctx-size 8192 --n-gpu-layers 99
    cmdStop: >-
      powershell.exe -NoProfile -NonInteractive -Command `"Stop-Process -Id `${PID} -Force`"
    ttl: 0
routing:
  router:
    use: group
    settings:
      groups:
        kev-models:
          swap: true
          exclusive: true
          members: [kev-08b, kev-4b]
"@
Set-Content -LiteralPath (Join-Path $Root 'config.yaml') -Value $config -Encoding utf8

Write-Host "Setup ready: $Root"
Write-Host 'Client model values must be effort-kev-08b or effort-kev-4b to select the corresponding llama-swap backend.'
