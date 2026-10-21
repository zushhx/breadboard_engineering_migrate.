param([switch]$PrepareOnly, [string]$WifiConfig)
$ErrorActionPreference = 'Stop'
Write-Output ('Windows build host PowerShell: ' + $PSVersionTable.PSVersion.ToString())
if ($PSVersionTable.PSVersion -ne [version]'7.6.6') { throw 'Expected PowerShell 7.6.6' }
$webWorkspace = Split-Path -Parent $PSScriptRoot
$webPython = Join-Path $env:USERPROFILE '.platformio\penv\Scripts\python.exe'
$webArguments = @((Join-Path $webWorkspace 'tools\build_sensor_web_recorder.py'))
if ($PrepareOnly) { $webArguments += '--prepare-only' }
if ($WifiConfig) { $webArguments += @('--wifi-config', $WifiConfig) }
$env:PYTHONIOENCODING = 'utf-8'
& $webPython @webArguments
exit $LASTEXITCODE
