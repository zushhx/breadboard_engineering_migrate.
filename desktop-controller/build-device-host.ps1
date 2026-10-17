param([Parameter(Mandatory)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
if($PSVersionTable.PSVersion.ToString() -ne '7.6.6'){throw 'Use project PowerShell 7.6.6'}
$compiler=Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
$framework=Split-Path -Parent $compiler
$pin=Get-Content -Raw (Join-Path $PSScriptRoot 'toolchain.json') | ConvertFrom-Json
if((Get-FileHash -LiteralPath $compiler).Hash.ToLowerInvariant() -ne $pin.compiler_sha256){throw 'Compiler hash mismatch'}
$references=@($pin.reference_sha256.PSObject.Properties | ForEach-Object {
  $file=Join-Path $framework $_.Name
  if((Get-FileHash -LiteralPath $file).Hash.ToLowerInvariant() -ne $_.Value){throw 'Reference hash mismatch'}
  '/reference:'+$file
})
$null=New-Item -ItemType Directory -Path $OutputDirectory -Force
$target=Join-Path $OutputDirectory 'DeviceHost.exe'
if(Test-Path -LiteralPath $target){throw 'Diagnostic host already exists'}
$arguments=@('/nologo','/noconfig','/nostdlib+','/utf8output','/codepage:65001','/optimize+','/warn:4','/target:exe',('/out:'+$target))+$references+@((Join-Path $PSScriptRoot 'src/Core.cs'),(Join-Path $PSScriptRoot 'src/DeviceBridge.cs'),(Join-Path $PSScriptRoot 'tools/DeviceHost.cs'))
& $compiler @arguments
if($LASTEXITCODE -ne 0){throw 'Diagnostic host compile failed'}
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'src/app.config') -Destination ($target+'.config')
Write-Output 'Compile only. No USB/serial or device actions.'
