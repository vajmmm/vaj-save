@echo off
setlocal enableextensions
cd /d "%~dp0.."

if not "%OS%"=="Windows_NT" (
  echo This script builds a Windows .exe on Windows only.
  exit /b 1
)

if "%PYTHON%"=="" set PYTHON=python

"%PYTHON%" -m pip install -e ".[packaging]"
if errorlevel 1 exit /b 1

"%PYTHON%" -m PyInstaller --noconfirm --clean "%CD%\packaging\vaj-save-windows.spec"
if errorlevel 1 exit /b 1

if not exist "%CD%\dist\vaj-save\vaj-save.exe" (
  echo Build failed: dist\vaj-save\vaj-save.exe not found
  exit /b 1
)

echo Built: %CD%\dist\vaj-save\vaj-save.exe
echo Copy the whole dist\vaj-save\ folder (not just the exe).
