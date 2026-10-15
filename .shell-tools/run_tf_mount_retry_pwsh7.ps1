$ErrorActionPreference = 'Stop'
Write-Output ('Windows serial host PowerShell: ' + $PSVersionTable.PSVersion.ToString())
if ($PSVersionTable.PSVersion -ne [version]'7.6.6') {
    throw 'This workspace requires PowerShell 7.6.6 for Windows serial access.'
}
$tfPythonPath = 'C:\Users\acer\.platformio\penv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $tfPythonPath)) {
    throw 'PlatformIO Windows Python is unavailable.'
}
& $tfPythonPath (Join-Path $PSScriptRoot 'tf_mount_retry_readonly.py')
exit $LASTEXITCODE
