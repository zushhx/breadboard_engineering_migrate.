param([switch]$PrepareOnly, [string]$ApprovalRecord, [string]$ColdFolder, [int]$ColdNumber, [string]$ColdConfirmation)
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion -ne [version]'7.6.6') { throw 'Expected PowerShell 7.6.6' }
$webWorkspace = Split-Path -Parent $PSScriptRoot
$webPython = Join-Path $env:USERPROFILE '.platformio\penv\Scripts\python.exe'
$webArgs = @((Join-Path $webWorkspace 'tools\sensor_web_batch.py'))
if ($PrepareOnly) { $webArgs += '--prepare-only' }
if ($ApprovalRecord) { $webArgs += @('--approval-record', $ApprovalRecord) }
if ($ColdFolder) { $webArgs += @('--cold-folder', $ColdFolder, '--cold-number', $ColdNumber, '--cold-confirmation', $ColdConfirmation) }
$env:PYTHONIOENCODING = 'utf-8'
& $webPython @webArgs
exit $LASTEXITCODE
