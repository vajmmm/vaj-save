# Build dist/vaj-save/vaj-save.exe on Windows. Run from a Developer PowerShell
# with Python 3.11+ on PATH.
$ErrorActionPreference = "Stop"

if ($env:OS -notlike "*Windows*") {
    Write-Error "This script builds a Windows .exe on Windows only."
    exit 1
}

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = if ($env:PYTHON) { $env:PYTHON } else { "python" }

& $Python -m pip install -e ".[packaging]"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $Python -m PyInstaller --noconfirm --clean "$Root\packaging\vaj-save-windows.spec"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$Exe = Join-Path $Root "dist\vaj-save\vaj-save.exe"
if (-not (Test-Path $Exe)) {
    Write-Error "Build failed: $Exe not found"
    exit 1
}

Write-Host "Built: $Exe"
Write-Host "Copy the whole dist\vaj-save\ folder (not just the exe)."
