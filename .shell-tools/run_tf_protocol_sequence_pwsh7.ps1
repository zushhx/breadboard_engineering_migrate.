param(
    [Parameter(Mandatory)][ValidateSet('native-low-speed', 'spi', 'arduino-spi')][string]$Group,
    [Parameter(Mandatory)][string]$Backup
)
$ErrorActionPreference = 'Stop'
Write-Output ('Windows interface host PowerShell: ' + $PSVersionTable.PSVersion.ToString())
if ($PSVersionTable.PSVersion -ne [version]'7.6.6') { throw 'Expected PowerShell 7.6.6' }
$tfEnvironments = switch ($Group) {
    'native-low-speed' { 'tf_1bit_400k'; 'tf_4bit_400k' }
    'spi' { 'tf_spi_1m'; 'tf_spi_4m' }
    'arduino-spi' { 'tf_arduino_spi_1m'; 'tf_arduino_spi_400k' }
}
$env:PYTHONIOENCODING = 'utf-8'
foreach ($tfEnvironment in $tfEnvironments) {
    & 'C:\Users\acer\.platformio\penv\Scripts\python.exe' (Join-Path $PSScriptRoot 'tf_software_trials.py') flash --env $tfEnvironment --backup $Backup --seconds 15
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
