@echo off
setlocal EnableDelayedExpansion

set ROOT=%~dp0
cd /d "%ROOT%"

if not defined AUTO_START_OLLAMA set "AUTO_START_OLLAMA=1"
if not defined OLLAMA_READY_TIMEOUT set "OLLAMA_READY_TIMEOUT=90"

echo [CWD]=%CD%
echo [ROOT]=%ROOT%
echo [OLLAMA] AUTO_START_OLLAMA=%AUTO_START_OLLAMA% OLLAMA_READY_TIMEOUT=%OLLAMA_READY_TIMEOUT%s
echo.

set /p ENTP_CODE=Enter entpCode (from config\all_login_info.json): 
if "%ENTP_CODE%"=="" (
  echo ENTP_CODE is empty. Exit.
  exit /b 1
)

python "%ROOT%select_login_info.py" --entpCode "%ENTP_CODE%"
if errorlevel 1 (
  echo select_login_info.py failed. Exit.
  exit /b 1
)

set /p START_ROW=Enter data start row (ex: 4): 
if "%START_ROW%"=="" (
  echo START_ROW is empty. Exit.
  exit /b 1
)

set /p ROW_COUNT=Enter row count (min 1): 
if "%ROW_COUNT%"=="" (
  echo ROW_COUNT is empty. Exit.
  exit /b 1
)

set /a ROW_COUNT_INT=%ROW_COUNT%
if %ROW_COUNT_INT% LSS 1 set ROW_COUNT_INT=1

set /a END_INDEX=%ROW_COUNT_INT%-1

echo.
echo START_ROW=%START_ROW% ROW_COUNT=%ROW_COUNT_INT%
echo.

set PAYLOAD_PY="%ROOT%momotest\xlsx_to_payload.py"
set PACK_PY="%ROOT%momo_auto_pack.py"
set REPORT_PY="%ROOT%report_goods.py"

if not exist "%ROOT%momotest\xlsx_to_payload.py" (
  echo Missing script: %PAYLOAD_PY%
  exit /b 1
)
if not exist "%ROOT%momo_auto_pack.py" (
  echo Missing script: %PACK_PY%
  exit /b 1
)
if not exist "%ROOT%report_goods.py" (
  echo Missing script: %REPORT_PY%
  exit /b 1
)

for /l %%i in (0,1,%END_INDEX%) do (
  set /a CUR_START=%START_ROW%+%%i
  set /a DISPLAY_INDEX=%%i+1

  echo ============================================================
  echo Run !DISPLAY_INDEX! / %ROW_COUNT_INT% : --data-start-row=!CUR_START! --data-row-count=1
  echo ============================================================

  python "%ROOT%momotest\xlsx_to_payload.py" --data-start-row !CUR_START! --data-row-count 1
  if errorlevel 1 (
    echo xlsx_to_payload.py failed. Exit.
    exit /b 1
  )

  python "%ROOT%momo_auto_pack.py" --payload "%ROOT%payload.json" --asset-root "%ROOT%..\shared\assets" --output-dir "%ROOT%out"
  if errorlevel 1 (
    echo momo_auto_pack.py failed. Exit.
    exit /b 1
  )

  python "%ROOT%report_goods.py"
  if errorlevel 1 (
    echo report_goods.py failed. Exit.
    exit /b 1
  )
)

echo.
echo Done.
pause