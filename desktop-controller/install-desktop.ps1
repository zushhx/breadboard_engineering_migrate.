param(
    [Parameter(Mandatory)][string]$BuildDirectory,
    [Parameter(Mandatory)][string]$ExpectedExecutableSha256,
    [switch]$Candidate,
    [string]$ApplicationName
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($PSVersionTable.PSVersion.ToString() -ne '7.6.6') { throw 'Use the project PowerShell 7.6.6 entry point.' }
$buildPath = (Resolve-Path -LiteralPath $BuildDirectory).Path
$sourceExe = Join-Path $buildPath '主控控制台.exe'
$sourceConfig = $sourceExe + '.config'
if ((Get-FileHash -LiteralPath $sourceExe -Algorithm SHA256).Hash -ne $ExpectedExecutableSha256) { throw 'Executable SHA-256 differs from the reviewed build.' }
$manifest = Get-Content -Raw -LiteralPath (Join-Path $buildPath 'build-manifest.json') | ConvertFrom-Json
$manifestExe = @($manifest.artifacts | Where-Object { $_.name -eq '主控控制台.exe' })
$manifestConfig = @($manifest.artifacts | Where-Object { $_.name -eq '主控控制台.exe.config' })
if ($manifest.source_worktree_dirty -or $manifestExe.Count -ne 1 -or $manifestExe[0].sha256 -ne $ExpectedExecutableSha256 -or $manifestConfig.Count -ne 1 -or
    (Get-FileHash -LiteralPath $sourceConfig -Algorithm SHA256).Hash -ne $manifestConfig[0].sha256) { throw 'Build manifest verification failed.' }
$desktopPath = [Environment]::GetFolderPath('Desktop')
if (-not $ApplicationName) { $ApplicationName = if ($Candidate) { '主控控制台 v2（待实机验收）' } else { '主控控制台' } }
if ($ApplicationName -notmatch '^主控控制台(?: v[0-9]+(?:（待实机验收）)?)?$') { throw 'Application name must be a controller desktop folder name, not a path.' }
$destination = Join-Path $desktopPath $applicationName
$shortcutPath = Join-Path $desktopPath ($applicationName + '.lnk')
if ((Test-Path -LiteralPath $destination) -or (Test-Path -LiteralPath $shortcutPath)) {
    throw 'A desktop application or shortcut with this name already exists. Preserve/review it before installing.'
}
$null = New-Item -ItemType Directory -Path $destination
$destinationExe = Join-Path $destination '主控控制台.exe'
Copy-Item -LiteralPath $sourceExe -Destination $destinationExe
Copy-Item -LiteralPath $sourceConfig -Destination ($destinationExe + '.config')
Copy-Item -LiteralPath (Join-Path $PSScriptRoot '使用说明.txt') -Destination (Join-Path $destination '使用说明.txt')
Copy-Item -LiteralPath (Join-Path $buildPath 'build-manifest.json') -Destination (Join-Path $destination 'build-manifest.json')
if ((Get-FileHash -LiteralPath $destinationExe -Algorithm SHA256).Hash -ne $ExpectedExecutableSha256) { throw 'Installed EXE hash verification failed; no shortcut created.' }
if ((Get-FileHash -LiteralPath ($destinationExe + '.config') -Algorithm SHA256).Hash -ne $manifestConfig[0].sha256) { throw 'Installed config verification failed; no shortcut created.' }
$shellObject = New-Object -ComObject WScript.Shell
$shortcut = $shellObject.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $destinationExe
$shortcut.WorkingDirectory = $destination
$shortcut.Description = '连接主控，打开原检测页面'
$shortcut.IconLocation = $destinationExe + ',0'
$shortcut.Save()
$readback = $shellObject.CreateShortcut($shortcutPath)
if ($readback.TargetPath -ne $destinationExe) { throw 'Shortcut target readback failed.' }
[ordered]@{
    installed_at = [DateTimeOffset]::Now.ToString('o')
    executable = $destinationExe
    executable_sha256 = $ExpectedExecutableSha256.ToLowerInvariant()
    shortcut = $shortcutPath
    shortcut_target_verified = $true
    config_sha256 = (Get-FileHash -LiteralPath ($destinationExe + '.config') -Algorithm SHA256).Hash.ToLowerInvariant()
    starts_on_boot = $false
    network_configuration_changed = $false
    hardware_accessed = $false
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $buildPath 'desktop-install.json') -Encoding utf8
Write-Output "DESKTOP_SHORTCUT=$shortcutPath"
Write-Output "INSTALLED_EXE=$destinationExe"
Write-Output 'Installed local portable application only. Not launched; no network settings or firmware changed.'
