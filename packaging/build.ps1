$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$temporaryArchivePath = $null
$temporaryPublishDirectory = $null
$temporaryBackupDirectory = $null
$stagingDirectory = $null

try {
    Set-Location -LiteralPath $projectRoot

    $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath (Join-Path $projectRoot ".venv")) {
        if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
            throw "The .venv folder exists but its Python interpreter is missing: $venvPython"
        }
        $python = $venvPython
    }
    else {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if (-not $pythonCommand) {
            throw "Python was not found. Create .venv or install Python and add it to PATH."
        }
        $python = $pythonCommand.Source
    }

    $dependencyCheck = "import importlib.util, sys; required = ['PyInstaller', 'PIL', 'requests', 'sv_ttk', 'tkinterdnd2']; missing = [name for name in required if importlib.util.find_spec(name) is None]; print(', '.join(missing)); sys.exit(bool(missing))"
    $missingDependencies = @(& $python -c $dependencyCheck)
    if ($LASTEXITCODE -ne 0) {
        $missingList = ($missingDependencies -join ", ").Trim()
        if (-not $missingList) {
            $missingList = "PyInstaller, Pillow, requests, sv-ttk, tkinterdnd2"
        }
        throw "Missing build dependencies ($missingList). From the project root, install them with: `"$python`" -m pip install -r packaging\requirements-build.txt"
    }

    $versionOutput = & $python -c "from moviq import __version__; print(__version__)"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not read the project version from moviq.__version__."
    }
    $version = ($versionOutput | Out-String).Trim()
    if ($version -notmatch "^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$") {
        throw "The project version must be numeric SemVer (MAJOR.MINOR.PATCH), without a prefix or suffix: '$version'."
    }

    $installerCompiler = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($installerCompiler) {
        $installerCompilerPath = $installerCompiler.Source
    }
    else {
        $installerCompilerPath = $null
        foreach ($programFiles in @(
            ${env:ProgramFiles(x86)}, $env:ProgramFiles
        )) {
            if (-not $programFiles) {
                continue
            }
            foreach ($innoVersion in @("Inno Setup 7", "Inno Setup 6")) {
                $candidate = Join-Path $programFiles (
                    "$innoVersion\ISCC.exe"
                )
                if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                    $installerCompilerPath = $candidate
                    break
                }
            }
            if ($installerCompilerPath) {
                break
            }
        }
    }
    if (-not $installerCompilerPath) {
        throw "Inno Setup Compiler (ISCC.exe) was not found. Add it to PATH or install it in Program Files."
    }

    $releaseDistPath = Join-Path $projectRoot "release\dist"
    $releaseBuildPath = Join-Path $projectRoot "release\build"
    $versionDirectoryName = "v$version"
    $versionOutputDirectory = Join-Path $releaseDistPath $versionDirectoryName
    $portableName = "Moviq-Portable-$version"
    $stagingDirectory = Join-Path $releaseBuildPath (
        "portable-$([Guid]::NewGuid().ToString('N'))"
    )
    $portableDirectory = Join-Path $stagingDirectory $portableName
    $executablePath = Join-Path $portableDirectory "$portableName.exe"
    $archivePath = Join-Path $stagingDirectory "$portableName.zip"
    $installerScript = Join-Path $PSScriptRoot "moviq.iss"
    $installerOutputDirectory = Join-Path $stagingDirectory "installer"
    $installerName = "Moviq-Setup-$version.exe"
    $installerPath = Join-Path $versionOutputDirectory $installerName
    if (-not (Test-Path -LiteralPath $installerScript -PathType Leaf)) {
        throw "The Inno Setup script is missing: $installerScript"
    }

    New-Item -ItemType Directory -Path $releaseBuildPath, $releaseDistPath -Force | Out-Null

    $entryScript = Join-Path $PSScriptRoot "run_moviq.py"
    if (-not (Test-Path -LiteralPath $entryScript -PathType Leaf)) {
        throw "The PyInstaller entry script is missing: $entryScript"
    }

    $specPath = Join-Path $PSScriptRoot "moviq.spec"
    if (-not (Test-Path -LiteralPath $specPath -PathType Leaf)) {
        throw "The PyInstaller spec is missing: $specPath"
    }

    & $python -m PyInstaller --noconfirm --clean `
        --distpath $stagingDirectory `
        --workpath $releaseBuildPath `
        $specPath
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }

    if (-not (Test-Path -LiteralPath $executablePath -PathType Leaf)) {
        throw "PyInstaller completed but the portable application was not created."
    }

    Add-Type -AssemblyName System.IO.Compression
    $temporaryArchivePath = Join-Path $stagingDirectory (
        ".$portableName-$([Guid]::NewGuid().ToString('N')).tmp.zip"
    )
    $archiveFiles = @(
        Get-ChildItem -LiteralPath $portableDirectory -File -Recurse |
            Sort-Object -Property FullName
    )
    $archiveCreated = $false
    for ($attempt = 1; $attempt -le 5 -and -not $archiveCreated; $attempt++) {
        $archiveStream = $null
        $zipArchive = $null
        try {
            $archiveStream = [System.IO.File]::Open(
                $temporaryArchivePath,
                [System.IO.FileMode]::CreateNew,
                [System.IO.FileAccess]::ReadWrite,
                [System.IO.FileShare]::None
            )
            $zipArchive = [System.IO.Compression.ZipArchive]::new(
                $archiveStream,
                [System.IO.Compression.ZipArchiveMode]::Create,
                $true
            )
            foreach ($sourceFile in $archiveFiles) {
                $entryName = $sourceFile.FullName.Substring(
                    $portableDirectory.Length + 1
                ).Replace("\", "/")
                $entryName = "$portableName/$entryName"
                $entry = $zipArchive.CreateEntry(
                    $entryName,
                    [System.IO.Compression.CompressionLevel]::Optimal
                )
                $sourceStream = $null
                $entryStream = $null
                try {
                    $sourceStream = [System.IO.File]::Open(
                        $sourceFile.FullName,
                        [System.IO.FileMode]::Open,
                        [System.IO.FileAccess]::Read,
                        [System.IO.FileShare]::ReadWrite
                    )
                    $entryStream = $entry.Open()
                    $sourceStream.CopyTo($entryStream)
                }
                finally {
                    if ($entryStream) {
                        $entryStream.Dispose()
                    }
                    if ($sourceStream) {
                        $sourceStream.Dispose()
                    }
                }
            }
            $zipArchive.Dispose()
            $zipArchive = $null
            $archiveStream.Dispose()
            $archiveStream = $null
            $archiveCreated = $true
        }
        catch [System.IO.IOException] {
            if ($zipArchive) {
                $zipArchive.Dispose()
                $zipArchive = $null
            }
            if ($archiveStream) {
                $archiveStream.Dispose()
                $archiveStream = $null
            }
            if (Test-Path -LiteralPath $temporaryArchivePath) {
                Remove-Item -LiteralPath $temporaryArchivePath -Force
            }
            if ($attempt -eq 5) {
                throw "Could not read a file while creating the portable ZIP after 5 attempts. Close programs using the build folder and retry. Last error: $($_.Exception.Message)"
            }
            Write-Warning "A build file is temporarily locked; retrying ZIP creation ($attempt of 5)."
            Start-Sleep -Seconds 2
        }
        finally {
            if ($zipArchive) {
                $zipArchive.Dispose()
            }
            if ($archiveStream) {
                $archiveStream.Dispose()
            }
        }
    }

    [System.IO.File]::Move($temporaryArchivePath, $archivePath)
    $temporaryArchivePath = $null
    if (-not (Test-Path -LiteralPath $archivePath -PathType Leaf)) {
        throw "The portable ZIP archive was not created."
    }

    New-Item -ItemType Directory -Path $installerOutputDirectory -Force |
        Out-Null
    & $installerCompilerPath `
        "/DAppVersion=$version" `
        "/DSourceDir=$portableDirectory" `
        "/DInstallerOutputDir=$installerOutputDirectory" `
        $installerScript
    if ($LASTEXITCODE -ne 0) {
        throw "Inno Setup Compiler failed with exit code $LASTEXITCODE."
    }
    $stagedInstallerPath = Join-Path $installerOutputDirectory $installerName
    if (-not (Test-Path -LiteralPath $stagedInstallerPath -PathType Leaf)) {
        throw "Inno Setup completed but the installer was not created."
    }
    $temporaryPublishDirectory = Join-Path $releaseDistPath (
        ".$versionDirectoryName-$([Guid]::NewGuid().ToString('N')).tmp"
    )
    New-Item -ItemType Directory -Path $temporaryPublishDirectory |
        Out-Null
    [System.IO.File]::Copy(
        $archivePath,
        (Join-Path $temporaryPublishDirectory "$portableName.zip")
    )
    [System.IO.File]::Copy(
        $stagedInstallerPath,
        (Join-Path $temporaryPublishDirectory $installerName)
    )

    if (Test-Path -LiteralPath $versionOutputDirectory) {
        $temporaryBackupDirectory = Join-Path $releaseDistPath (
            ".$versionDirectoryName-$([Guid]::NewGuid().ToString('N')).bak"
        )
        [System.IO.Directory]::Move(
            $versionOutputDirectory, $temporaryBackupDirectory
        )
    }
    try {
        [System.IO.Directory]::Move(
            $temporaryPublishDirectory, $versionOutputDirectory
        )
        $temporaryPublishDirectory = $null
    }
    catch {
        if (
            $temporaryBackupDirectory -and
            (Test-Path -LiteralPath $temporaryBackupDirectory) -and
            -not (Test-Path -LiteralPath $versionOutputDirectory)
        ) {
            [System.IO.Directory]::Move(
                $temporaryBackupDirectory, $versionOutputDirectory
            )
            $temporaryBackupDirectory = $null
        }
        throw
    }
    if ($temporaryBackupDirectory) {
        Remove-Item -LiteralPath $temporaryBackupDirectory -Recurse -Force
        $temporaryBackupDirectory = $null
    }

    Remove-Item -LiteralPath $stagingDirectory -Recurse -Force
    Write-Host "Portable ZIP archive created: $(Join-Path $versionOutputDirectory "$portableName.zip")"
    Write-Host "Windows installer created: $installerPath"
}
catch {
    $buildError = $_.Exception.Message
    if ($stagingDirectory -and (Test-Path -LiteralPath $stagingDirectory)) {
        try {
            Remove-Item -LiteralPath $stagingDirectory -Recurse -Force
        }
        catch {
            Write-Warning "Could not remove temporary build folder $stagingDirectory`: $($_.Exception.Message)"
        }
    }
    if ($temporaryArchivePath -and (Test-Path -LiteralPath $temporaryArchivePath)) {
        try {
            Remove-Item -LiteralPath $temporaryArchivePath -Force
        }
        catch {
            Write-Warning "Could not remove temporary archive $temporaryArchivePath`: $($_.Exception.Message)"
        }
    }
    if (
        $temporaryPublishDirectory -and
        (Test-Path -LiteralPath $temporaryPublishDirectory)
    ) {
        try {
            Remove-Item -LiteralPath $temporaryPublishDirectory -Recurse -Force
        }
        catch {
            Write-Warning "Could not remove temporary release folder $temporaryPublishDirectory`: $($_.Exception.Message)"
        }
    }
    [Console]::Error.WriteLine("Build failed: $buildError")
    exit 1
}
