param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$PackagingRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $PackagingRoot
Set-Location $ProjectRoot
$env:PYTHONPATH = Join-Path $ProjectRoot "src"

& $Python -m PyInstaller packaging/ground_station_V7.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed."
}

$AppVersion = (& $Python -c "from app_metadata import APP_VERSION; print(APP_VERSION)" | Out-String).Trim()
$ReleaseName = (& $Python -c "from app_metadata import WINDOWS_RELEASE_NAME; print(WINDOWS_RELEASE_NAME)" | Out-String).Trim()
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

Write-Host "Windows release artifact: $Artifact"
Write-Host "Firebase credentials remain external and are not bundled."
