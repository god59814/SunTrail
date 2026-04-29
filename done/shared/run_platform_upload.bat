@echo off
REM 選擇平台後呼叫 Coupang商城 / Showmore / Momo 既有的上架用 bat
REM 此檔位於 shared\，專案目錄為上一層 work\
chcp 65001 >nul 2>&1
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

if not defined AUTO_START_OLLAMA set "AUTO_START_OLLAMA=1"
if not defined OLLAMA_READY_TIMEOUT set "OLLAMA_READY_TIMEOUT=90"

echo.
echo ========================================
echo   上架平台選擇
echo ========================================
echo   1  Coupang商城   [自動開圖片服務視窗]
echo   2  Showmore       [自動開圖片服務視窗]
echo   3  Momo
echo   4  Shopee
echo   0  離開
echo ========================================
echo   OLLAMA: AUTO_START_OLLAMA=!AUTO_START_OLLAMA!  TIMEOUT=!OLLAMA_READY_TIMEOUT!s
REM Showmore 流程會使用預設登入 suntrailrobot 帳號（gmail）
echo.

set "CHOICE="
set /p "CHOICE=請輸入數字 0-4: "

if "!CHOICE!"=="" (
  echo [ERROR] 未輸入選項。
  goto :end
)

if "!CHOICE!"=="0" (
  echo 已取消。
  goto :end
)

if "!CHOICE!"=="1" (
  call "%~dp0..\coupang商城\run_coupang_by_row.bat"
  goto :done
)

if "!CHOICE!"=="2" (
  call "%~dp0..\showmore\run_showmore_by_row.bat"
  goto :done
)

if "!CHOICE!"=="3" (
  call "%~dp0..\momo\run_momo_by_row.bat"
  goto :done
)

if "!CHOICE!"=="4" (
  call "%~dp0..\..\shopee\run_shopee_by_row.bat"
  goto :done
)

echo [ERROR] 無效的選項: !CHOICE! 請輸入 0..4

:end
echo.
pause
endlocal
exit /b 0

:done
endlocal
exit /b 0
