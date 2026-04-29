@echo off
chcp 65001 >nul 2>&1
setlocal EnableExtensions

cd /d "%~dp0"

if not defined AUTO_START_OLLAMA set AUTO_START_OLLAMA=1
if not defined OLLAMA_READY_TIMEOUT set OLLAMA_READY_TIMEOUT=90

echo [CWD]=%CD%
echo [OLLAMA] AUTO_START_OLLAMA=%AUTO_START_OLLAMA% OLLAMA_READY_TIMEOUT=%OLLAMA_READY_TIMEOUT%s
echo.

set TEMPLATE=MassUploadListingRequestTemplate_TW.xlsm
set CATEGORY_XLS=Shopee_category_list.xls
set OUTPUT_XLSX=out\mass_upload_ready.xlsx

if not exist "%TEMPLATE%" (
  echo [ERROR] Missing template: %TEMPLATE%
  exit /b 1
)
if not exist "%CATEGORY_XLS%" (
  echo [ERROR] Missing category file: %CATEGORY_XLS%
  exit /b 1
)

set PRINT_TOPK=5
set /p PRINT_TOPK=Enter --print-category-topk (default 5, 0 = disable): 
if "%PRINT_TOPK%"=="" set PRINT_TOPK=5

echo.
echo [1/2] Build Shopee upload xlsx...

if "%PRINT_TOPK%"=="0" (
  echo python build_shopee_mass_upload_with_category.py --use-gsheet --template "%TEMPLATE%" --category-xls "%CATEGORY_XLS%" --output-xlsx "%OUTPUT_XLSX%" --use-rag-ai-category
  python build_shopee_mass_upload_with_category.py --use-gsheet --template "%TEMPLATE%" --category-xls "%CATEGORY_XLS%" --output-xlsx "%OUTPUT_XLSX%" --use-rag-ai-category
) else (
  echo python build_shopee_mass_upload_with_category.py --use-gsheet --template "%TEMPLATE%" --category-xls "%CATEGORY_XLS%" --output-xlsx "%OUTPUT_XLSX%" --use-rag-ai-category --print-category-topk %PRINT_TOPK%
  python build_shopee_mass_upload_with_category.py --use-gsheet --template "%TEMPLATE%" --category-xls "%CATEGORY_XLS%" --output-xlsx "%OUTPUT_XLSX%" --use-rag-ai-category --print-category-topk %PRINT_TOPK%
)
if errorlevel 1 (
  echo [ERROR] build_shopee_mass_upload_with_category.py failed
  exit /b 1
)

if not exist "%OUTPUT_XLSX%" (
  echo [ERROR] Missing output xlsx: %OUTPUT_XLSX%
  exit /b 1
)

echo.
echo [2/2] Upload Shopee xlsx...
python 2shopee_bulk_upload.py --file "%OUTPUT_XLSX%"
if errorlevel 1 (
  echo [ERROR] 2shopee_bulk_upload.py failed
  exit /b 1
)

echo.
echo [OK] Shopee flow done
pause
endlocal
exit /b 0
