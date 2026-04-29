@echo off
cd /d "%~dp0"

powershell -NoExit -ExecutionPolicy Bypass -Command ^
    "Set-Location -LiteralPath '%~dp0'; cmd /c '.\done\shared\run_platform_upload.bat'"
