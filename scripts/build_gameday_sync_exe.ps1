# Build GamedaySyncPatch.exe — Windows one-file patcher for PrestoSync / Gameday LiveStats desktop app.
# Prerequisites: Python 3.x with pip; Node.js + npm (for npx @electron/asar at runtime when patching).
#
#   powershell -ExecutionPolicy Bypass -File scripts/build_gameday_sync_exe.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

py -3 -m pip install --upgrade pyinstaller

# --onefile: single GamedaySyncPatch.exe; console for log output
py -3 -m PyInstaller `
    --onefile `
    --console `
    --name "GamedaySyncPatch" `
    --clean `
    "scripts\patch_gameday_sync.py"

Write-Host "Built: dist\GamedaySyncPatch.exe"
Write-Host "Example: dist\GamedaySyncPatch.exe --default-windows-install"
Write-Host '  (patches %LOCALAPPDATA%\Programs\prestosports-prestosync\resources\app.asar)'
