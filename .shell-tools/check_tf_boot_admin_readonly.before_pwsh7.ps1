# Run only the read-only boot-sector check for the reader identified in this session.
# This script never opens the card for writing, locks it, repairs it, or formats it.
$ErrorActionPreference = 'Stop'
$tfAdminLog = Join-Path (Split-Path -Parent $PSScriptRoot) 'logs/tf_pc_admin_readonly_20261012.log'
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
