@echo off
REM UTF-8 BOM above: required so cmd.exe parses this file as UTF-8 (fixes broken echo/set with Chinese Windows ANSI).
chcp 65001 >nul 2>&1
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

if not defined AUTO_START_OLLAMA set "AUTO_START_OLLAMA=1"
if not defined OLLAMA_READY_TIMEOUT set "OLLAMA_READY_TIMEOUT=90"

echo [CWD]=%CD%
echo [OLLAMA] AUTO_START_OLLAMA=!AUTO_START_OLLAMA! OLLAMA_READY_TIMEOUT=!OLLAMA_READY_TIMEOUT!s
echo.

echo [1/2] Starting image hosting in a new window...
start "Showmore Image Hosting" cmd /k python start_image_hosting.py

echo [1/2] Waiting for image_hosting.json (up to 120s)...
set "READY="
for /l %%i in (1,1,120) do (
  if exist "image_hosting.json" (
    set "READY=1"
    goto :ready
  )
  timeout /t 1 /nobreak >nul
)
:ready

if not defined READY (
  echo [ERROR] Timeout: image_hosting.json was not created.
  exit /b 1
)

echo [OK] image_hosting.json is ready. Keep the hosting window open until uploads finish.
echo.

set "START_ROW="
set /p "START_ROW=Enter data start row (e.g. 4): "
if "%START_ROW%"=="" (
  echo [ERROR] START_ROW is empty.
  exit /b 1
)

set "ROW_COUNT="
set /p "ROW_COUNT=Enter row count (min 1): "
if "%ROW_COUNT%"=="" (
  echo [ERROR] ROW_COUNT is empty.
  exit /b 1
)

echo.
echo [2/2] Showmore upload (single login, batch mode)
echo START_ROW=%START_ROW% ROW_COUNT=%ROW_COUNT%
echo.

python sheet_to_showmore_upload.py --data-start-row %START_ROW% --data-row-count %ROW_COUNT%

if errorlevel 1 (
  echo sheet_to_showmore_upload.py failed. Exit.
  exit /b 1
)

echo.
echo Done.
pause
