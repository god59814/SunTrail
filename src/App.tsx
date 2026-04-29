import { useMemo, useRef, useState } from 'react';
import './App.css';
import Header from './components/Header.tsx';
import ProductGrid from './components/ProductGrid.tsx';
import ValidationPanel from './components/ValidationPanel.tsx';
import PriceTagEditor from './components/PriceTagEditor.tsx';
import { PLATFORM_OPTIONS } from './config/fieldConfig';
import {
  parseAnyTabularFile,
  parsePlatformOptions,
  parsePriceMapping,
  type ParsedRecord,
} from './utils/fileParsers';
import { rowsToProducts } from './utils/productMappers';
import { validateProduct, type ProductRow } from './utils/validateProduct';
import type { PriceFieldMeta } from './utils/fileParsers';
import { parseWorkbook, type PriceTagRow } from './server/parseWorkbook';
import { buildGoogleSheetRows } from './server/buildGoogleSheetRows';

function parseNumeric(value: unknown): number | null {
  const text = String(value ?? '').trim().replaceAll(',', '');
  if (!text) return null;
  const asNumber = Number(text);
  if (!Number.isFinite(asNumber)) return null;
  return asNumber;
}

function parseFeeRatio(value: unknown): number | null {
  const text = String(value ?? '').trim().replace('％', '%');
  if (!text) return null;
  if (text.endsWith('%')) {
    const p = parseNumeric(text.slice(0, -1));
    if (p === null) return null;
    return p / 100;
  }
  const n = parseNumeric(text);
  if (n === null) return null;
  return n > 1 ? n / 100 : n;
}

function formatNumber(value: number): string {
  return value.toFixed(4).replace(/\.?0+$/, '');
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result ?? '');
      const base64 = result.includes(',') ? result.split(',')[1] : result;
      resolve(base64);
    };
    reader.onerror = () => reject(reader.error ?? new Error('檔案讀取失敗'));
    reader.readAsDataURL(file);
  });
}

function applyPriceTagFormula(
  row: ParsedRecord,
  lookup: Record<string, PriceFieldMeta>,
  selectedPlatforms: string[],
): ParsedRecord {
  const nextRow: ParsedRecord = { ...row };
  const selectedSet = new Set(selectedPlatforms.map((p) => p.trim().toLowerCase()));
  const grouped = new Map<string, Partial<Record<'price' | 'fee' | 'cost', string>>>();
  Object.entries(lookup).forEach(([fieldKey, meta]) => {
    if (selectedSet.size > 0 && !selectedSet.has(meta.platform.toLowerCase())) return;
    if (!grouped.has(meta.platform)) grouped.set(meta.platform, {});
    grouped.get(meta.platform)![meta.kind] = fieldKey;
  });

  const allPrice = parseNumeric(nextRow.all_price);
  for (const [, fields] of grouped.entries()) {
    const priceKey = fields.price;
    const feeKey = fields.fee;
    const costKey = fields.cost;
    if (!priceKey || !feeKey || !costKey) continue;

    const platformPrice = parseNumeric(nextRow[priceKey]);
    const effectivePrice = platformPrice ?? allPrice;
    const feeRatio = parseFeeRatio(nextRow[feeKey]);

    if (effectivePrice === null || feeRatio === null) {
      nextRow[costKey] = '';
      continue;
    }
    nextRow[costKey] = formatNumber(effectivePrice * (1 - feeRatio));
  }

  return nextRow;
}

export default function App() {
  const [products, setProducts] = useState<ProductRow[]>([]);
  const [rawRecords, setRawRecords] = useState<ParsedRecord[]>([]);
  const [priceTagEditedRecords, setPriceTagEditedRecords] = useState<ParsedRecord[]>([]);
  const [uploadedFile, setUploadedFile] = useState<File | null>(null);
  const [submitLoading, setSubmitLoading] = useState(false);
  const [lastValidatedAt, setLastValidatedAt] = useState<number | null>(null);
  const [uploadedFileName, setUploadedFileName] = useState<string>('');
  const [, setValidatedProducts] = useState<ProductRow[]>([]);
  const [selectedPlatforms, setSelectedPlatforms] = useState<string[]>([]);
  const [priceFieldLookup, setPriceFieldLookup] = useState<Record<string, PriceFieldMeta>>({});
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  function toEditedPriceTagRows(records: ParsedRecord[]): PriceTagRow[] {
    return records.map((record) => ({
      ...record,
      __sheetName: 'price_tag',
      __excelRow: Number(record.__excelRow ?? 0),
      erp_sku: String(record.erp_sku ?? '').trim(),
    }));
  }

  const allValidationResults = useMemo(() => {
    return products.map((product) => ({
      product,
      result: validateProduct(product, priceFieldLookup, selectedPlatforms),
    }));
  }, [products, priceFieldLookup, selectedPlatforms]);

  const allIssues = useMemo(() => {
    return allValidationResults.flatMap(({ result }) => result.issues);
  }, [allValidationResults]);

  const allProductsValid = allIssues.length === 0;
  const hasIssues = allIssues.length > 0;
  const hasUploadedData = rawRecords.length > 0;
  const priceTagRecords = useMemo(() => {
    return rawRecords.filter((record) => record.__sheetName?.toLowerCase() === 'price_tag');
  }, [rawRecords]);
  const displayedProductCount = (priceTagEditedRecords.length ? priceTagEditedRecords : priceTagRecords).length;
  const showSubmitToGoogleSheet = Boolean(lastValidatedAt) && allProductsValid;
  const selectedPlatformsText = selectedPlatforms.length > 0 ? selectedPlatforms.join('、') : '未偵測到平台';
  const selectedFieldSummary = useMemo(() => {
    return selectedPlatforms.map((platform) => {
      const normalizedPlatform = platform.trim().toLowerCase();
      const fields = Object.entries(priceFieldLookup)
        .filter(([, meta]) => meta.platform.trim().toLowerCase() === normalizedPlatform)
        .map(([fieldKey, meta]) => {
          const kindLabel =
            meta.kind === 'price' ? '價格售價' : meta.kind === 'fee' ? '平台抽成' : '平台進價';
          return `${kindLabel}:${fieldKey}`;
        });
      return {
        platform,
        text: fields.length > 0 ? fields.join(' / ') : '未設定欄位對應',
      };
    });
  }, [selectedPlatforms, priceFieldLookup]);

  const handleDownloadTemplate = async () => {
    const templatePath = '/template.xlsm';
    const templateExists = await fetch(templatePath, { method: 'HEAD' }).then((res) => res.ok);
    if (!templateExists) {
      alert('找不到模板 template.xlsm，請先把檔案放到 public/template.xlsm');
      return;
    }

    const link = document.createElement('a');
    link.href = templatePath;
    link.download = 'template.xlsm';
    document.body.appendChild(link);
    link.click();
    link.remove();
  };

  const handleUploadMainFile = async (file: File) => {
    const [workbookData, parsedPlatformOptions, parsedPriceFieldLookup] = await Promise.all([
      parseAnyTabularFile(file),
      parsePlatformOptions(file),
      parsePriceMapping(file),
    ]);

    const nextPlatformOptions = parsedPlatformOptions.length
      ? parsedPlatformOptions
      : [...PLATFORM_OPTIONS];

    const parsedProducts = rowsToProducts(workbookData.records, nextPlatformOptions);
    if (!parsedProducts.length) return;

    setRawRecords(workbookData.records);
    setUploadedFile(file);
    const nextSelectedPlatforms = workbookData.selectedPlatforms;
    const nextPriceTagRecords = workbookData.records
      .filter((record) => record.__sheetName?.toLowerCase() === 'price_tag')
      .map((record) => applyPriceTagFormula(record, parsedPriceFieldLookup, nextSelectedPlatforms));
    setPriceTagEditedRecords(nextPriceTagRecords);
    setSelectedPlatforms(nextSelectedPlatforms);
    setPriceFieldLookup(parsedPriceFieldLookup);
    setProducts(parsedProducts);
    setValidatedProducts(parsedProducts);
    setLastValidatedAt(null);
    setUploadedFileName(file.name);
  };

  const handleValidate = () => {
    setValidatedProducts(products);
    setLastValidatedAt(Date.now());
  };

  const handleSubmitToGoogleSheet = async () => {
    if (!uploadedFile) {
      alert('請先上傳檔案');
      return;
    }

    try {
      setSubmitLoading(true);
      const parsed = await parseWorkbook(uploadedFile);
      const result = buildGoogleSheetRows({
        ...parsed,
        editedPriceTagRows: toEditedPriceTagRows(priceTagEditedRecords),
      });

      const rowsToUpload = result.rows.filter(
        (row) =>
          String(row.erp_sku ?? '').trim() !== '' && String(row.platform ?? '').trim() !== '',
      );

      const response = await fetch('/api/upload-to-sheet', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          rows: rowsToUpload,
          selectedPlatforms,
          originalFile: {
            name: uploadedFile.name,
            mimeType: uploadedFile.type,
            size: uploadedFile.size,
            contentBase64: await fileToBase64(uploadedFile),
          },
        }),
      });

      const data = await response.json();
      if (!response.ok || !data.ok) {
        throw new Error(data.message || 'Google Sheet 寫入失敗');
      }

      alert(`已成功寫入 Google Sheet，共 ${data.appendedCount} 筆`);
    } catch (error) {
      alert(error instanceof Error ? error.message : '送出失敗');
    } finally {
      setSubmitLoading(false);
    }
  };

  return (
    <div className="appShell">
      <Header
        onDownloadTemplate={handleDownloadTemplate}
        onUploadFile={() => fileInputRef.current?.click()}
        uploadedFileName={uploadedFileName}
      />
      <input
        ref={fileInputRef}
        type="file"
        accept=".csv,.xlsx,.xls,.xlsm,text/csv,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        className="hiddenInput"
        onChange={async (event) => {
          const input = event.currentTarget;
          const file = input.files?.[0];
          if (file) await handleUploadMainFile(file);
          input.value = '';
        }}
      />

      {!hasUploadedData ? (
        <div className="onboardingCard">
          <h2>使用說明</h2>
          <ol>
            <li>先下載模板，並在 xlsm 內填寫或更新商品資料。</li>
            <li>點擊「上傳檔案」上傳 xlsm。</li>
            <li>依檢核結果修正缺漏，或在表格確認售價與抽成。</li>
            <li>按「驗證」確認無誤後，再按「上傳到 Google Sheet」。</li>
          </ol>
        </div>
      ) : (
        <>
          <div className="selectionSummaryBar">
            <div className="selectionSummaryTitle">目前已選平台：{selectedPlatformsText}</div>
            {selectedFieldSummary.length > 0 ? (
              <div className="selectionSummaryFields">
                {selectedFieldSummary.map((item) => (
                  <div key={item.platform} className="selectionSummaryFieldItem">
                    <strong>{item.platform}</strong>
                    <span>{item.text}</span>
                  </div>
                ))}
              </div>
            ) : null}
          </div>

          <div className={`content ${hasIssues ? 'contentError' : 'contentOk'}`}>
            <div className="gridPane">
              {hasIssues ? (
                <ProductGrid issues={allIssues} />
              ) : (
                <PriceTagEditor
                  records={priceTagEditedRecords.length ? priceTagEditedRecords : priceTagRecords}
                  priceFieldLookup={priceFieldLookup}
                  selectedPlatforms={selectedPlatforms}
                  onChangeRecords={setPriceTagEditedRecords}
                  hint="缺漏檢查通過，請過目一遍售價與抽成，確認無誤後點擊「驗證」，系統確認沒有問題後再上傳到 Google Sheet。"
                />
              )}
            </div>

            <aside className="sidePane">
              <ValidationPanel
                issues={allIssues}
                lastValidatedAt={lastValidatedAt}
                successProductCount={displayedProductCount}
                onValidate={handleValidate}
                onSubmitToGoogleSheet={handleSubmitToGoogleSheet}
                showSubmitToGoogleSheet={showSubmitToGoogleSheet}
                canSubmitToGoogleSheet={showSubmitToGoogleSheet}
                submitLoading={submitLoading}
              />
            </aside>
          </div>
        </>
      )}
    </div>
  );
}
