param(
    [Parameter(Mandatory)][ValidateSet('backup', 'flash', 'capture', 'restore')][string]$Action,
    [string]$Environment,
    [string]$Backup,
    [double]$Seconds = 15,
    [switch]$Reset
)
$ErrorActionPreference = 'Stop'
Write-Output ('Windows interface host PowerShell: ' + $PSVersionTable.PSVersion.ToString())
if ($PSVersionTable.PSVersion -ne [version]'7.6.6') { throw 'Expected PowerShell 7.6.6' }
$tfArguments = @((Join-Path $PSScriptRoot 'tf_software_trials.py'), $Action, '--seconds', [string]$Seconds)
if ($Environment) { $tfArguments += @('--env', $Environment) }
if ($Backup) { $tfArguments += @('--backup', $Backup) }
if ($Reset) { $tfArguments += '--reset' }
& 'C:\Users\acer\.platformio\penv\Scripts\python.exe' @tfArguments
exit $LASTEXITCODE
