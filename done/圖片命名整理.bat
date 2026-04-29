@echo off
REM 整理 shared\assets 圖片命名（main_xx / desc_xx / detail_xx）
chcp 65001 >nul 2>&1
cd /d "%~dp0"

python "%~dp0shared\rename_assets_images.py"
set "ERR=%ERRORLEVEL%"

if not "%ERR%"=="0" (
  echo.
  echo [ERROR] Python 結束代碼: %ERR%
  pause
  exit /b %ERR%
)

echo.
pause
