# SunTrail

四平台自動上架工具（`Coupang商城` / `Shopee` / `Showmore` / `Momo`），支援：
- Google Sheet / xlsx 資料來源
- 圖片整理與上傳流程
- RAG + LLM 類別判斷（含結果記憶庫）
- bat 一鍵選平台執行

---

## 專案目標

將多平台上架流程標準化，減少手動操作，並透過分類記憶庫讓同商品第二次處理更快、可累積為後續訓練素材。

---

## 快速開始

### 1) 安裝 Python 套件

```bash
pip install -r done/requirements.txt
python -m playwright install
```

### 2) 從入口 bat 啟動

在專案根目錄執行：

```bat
上架.bat
```

會開啟平台選單：
- `1` Coupang商城
- `2` Showmore
- `3` Momo
- `4` Shopee

---

## 專案架構（重點）

- `上架.bat`：根目錄入口
- `done/shared/run_platform_upload.bat`：平台選單與分流
- `shopee/`：Shopee 建檔 + 上傳
- `done/coupang商城/`：Coupang商城 上傳流程
- `done/showmore/`：Showmore 上傳流程
- `done/momo/`：Momo payload/打包/送出流程
- `done/shared/llm_core/ollama_client.py`：共用 Ollama client（可自動啟動）
- `done/shared/classify_product.py`：共用 RAG 分類與結果記憶

---

## 各平台入口

### Shopee

- bat：`shopee/run_shopee_by_row.bat`
- 主要腳本：
  - `shopee/build_shopee_mass_upload_with_category.py`
  - `shopee/2shopee_bulk_upload.py`

常用手動指令：

```bash
python shopee/build_shopee_mass_upload_with_category.py --use-gsheet --template shopee/MassUploadListingRequestTemplate_TW.xlsm --category-xls shopee/Shopee_category_list.xls --output-xlsx shopee/out/mass_upload_ready.xlsx --use-rag-ai-category --print-category-topk 5
python shopee/2shopee_bulk_upload.py --file shopee/out/mass_upload_ready.xlsx
```

### Coupang商城

- bat：`done/coupang商城/run_coupang_by_row.bat`
- 主要腳本：
  - `done/coupang商城/auto_upload.py`
  - `done/coupang商城/create_from_xlsx.py`

### Showmore

- bat：`done/showmore/run_showmore_by_row.bat`
- 主要腳本：`done/showmore/sheet_to_showmore_upload.py`

### Momo

- bat：`done/momo/run_momo_by_row.bat`
- 主要腳本：
  - `done/momo/momotest/xlsx_to_payload.py`
  - `done/momo/momo_auto_pack.py`
  - `done/momo/report_goods.py`

---

## RAG 分類與結果記憶

### Shopee 專用

- 服務：`shopee/llm_category_service.py`
- 快取與記憶：
  - `shopee/cache/shopee_categories.json`
  - `shopee/cache/shopee_category_embeddings.npy`
  - `shopee/cache/shopee_rag_memory_log.jsonl`
  - `shopee/cache/shopee_rag_memory_index.json`

### done 共用（可跨平台）

- 服務：`done/shared/classify_product.py`
- 記憶：
  - `done/shared/data/<channel>/classify_cache/rag_result_memory_log.jsonl`
  - `done/shared/data/<channel>/classify_cache/rag_result_memory_index.json`

---

## Ollama 設定

共用環境變數：
- `AUTO_START_OLLAMA=1|0`（預設 1）
- `OLLAMA_READY_TIMEOUT=<秒數>`（預設 90）

若啟用自動啟動，流程在需要 LLM 時會嘗試啟動 `ollama serve`。

---

## 文件

- 詳細操作手冊：`四平台上架與分類操作手冊.md`

---

## 安全注意

- `config/*.json` 內含帳密/憑證，請勿公開上傳。
- 建議將敏感資訊改為環境變數或本機私有設定。
