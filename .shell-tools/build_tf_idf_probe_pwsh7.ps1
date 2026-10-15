$ErrorActionPreference = 'Stop'
Write-Output ('Windows build host PowerShell: ' + $PSVersionTable.PSVersion.ToString())
if ($PSVersionTable.PSVersion -ne [version]'7.6.6') { throw 'Expected PowerShell 7.6.6' }
$tfWorkspace = Split-Path -Parent $PSScriptRoot
$tfProject = Join-Path $tfWorkspace '面包板样机_固件'
$tfTemplate = Join-Path $tfProject 'diagnostics\tf_idf_probe'
# ESP-IDF/CMake is built from an ASCII path; the editable source stays in this workspace.
$tfBuildProject = 'C:\Users\acer\.platformio\tf_idf_probe_542'
if (-not (Test-Path -LiteralPath $tfBuildProject)) { New-Item -ItemType Directory -Path $tfBuildProject | Out-Null }
Copy-Item -LiteralPath (Join-Path $tfTemplate 'src') -Destination $tfBuildProject -Recurse -Force
foreach ($tfName in @('platformio.ini', 'CMakeLists.txt', 'sdkconfig.defaults', 'partitions.csv')) {
    Copy-Item -LiteralPath (Join-Path $tfTemplate $tfName) -Destination $tfBuildProject -Force
}
Copy-Item -LiteralPath (Join-Path $tfProject 'diagnostics\tf_protocol_probe\main.cpp') -Destination (Join-Path $tfBuildProject 'src\main.cpp') -Force
$env:CODEBUDDY_SAFE_DELETE_ENABLED = '0'
$env:PYTHONIOENCODING = 'utf-8'
$tfStamp = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTimeOffset]::UtcNow, 'China Standard Time').ToString('yyyyMMdd_HHmmss')
$tfBuildLog = Join-Path $tfProject ('logs\tf_idf542_probe_build_' + $tfStamp + '.log')
Set-Location -LiteralPath $tfBuildProject
& 'C:\Users\acer\.platformio\penv\Scripts\python.exe' -m platformio run 2>&1 | Tee-Object -FilePath $tfBuildLog
$tfBuildExit = $LASTEXITCODE
if ($tfBuildExit -eq 0) {
    $tfOutput = Join-Path $tfProject '.pio\tf_idf_probe\tf_idf542_4bit_20m'
    New-Item -ItemType Directory -Path $tfOutput -Force | Out-Null
    foreach ($tfName in @('firmware.bin', 'bootloader.bin', 'partitions.bin')) {
        Copy-Item -LiteralPath (Join-Path $tfBuildProject ('.pio\build\tf_idf542_4bit_20m\' + $tfName)) -Destination $tfOutput -Force
    }
}
Write-Output ('Saved: ' + $tfBuildLog)
exit $tfBuildExit
