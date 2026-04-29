# 商品上架工作台（web）

此專案提供一個 xlsm 上傳與檢核流程，支援：

- 解析 `main` / `price_tag` 資料
- 檢核缺漏與格式錯誤
- 在網頁上快速調整售價與抽成欄位
- 將整理後資料寫入 Google Sheet
- 保存原始上傳檔並記錄 upload log（時間、IP、平台、檔案路徑）

---

## 技術棧

- React + TypeScript + Vite
- AG Grid（錯誤清單）
- `xlsx`（Excel 解析）
- `googleapis`（Google Sheet append）
- Vite middleware（API：`/api/upload-to-sheet`、`/api/sheet-health`）

---

## 快速開始

### 1) 安裝

```bash
npm install
```

### 2) 設定 Google Sheet 參數

修改 `src/server/sheetUploadConfig.ts`：

```ts
export const SHEET_UPLOAD_CONFIG = {
  cred_path: 'shared/config/你的憑證.json',
  spreadsheet_id: '你的試算表ID',
  worksheet_name: '上架_PM',
  header_row: 2,
  data_start_row: 4,
};
```

把服務帳戶 JSON 憑證放到 `shared/config/`（此路徑已被 `.gitignore` 排除）。

### 3) 啟動

```bash
npm run dev
```

---

## 區網分享（同網路）

```bash
npm run dev -- --host 0.0.0.0 --port 5173
```

同網路裝置使用：

- `http://<你的內網IP>:5173`

---

## 常用指令

- 開發：`npm run dev`
- 建置：`npm run build`
- 預覽：`npm run preview`
- Lint：`npm run lint`

---

## 專案流程（使用者視角）

1. 開啟首頁，先看到操作說明。
2. 上傳 `xlsm` 檔案。
3. 若有錯誤：左側顯示缺漏/錯誤表格，右側提供修正提示。
4. 若檢核通過：可確認售價與抽成，按「驗證」後可按「上傳到 Google Sheet」。

---

## API 與健康檢查

- `GET /api/sheet-health`
  - 檢查憑證是否可用、是否可讀取 worksheet 表頭。
- `POST /api/upload-to-sheet`
  - 寫入 Google Sheet，並保存原始檔與 upload log。

---

## 日誌與檔案保存

- 上傳檔案：`shared/uploads/YYYY-MM-DD/...`
- 上傳日誌：`shared/logs/upload-log.jsonl`

每筆 log 會包含：時間、IP、平台、原始檔資訊、儲存路徑、寫入列數。

---

## 文件索引

- 完整部署/架構/參數文件：`docs/系統部署與參數設定說明.md`

---

## 常見問題

- 啟動時出現 `[sheet-health] FAIL`：
  - 檢查憑證路徑、JSON 是否有效、試算表是否已分享給服務帳戶。
- 同網路連不到：
  - 確認 `--host 0.0.0.0`、防火牆放行 port、雙方在同網段。
