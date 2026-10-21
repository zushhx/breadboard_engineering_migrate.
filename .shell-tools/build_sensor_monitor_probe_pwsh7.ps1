param([switch]$PrepareOnly)
$ErrorActionPreference = 'Stop'
Write-Output ('Windows build host PowerShell: ' + $PSVersionTable.PSVersion.ToString())
if ($PSVersionTable.PSVersion -ne [version]'7.6.6') { throw 'Expected PowerShell 7.6.6' }
$tfWorkspace = Split-Path -Parent $PSScriptRoot
$tfPython = Join-Path $env:USERPROFILE '.platformio\penv\Scripts\python.exe'
$tfArguments = @((Join-Path $tfWorkspace 'tools\build_sensor_monitor_probe.py'))
if ($PrepareOnly) { $tfArguments += '--prepare-only' }
$env:PYTHONIOENCODING = 'utf-8'
& $tfPython @tfArguments
exit $LASTEXITCODE
