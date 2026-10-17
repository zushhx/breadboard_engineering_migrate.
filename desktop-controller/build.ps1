param([string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($PSVersionTable.PSVersion.ToString() -ne '7.6.6') { throw 'Use the project PowerShell 7.6.6 entry point.' }
$projectRoot = Split-Path -Parent $PSScriptRoot
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64/v4.0.30319/csc.exe'
$framework = Split-Path -Parent $compiler
$sourceDirectory = Join-Path $PSScriptRoot 'src'
$toolchain = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'toolchain.json') | ConvertFrom-Json
if ((Get-FileHash -LiteralPath $compiler -Algorithm SHA256).Hash.ToLowerInvariant() -ne $toolchain.compiler_sha256) {
    throw 'Compiler differs from the pinned toolchain. Review the toolchain change before building.'
}
$referencePaths = @($toolchain.reference_sha256.PSObject.Properties | ForEach-Object {
    $referencePath = Join-Path $framework $_.Name
    if ((Get-FileHash -LiteralPath $referencePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $_.Value) {
        throw "Framework reference differs from the pinned toolchain: $($_.Name)"
    }
    $referencePath
})
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $projectRoot ('local-data/desktop-controller/build-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
}
$output = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath $output) { throw "Output already exists; choose a new build directory: $output" }
$null = New-Item -ItemType Directory -Path $output

# Code-drawn waveform icon, embedded in the EXE. No external media or downloaded assets.
Add-Type -AssemblyName System.Drawing
$bitmap = [Drawing.Bitmap]::new(64, 64)
$graphics = [Drawing.Graphics]::FromImage($bitmap)
$graphics.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::AntiAlias
$graphics.Clear([Drawing.ColorTranslator]::FromHtml('#245DD6'))
$pen = [Drawing.Pen]::new([Drawing.Color]::White, 4)
$pen.LineJoin = [Drawing.Drawing2D.LineJoin]::Round
$points = [Drawing.Point[]]@([Drawing.Point]::new(10, 33), [Drawing.Point]::new(21, 33), [Drawing.Point]::new(27, 16), [Drawing.Point]::new(35, 49), [Drawing.Point]::new(43, 27), [Drawing.Point]::new(49, 33), [Drawing.Point]::new(56, 33))
$graphics.DrawLines($pen, $points)
$png = [IO.MemoryStream]::new()
$bitmap.Save($png, [Drawing.Imaging.ImageFormat]::Png)
$pngBytes = $png.ToArray()
$iconPath = Join-Path $output 'AppIcon.ico'
$iconWriter = [IO.BinaryWriter]::new([IO.File]::Create($iconPath))
try {
    $iconWriter.Write([uint16]0); $iconWriter.Write([uint16]1); $iconWriter.Write([uint16]1)
    $iconWriter.Write([byte]64); $iconWriter.Write([byte]64); $iconWriter.Write([byte]0); $iconWriter.Write([byte]0)
    $iconWriter.Write([uint16]1); $iconWriter.Write([uint16]32)
    $iconWriter.Write([uint32]$pngBytes.Length); $iconWriter.Write([uint32]22); $iconWriter.Write($pngBytes)
} finally {
    $iconWriter.Dispose(); $png.Dispose(); $pen.Dispose(); $graphics.Dispose(); $bitmap.Dispose()
}
$common = @('/nologo', '/noconfig', '/nostdlib+', '/utf8output', '/codepage:65001', '/optimize+', '/warn:4', '/platform:anycpu')
$common += @($referencePaths | ForEach-Object { '/reference:' + $_ })
$exe = Join-Path $output '主控控制台.exe'
$compile = $common + @('/target:winexe', ('/out:' + $exe), ('/win32manifest:' + (Join-Path $sourceDirectory 'app.manifest')),
    ('/win32icon:' + $iconPath), ('/resource:' + (Join-Path $sourceDirectory 'MainWindow.xaml') + ',MainWindow.xaml'),
    ('/resource:' + $iconPath + ',AppIcon.ico'), (Join-Path $sourceDirectory 'Core.cs'), (Join-Path $sourceDirectory 'Program.cs'))
& $compiler @compile 2>&1 | Tee-Object -FilePath (Join-Path $output 'compile.log')
if ($LASTEXITCODE -ne 0) { throw 'Desktop application compilation failed.' }
Copy-Item -LiteralPath (Join-Path $sourceDirectory 'app.config') -Destination ($exe + '.config')
$testExe = Join-Path $output 'CoreTests.exe'
$testCompile = $common + @('/target:exe', ('/out:' + $testExe), (Join-Path $sourceDirectory 'Core.cs'), (Join-Path $PSScriptRoot 'tests/CoreTests.cs'))
& $compiler @testCompile 2>&1 | Tee-Object -FilePath (Join-Path $output 'test-compile.log')
if ($LASTEXITCODE -ne 0) { throw 'Desktop test compilation failed.' }
Copy-Item -LiteralPath (Join-Path $sourceDirectory 'app.config') -Destination ($testExe + '.config')
$sourceFiles = @((Get-ChildItem -LiteralPath $sourceDirectory -File), (Get-Item -LiteralPath $PSCommandPath), (Get-Item -LiteralPath (Join-Path $PSScriptRoot 'toolchain.json')), (Get-Item -LiteralPath (Join-Path $PSScriptRoot 'tests/CoreTests.cs'))) | ForEach-Object { $_ }
$sourceHashes = @($sourceFiles | ForEach-Object { [ordered]@{path = [IO.Path]::GetRelativePath($projectRoot, $_.FullName); sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()} })
$manifest = [ordered]@{
    built_at = [DateTimeOffset]::Now.ToString('o')
    source_commit = (& git -C $projectRoot rev-parse HEAD)
    source_worktree_dirty = [bool](& git -C $projectRoot status --porcelain)
    powershell = $PSVersionTable.PSVersion.ToString()
    compiler = $compiler
    compiler_version = (Get-Item -LiteralPath $compiler).VersionInfo.FileVersion
    compiler_sha256 = (Get-FileHash -LiteralPath $compiler -Algorithm SHA256).Hash.ToLowerInvariant()
    references = @($referencePaths | ForEach-Object { [ordered]@{ path = $_; sha256 = (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant()} })
    sources = $sourceHashes
    artifacts = @($exe, ($exe + '.config'), $testExe | ForEach-Object { [ordered]@{ name = [IO.Path]::GetFileName($_); bytes = (Get-Item -LiteralPath $_).Length; sha256 = (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant()} })
    build_passed = $true
    hardware_tested = $false
    action = 'build_only_no_device_or_install'
}
$manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $output 'build-manifest.json') -Encoding utf8
Write-Output "BUILD_OUTPUT=$output"
Write-Output 'Build only: no browser, settings, serial, flash, device requests or desktop installation.'
