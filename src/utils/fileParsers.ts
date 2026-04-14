import * as XLSX from 'xlsx';

type SheetRow = unknown[];

export type ParsedRecord = Record<string, unknown> & {
  __sheetName: string;
  __excelRow: number;
};

function normalizeKey(value: unknown) {
  return String(value ?? '').trim();
}

function isLikelySystemKey(value: string) {
  if (!value) return false;

  const knownKeys = new Set([
    'platform',
    'targets',
    'erp_product_name',
    'sale_product_name',
    'slogan',
    'feature',
    'product_description',
    'erp_sku',
    'barcode',
    'brand',
    'model_no',
    'spec_name_1',
    'spec_value_1',
    'spec_name_2',
    'spec_value_2',
    'length_cm',
    'width_cm',
    'height_cm',
    'weight_kg',
    'list_price',
    'sale_price',
    'cost_price',
    'stock_qty',
    'delivery_type',
    'temperature_type',
  ]);
  return knownKeys.has(value);
}

function isLikelySystemKeyRow(row: SheetRow) {
  const keys = row.map(normalizeKey).filter(Boolean);
  if (!keys.length) return false;

  const hitCount = keys.filter(isLikelySystemKey).length;

  return hitCount >= 3;
}

function isDescriptionLikeCell(text: string) {
  if (!text) return false;

  return (
    text.includes('說明') ||
    text.includes('備註') ||
    text.includes('為基準') ||
    text.includes('請填') ||
    text.includes('必填') ||
    text.includes('textarea') ||
    text.includes('分行') ||
    text.includes('測試值') ||
    text.includes('根據過往屬性複製') ||
    text.includes('ERP[') ||
    text.includes('保固有/無') ||
    text.includes('看材積判斷') ||
    text.includes('無誤') ||
    text.includes('有誤')
  );
}

function isDescriptionRow(row: SheetRow) {
  const cells = row.map(normalizeKey).filter(Boolean);
  if (!cells.length) return false;

  const descHitCount = cells.filter(isDescriptionLikeCell).length;

  return descHitCount >= 1;
}

function mapRowsToRecords(
  keyRow: SheetRow,
  dataRows: SheetRow[],
  sheetName: string,
  startExcelRow: number,
): ParsedRecord[] {
  const keys = keyRow.map(normalizeKey);
  return dataRows
    .filter((row) => row.some((cell) => normalizeKey(cell) !== ''))
    .map((row, idx) => {
      const record: ParsedRecord = {
        __sheetName: sheetName,
        __excelRow: startExcelRow + idx,
      };
      keys.forEach((key, index) => {
        if (!key) return;
        record[key] = row[index] ?? '';
      });
      return record;
    });
}

function sheetToRecordsByFirstRow(worksheet: XLSX.WorkSheet) {
  return XLSX.utils.sheet_to_json<Record<string, unknown>>(worksheet, {
    defval: '',
    raw: false,
    blankrows: false,
  });
}

async function readWorkbookFromFile(file: File): Promise<XLSX.WorkBook> {
  const buffer = await file.arrayBuffer();
  return XLSX.read(buffer, { type: 'array' });
}

export async function parsePlatformOptions(file: File): Promise<string[]> {
  const workbook = await readWorkbookFromFile(file);
  const sheetName = workbook.SheetNames.find((n) => n.toLowerCase() === 'platform');
  if (!sheetName) return [];

  const worksheet = workbook.Sheets[sheetName];
  if (!worksheet) return [];

  const rows = XLSX.utils.sheet_to_json<SheetRow>(worksheet, {
    header: 1,
    defval: '',
    raw: false,
    blankrows: false,
  });

  // 跳過第一列（標題），取 B 欄（index 1）
  const values = rows
    .slice(1)
    .map((row) => normalizeKey(row[1] ?? ''))
    .filter(Boolean);

  return Array.from(new Set(values));
}

export async function parseAnyTabularFile(file: File): Promise<ParsedRecord[]> {
  const workbook = await readWorkbookFromFile(file);
  const firstSheetName = workbook.SheetNames[0];
  if (!firstSheetName) return [];
  const worksheet = workbook.Sheets[firstSheetName];

  const rows = XLSX.utils.sheet_to_json<SheetRow>(worksheet, {
    header: 1,
    defval: '',
    raw: false,
    blankrows: false,
  });
  if (rows.length === 0) return [];

  // 主表 A4 常放全域平台設定，後續可補到每筆資料
  const globalPlatform = normalizeKey(rows[3]?.[0] ?? '');

  // 模板結構：
  // 第1列：中文欄位
  // 第2列：key
  // 第3列：說明
  // 第4列開始：資料
  const findKeyRowIndex = (sheetRows: SheetRow[]) => {
    const maxScan = Math.min(sheetRows.length, 15);
    for (let i = 0; i < maxScan; i += 1) {
      if (isLikelySystemKeyRow(sheetRows[i])) return i;
    }
    return -1;
  };

  const keyRowIndex = findKeyRowIndex(rows);
  if (keyRowIndex === -1) return [];

  let dataStartIndex = keyRowIndex + 1;

  // 若 key 下一列是說明列，就跳過
  if (rows[dataStartIndex] && isDescriptionRow(rows[dataStartIndex])) {
    dataStartIndex += 1;
  }

  // 多一層保險：過濾掉剩餘仍像說明列的 row
  const rawDataRows = rows.slice(dataStartIndex);
  const filteredDataRows = rawDataRows.filter((row) => !isDescriptionRow(row));

  const records = mapRowsToRecords(
    rows[keyRowIndex] ?? [],
    filteredDataRows,
    firstSheetName,
    dataStartIndex + 1,
  );

  // 資料列沒有 platform 時，回填 A4 全域平台
  return records.map((record) => ({
    ...record,
    platform: normalizeKey(record.platform) || globalPlatform,
  }));
}

export async function parseEboFile(file: File): Promise<Record<string, unknown>[]> {
  const buffer = await file.arrayBuffer();
  const workbook = XLSX.read(buffer, { type: 'array' });
  if (!workbook.SheetNames.length) return [];

  const preferred = ['Sheet', 'output_1', 'output_2'];
  const tryNames = [
    ...preferred.filter((name) => workbook.SheetNames.includes(name)),
    ...workbook.SheetNames.filter((name) => !preferred.includes(name)),
  ];

  const scoreHeaders = (rows: Record<string, unknown>[]) => {
    if (!rows.length) return 0;
    const keys = Object.keys(rows[0] ?? {});
    let score = 0;
    if (keys.includes('品號')) score += 3;
    if (keys.includes('進貨發票價(含稅)')) score += 2;
    if (keys.includes('Momo購物')) score += 1;
    if (keys.includes('PCHOME')) score += 1;
    if (keys.includes('蝦皮直送')) score += 1;
    return score;
  };

  let bestRows: Record<string, unknown>[] = [];
  let bestScore = -1;

  for (const name of tryNames) {
    const sheet = workbook.Sheets[name];
    if (!sheet) continue;
    const rows = sheetToRecordsByFirstRow(sheet);
    const score = scoreHeaders(rows);
    if (score > bestScore) {
      bestRows = rows;
      bestScore = score;
    }
  }

  return bestRows;
}
