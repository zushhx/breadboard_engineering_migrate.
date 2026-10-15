param([switch]$VerifyHost)

# Launch the existing guarded check with the project's current PowerShell 7.
$ErrorActionPreference = 'Stop'
$tfProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$tfPwsh = [IO.Path]::GetFullPath((Join-Path $tfProjectRoot '.shell-tools/PowerShell-7.6.6/pwsh.exe'))
$tfScript = Join-Path $PSScriptRoot 'check_tf_boot_admin_readonly.ps1'
if (-not (Test-Path -LiteralPath $tfPwsh -PathType Leaf)) {
    throw 'The verified project PowerShell 7.6.6 runtime is missing.'
}
$tfArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $tfScript + '"'))
if ($VerifyHost) { $tfArgs += '-VerifyHost' }
$tfProcess = Start-Process -FilePath $tfPwsh -Verb RunAs -ArgumentList $tfArgs `
    -WindowStyle Hidden -Wait -PassThru
exit $tfProcess.ExitCode

