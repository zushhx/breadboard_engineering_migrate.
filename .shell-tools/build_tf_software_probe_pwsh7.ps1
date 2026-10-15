$ErrorActionPreference = 'Stop'
Write-Output ('Windows build host PowerShell: ' + $PSVersionTable.PSVersion.ToString())
if ($PSVersionTable.PSVersion -ne [version]'7.6.6') {
    throw 'Expected the workspace PowerShell 7.6.6 host.'
}
$tfWorkspace = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath (Join-Path $tfWorkspace '面包板样机_固件')
$env:CODEBUDDY_SAFE_DELETE_ENABLED = '0'
$tfStamp = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTimeOffset]::UtcNow, 'China Standard Time').ToString('yyyyMMdd_HHmmss')
$tfBuildLog = Join-Path (Get-Location) ('logs\tf_software_probe_build_' + $tfStamp + '.log')
& 'C:\Users\acer\.platformio\penv\Scripts\python.exe' -m platformio run --project-conf platformio.tf_software_probe.ini 2>&1 | Tee-Object -FilePath $tfBuildLog
$tfBuildExit = $LASTEXITCODE
Write-Output ('Saved: ' + $tfBuildLog)
exit $tfBuildExit
