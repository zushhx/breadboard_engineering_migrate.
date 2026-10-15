param([string[]]$Environments)
$ErrorActionPreference = 'Stop'
Write-Output ('Windows build host PowerShell: ' + $PSVersionTable.PSVersion.ToString())
if ($PSVersionTable.PSVersion -ne [version]'7.6.6') { throw 'Expected PowerShell 7.6.6' }
$tfWorkspace = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath (Join-Path $tfWorkspace '面包板样机_固件')
$env:CODEBUDDY_SAFE_DELETE_ENABLED = '0'
$env:PYTHONIOENCODING = 'utf-8'
$tfStamp = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTimeOffset]::UtcNow, 'China Standard Time').ToString('yyyyMMdd_HHmmss')
$tfBuildLog = Join-Path (Get-Location) ('logs\tf_protocol_probe_build_' + $tfStamp + '.log')
$tfBuildArguments = @('-m', 'platformio', 'run', '--project-conf', 'platformio.tf_protocol_probe.ini')
if ($Environments) { $Environments = @($Environments | ForEach-Object { $_ -split ',' }) }
foreach ($tfEnvironment in $Environments) { $tfBuildArguments += @('--environment', $tfEnvironment) }
& 'C:\Users\acer\.platformio\penv\Scripts\python.exe' @tfBuildArguments 2>&1 | Tee-Object -FilePath $tfBuildLog
$tfBuildExit = $LASTEXITCODE
Write-Output ('Saved: ' + $tfBuildLog)
exit $tfBuildExit
