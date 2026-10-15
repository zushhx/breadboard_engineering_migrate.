param([switch]$VerifyHost)

# Run only the read-only boot-sector check for the reader identified in this session.
# This script never opens the card for writing, locks it, repairs it, or formats it.
$ErrorActionPreference = 'Stop'
$tfProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$tfPwsh = [IO.Path]::GetFullPath((Join-Path $tfProjectRoot '.shell-tools/PowerShell-7.6.6/pwsh.exe'))
if (-not (Test-Path -LiteralPath $tfPwsh -PathType Leaf)) {
    throw 'The verified project PowerShell 7.6.6 runtime is missing.'
}
$tfCurrentHost = [Diagnostics.Process]::GetCurrentProcess().MainModule.FileName
if ($tfCurrentHost -ine $tfPwsh) {
    # An elevated caller passes its current Windows privileges to the new host.
    $tfArgs = @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath)
    if ($VerifyHost) { $tfArgs += '-VerifyHost' }
    & $tfPwsh @tfArgs
    exit $LASTEXITCODE
}
if ($PSVersionTable.PSVersion.ToString() -ne '7.6.6') {
    throw 'The project runtime is not the verified PowerShell 7.6.6 version.'
}
if ($VerifyHost) {
    $tfPrincipal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    [pscustomobject]@{
        Version=$PSVersionTable.PSVersion.ToString()
        Executable=$tfCurrentHost
        IsAdministrator=$tfPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
        CardRead=$false
    } | ConvertTo-Json
    exit 0
}
$tfAdminLog = Join-Path (Split-Path -Parent $PSScriptRoot) (
    'logs/tf_pc_admin_readonly_' + [DateTime]::Now.ToString('yyyyMMdd_HHmmss_fff') + '.log')
try {
    $tfPrincipal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $tfPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Windows administrator access is required for the physical-sector read.'
    }
    $tfPartition = Get-Partition -DriveLetter F
    if (@($tfPartition).Count -ne 1) { throw 'F is not one uniquely identified partition.' }
    $tfDisk = Get-Disk -Number $tfPartition.DiskNumber
    if ($tfDisk.Number -ne 2 -or $tfDisk.BusType.ToString() -ne 'USB' -or
        $tfDisk.Size -ne 7948206080 -or $tfDisk.SerialNumber.Trim() -ne '121220160204') {
        throw 'The reader identity has changed. No sectors read.'
    }
    & (Join-Path $PSScriptRoot 'check_tf_boot_readonly.ps1') -DriveLetter F |
        Set-Content -LiteralPath $tfAdminLog -Encoding UTF8
    exit 0
} catch {
    $_ | Out-String | Set-Content -LiteralPath $tfAdminLog -Encoding UTF8
    exit 1
}
