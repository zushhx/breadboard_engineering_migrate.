param([string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($PSVersionTable.PSVersion.ToString() -ne '7.6.6') { throw 'Use project PowerShell 7.6.6.' }
$projectRoot = Split-Path -Parent $PSScriptRoot
$compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
$framework = Split-Path -Parent $compiler
$toolchain = Get-Content -Raw (Join-Path $projectRoot 'desktop-controller/toolchain.json') | ConvertFrom-Json
if ((Get-FileHash $compiler -Algorithm SHA256).Hash.ToLowerInvariant() -ne $toolchain.compiler_sha256) { throw 'Compiler mismatch.' }
$references = @($toolchain.reference_sha256.PSObject.Properties | ForEach-Object {
    $path = Join-Path $framework $_.Name
    if ((Get-FileHash $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $_.Value) { throw "Reference mismatch: $($_.Name)" }
    '/reference:' + $path
})
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $projectRoot ('local-data/validation/release-v010/download-bridge-' + (Get-Date -Format 'yyyyMMdd-HHmmss')) }
if (Test-Path -LiteralPath $OutputDirectory) { throw 'Choose a new test output directory.' }
$null = New-Item -ItemType Directory -Path $OutputDirectory
$binary = Join-Path $OutputDirectory 'DownloadBridgeTests.exe'
$sources = @('desktop-controller/src/Core.cs','desktop-controller/src/DeviceBridge.cs','desktop-controller/tests/DownloadBridgeTests.cs') | ForEach-Object { Join-Path $projectRoot $_ }
& $compiler /nologo /noconfig /nostdlib+ /utf8output /codepage:65001 /optimize+ /warn:4 /target:exe ('/out:' + $binary) @references @sources 2>&1 | Tee-Object -FilePath (Join-Path $OutputDirectory 'compile.log')
if ($LASTEXITCODE -ne 0) { throw 'Download bridge test compilation failed.' }
Copy-Item -LiteralPath (Join-Path $projectRoot 'desktop-controller/src/app.config') -Destination ($binary + '.config')
& $binary 2>&1 | Tee-Object -FilePath (Join-Path $OutputDirectory 'test.log')
if ($LASTEXITCODE -ne 0) { throw 'Download bridge host regression failed.' }
Write-Output "DOWNLOAD_BRIDGE_EVIDENCE=$OutputDirectory"
