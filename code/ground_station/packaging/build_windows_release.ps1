param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$PackagingRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $PackagingRoot
Set-Location $ProjectRoot
$env:PYTHONPATH = Join-Path $ProjectRoot "src"

& $Python packaging/verify_runtime_dependencies.py
if ($LASTEXITCODE -ne 0) {
    throw "Runtime dependency lock verification failed. Install packaging/requirements-build.txt."
}

& $Python tests/run.py
if ($LASTEXITCODE -ne 0) {
    throw "Ground Station tests failed; no executable was produced."
}

& $Python -m PyInstaller packaging/ground_station_V7.spec --noconfirm --clean
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
    "main.py",
    "src/ground_station.py",
    "src/app_metadata.py",
    "src/rescue_event_manager.py",
    "src/priority_scheduler.py",
    "src/rescue_record_protocol.py",
    "src/sos_pattern.py",
    "packaging/ground_station_V7.spec",
    "packaging/windows_version_info_v7.txt",
    "requirements.txt",
    "packaging/requirements-build.txt",
    "packaging/verify_runtime_dependencies.py",
    "packaging/build_windows_release.ps1",
    "tests/test_dependency_lock.py",
    "tests/test_ground_station_rescue_flow.py",
    "tests/test_ground_station_runtime.py",
    "tests/test_rescue_event_manager.py",
    "tests/test_priority_scheduler.py",
    "tests/test_rescue_record_protocol.py",
    "tests/test_sos_pattern.py"
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
