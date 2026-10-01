param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

& $Python verify_runtime_dependencies.py
if ($LASTEXITCODE -ne 0) {
    throw "Runtime dependency lock verification failed. Install requirements-build.txt."
}

& $Python -m unittest discover -s . -p "test_*.py"
if ($LASTEXITCODE -ne 0) {
    throw "Ground Station tests failed; no executable was produced."
}

& $Python -m PyInstaller ground_station_V7.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed."
}

$AppVersion = (& $Python -c "from app_metadata import APP_VERSION; print(APP_VERSION)" | Out-String).Trim()
$ReleaseName = (& $Python -c "from app_metadata import WINDOWS_RELEASE_NAME; print(WINDOWS_RELEASE_NAME)" | Out-String).Trim()
$SystemReleaseId = (& $Python -c "from app_metadata import SYSTEM_RELEASE_ID; print(SYSTEM_RELEASE_ID)" | Out-String).Trim()
$Artifact = Join-Path $ProjectRoot "dist\$ReleaseName.exe"
if (-not (Test-Path $Artifact -PathType Leaf)) {
    throw "Expected artifact is missing: $Artifact"
}

$VersionInfo = (Get-Item $Artifact).VersionInfo
if ($VersionInfo.FileVersion -notlike "$AppVersion*") {
    throw "Unexpected Windows file version: $($VersionInfo.FileVersion)"
}
if ($VersionInfo.OriginalFilename -ne "$ReleaseName.exe") {
    throw "Unexpected Windows original filename: $($VersionInfo.OriginalFilename)"
}

$Hash = (Get-FileHash -Algorithm SHA256 $Artifact).Hash.ToLowerInvariant()
$HashLine = "$Hash  $ReleaseName.exe"
$HashPath = Join-Path $ProjectRoot "dist\$ReleaseName.exe.sha256"
Set-Content -Path $HashPath -Value $HashLine -Encoding ascii

$PythonVersion = (& $Python --version 2>&1 | Out-String).Trim()
$PyInstallerVersion = (& $Python -m PyInstaller --version 2>&1 | Out-String).Trim()
$PlatformInfo = @(& $Python -c "import platform; print(platform.platform()); print(platform.machine())")
$BuildInfo = @(
    "system_release_id=$SystemReleaseId",
    "ground_station_app_version=$AppVersion",
    "package_name=$ReleaseName.exe",
    "built_at_utc=$([DateTime]::UtcNow.ToString('o'))",
    "python=$PythonVersion",
    "pyinstaller=$PyInstallerVersion",
    "platform=$($PlatformInfo -join ' | ')",
    "artifact_sha256=$Hash",
    "",
    "[source-sha256]"
)
$SourceFiles = @(
    "ground_station.py",
    "app_metadata.py",
    "rescue_event_manager.py",
    "priority_scheduler.py",
    "rescue_record_protocol.py",
    "sos_pattern.py",
    "ground_station_V7.spec",
    "windows_version_info_v7.txt",
    "requirements.txt",
    "requirements-build.txt",
    "verify_runtime_dependencies.py",
    "build_windows_release.ps1",
    "test_dependency_lock.py",
    "test_ground_station_rescue_flow.py",
    "test_ground_station_runtime.py",
    "test_rescue_event_manager.py",
    "test_priority_scheduler.py",
    "test_rescue_record_protocol.py",
    "test_sos_pattern.py"
)
foreach ($SourceFile in $SourceFiles) {
    $SourceHash = (Get-FileHash -Algorithm SHA256 $SourceFile).Hash.ToLowerInvariant()
    $BuildInfo += "$SourceHash  $SourceFile"
}
$BuildInfo += ""
$BuildInfo += "[pip-freeze]"
$BuildInfo += @(& $Python -m pip freeze --all)
$BuildInfoPath = Join-Path $ProjectRoot "dist\$ReleaseName.build-info.txt"
Set-Content -Path $BuildInfoPath -Value $BuildInfo -Encoding ascii

Write-Host "Windows release artifact: $Artifact"
Write-Host "SHA-256: $Hash"
Write-Host "Build environment record: $BuildInfoPath"
Write-Host "Firebase credentials remain external and are not bundled."
