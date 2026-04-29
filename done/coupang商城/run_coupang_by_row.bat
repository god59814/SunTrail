@echo off
REM UTF-8 BOM：讓 cmd 正確處理中文（與 showmore\run_showmore_by_row.bat 相同做法）
chcp 65001 >nul 2>&1
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

if not defined AUTO_START_OLLAMA set "AUTO_START_OLLAMA=1"
if not defined OLLAMA_READY_TIMEOUT set "OLLAMA_READY_TIMEOUT=90"

echo [CWD]=%CD%
echo [OLLAMA] AUTO_START_OLLAMA=!AUTO_START_OLLAMA! OLLAMA_READY_TIMEOUT=!OLLAMA_READY_TIMEOUT!s
echo.

echo [1/2] Starting image hosting in a new window...
start "Coupang商城 Image Hosting" cmd /k python start_image_hosting.py

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
set /p "START_ROW=Enter data start row (e.g. 4, same as GSHEET_DATA_START_ROW): "
if "!START_ROW!"=="" (
  echo [ERROR] data start row is empty.
  exit /b 1
)

set "ROW_COUNT="
set /p "ROW_COUNT=Enter row count (min 1): "
if "!ROW_COUNT!"=="" (
  echo [ERROR] ROW_COUNT is empty.
  exit /b 1
)

echo.
echo [2/2] Coupang商城 upload
echo START_ROW=!START_ROW! ROW_COUNT=!ROW_COUNT!
echo.

python auto_upload.py --gsheet-data-start-row !START_ROW! --gsheet-data-row-count !ROW_COUNT!

if errorlevel 1 (
  echo auto_upload.py failed. Exit.
  exit /b 1
)

echo.
echo Done.
pause
