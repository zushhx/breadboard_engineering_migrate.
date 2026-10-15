param([ValidatePattern('^[A-Z]$')][string]$DriveLetter = 'F')

# Only read boot sectors. No filesystem repair, formatting or card writes.
$ErrorActionPreference = 'Stop'
$tfOutputDir = Join-Path (Split-Path -Parent $PSScriptRoot) 'logs'
$tfReadTag = [DateTime]::Now.ToString('yyyyMMdd_HHmmss_fff')
$tfPartition = Get-Partition -DriveLetter $DriveLetter -ErrorAction SilentlyContinue
if (@($tfPartition).Count -eq 1) {
    $tfDisk = Get-Disk -Number $tfPartition.DiskNumber
} else {
    $tfCandidates = @(Get-Disk | Where-Object { $_.BusType.ToString() -eq 'USB' -and $_.Size -gt 0 })
    if ($tfCandidates.Count -ne 1) {
        $tfCandidates | Select-Object Number,FriendlyName,BusType,Size | ConvertTo-Json -Compress
        throw 'No unique USB card disk. No raw sectors read.'
    }
    $tfDisk = $tfCandidates[0]
    $tfPartition = @(Get-Partition -DiskNumber $tfDisk.Number -ErrorAction SilentlyContinue |
        Where-Object { $_.DriveLetter } | Select-Object -First 1)
    if ($tfPartition.Count -eq 1) { $tfPartition = $tfPartition[0] } else { $tfPartition = $null }
}
if ($tfDisk.BusType.ToString() -ne 'USB' -or $tfDisk.LogicalSectorSize -ne 512) {
    throw 'Selected device is not a USB disk with 512-byte sectors. No raw sectors read.'
}
$tfVolume = if ($tfPartition) {
    Get-Volume -Partition $tfPartition -ErrorAction SilentlyContinue
} else { $null }
$tfDeviceInfo = [pscustomobject]@{
    DiskNumber=$tfDisk.Number
    BusType=$tfDisk.BusType.ToString()
    DiskSize=$tfDisk.Size
    LogicalSectorSize=$tfDisk.LogicalSectorSize
    PartitionStyle=$tfDisk.PartitionStyle.ToString()
    DriveLetter=if ($tfPartition) { $tfPartition.DriveLetter } else { $null }
    PartitionOffset=if ($tfPartition) { $tfPartition.Offset } else { $null }
    FileSystem=if ($tfVolume) { $tfVolume.FileSystem } else { $null }
}
$tfDeviceInfo | ConvertTo-Json -Compress

function Read-TfSector([string]$Device, [long]$Offset) {
    $tfStream = [System.IO.FileStream]::new($Device,[System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,[System.IO.FileShare]::ReadWrite,512)
    try {
        $null = $tfStream.Seek($Offset,[System.IO.SeekOrigin]::Begin)
        $tfBytes = New-Object byte[] 512
        $tfReadCount = 0
        while ($tfReadCount -lt 512) {
            $tfChunkCount = $tfStream.Read($tfBytes,$tfReadCount,512-$tfReadCount)
            if ($tfChunkCount -eq 0) { throw 'Short sector read.' }
            $tfReadCount += $tfChunkCount
        }
        return ,$tfBytes
    } finally { $tfStream.Dispose() }
}

function Summarize-TfSector([string]$Source,[byte[]]$Data) {
    $tfHash = [System.Security.Cryptography.SHA256]::Create()
    try { $tfDigest = [BitConverter]::ToString($tfHash.ComputeHash($Data)).Replace('-','') }
    finally { $tfHash.Dispose() }
    [uint64]$tfFnv = 2166136261
    $tfNonzero = 0
    foreach ($tfByte in $Data) {
        if ($tfByte -ne 0) { ++$tfNonzero }
        $tfFnv = ((($tfFnv -bxor [uint64]$tfByte) * [uint64]16777619) -band [uint64]4294967295)
    }
    return [pscustomobject]@{
        Source=$Source
        Jump=[BitConverter]::ToString($Data,0,3)
        Signature=[BitConverter]::ToString($Data,510,2)
        BytesPerSector=[BitConverter]::ToUInt16($Data,11)
        SectorsPerCluster=$Data[13]
        Reserved=[BitConverter]::ToUInt16($Data,14)
        FATCount=$Data[16]
        RootEntryCount=[BitConverter]::ToUInt16($Data,17)
        TotalSectors16=[BitConverter]::ToUInt16($Data,19)
        FATSize16=[BitConverter]::ToUInt16($Data,22)
        HiddenSectors=[BitConverter]::ToUInt32($Data,28)
        TotalSectors=[BitConverter]::ToUInt32($Data,32)
        FATSize=[BitConverter]::ToUInt32($Data,36)
        RootCluster=[BitConverter]::ToUInt32($Data,44)
        TypeLabel=[Text.Encoding]::ASCII.GetString($Data,82,8)
        NonzeroBytes=$tfNonzero
        FNV1a=('{0:X8}' -f $tfFnv)
        SHA256=$tfDigest
    }
}

$tfRawDevice = '\\.\PhysicalDrive' + $tfDisk.Number
$tfReports = @()
try {
    $tfPhysical = Read-TfSector $tfRawDevice 0
    $tfReports += Summarize-TfSector 'physical_LBA0' $tfPhysical
    [System.IO.File]::WriteAllBytes((Join-Path $tfOutputDir ('tf_pc_physical_lba0_' + $tfReadTag + '.bin')),$tfPhysical)
} catch {
    $tfReports += [pscustomobject]@{Source='physical_LBA0';ReadError=$_.Exception.Message}
}
if ($tfPartition -and $tfPartition.Offset -gt 0) {
    try {
        $tfPartitionBoot = Read-TfSector $tfRawDevice $tfPartition.Offset
        $tfReports += Summarize-TfSector 'physical_partition_start' $tfPartitionBoot
        [System.IO.File]::WriteAllBytes((Join-Path $tfOutputDir ('tf_pc_partition_boot_' + $tfReadTag + '.bin')),$tfPartitionBoot)
    } catch {
        $tfReports += [pscustomobject]@{Source='physical_partition_start';ReadError=$_.Exception.Message}
    }
}
if ($tfPartition -and $tfPartition.DriveLetter) {
    try {
        $tfLogical = Read-TfSector ('\\.\' + $tfPartition.DriveLetter + ':') 0
        $tfReports += Summarize-TfSector 'volume_sector0' $tfLogical
        [System.IO.File]::WriteAllBytes((Join-Path $tfOutputDir ('tf_pc_volume_boot_' + $tfReadTag + '.bin')),$tfLogical)
    } catch {
        $tfReports += [pscustomobject]@{Source='volume_sector0';ReadError=$_.Exception.Message}
    }
}
$tfReports | ConvertTo-Json -Depth 4
[pscustomobject]@{Device=$tfDeviceInfo;Sectors=$tfReports} | ConvertTo-Json -Depth 5 |
    Set-Content -LiteralPath (Join-Path $tfOutputDir ('tf_pc_boot_compare_' + $tfReadTag + '.json')) -Encoding UTF8
