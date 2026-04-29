import type { GoogleSheetRow } from './buildGoogleSheetRows';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { google } from 'googleapis';
import type { sheets_v4 } from 'googleapis';

export type GoogleSheetConfig = {
  cred_path: string;
  spreadsheet_id: string;
  worksheet_name: string;
  header_row: number;
  data_start_row: number;
};

export type GoogleSheetHealthCheck = {
  ok: boolean;
  headerCount: number;
  worksheet: string;
  sampleHeaders: string[];
};

/** 單一欄位可對應多種試算表表頭文字（正規化後比對） */
export type SheetHeaderLabels = string | readonly string[];

/** 試算表第 2 列為英文 key，與 GoogleSheetRow 欄位一對一 */
export const GOOGLE_SHEET_FIELD_MAP: Record<keyof GoogleSheetRow, SheetHeaderLabels> = {
  platform: 'platform',
  erp_product_name: 'erp_product_name',
  sale_product_name: 'sale_product_name',
  slogan: 'slogan',
  feature: 'feature',
  product_description: 'product_description',
  erp_sku: 'erp_sku',
  barcode: 'barcode',
  model_no: 'model_no',
  brand: 'brand',
  spec_name_1: 'spec_name_1',
  spec_value_1: 'spec_value_1',
  spec_name_2: 'spec_name_2',
  spec_value_2: 'spec_value_2',
  length_cm: 'length_cm',
  width_cm: 'width_cm',
  height_cm: 'height_cm',
  weight_kg: 'weight_kg',
  tax_type: 'tax_type',
  list_price: 'list_price',
  sale_price: 'sale_price',
  cost_price: 'cost_price',
  stock_qty: 'stock_qty',
  carton_qty: 'carton_qty',
  is_preorder: 'is_preorder',
  expected_ship_date: 'expected_ship_date',
  ship_days_after_order: 'ship_days_after_order',
  is_scheduled_delivery: 'is_scheduled_delivery',
  is_recyclable: 'is_recyclable',
  is_large_appliance: 'is_large_appliance',
  delivery_type: 'delivery_type',
  temperature_type: 'temperature_type',
  product_attribute: 'product_attribute',
  origin_country: 'origin_country',
  shelf_life_days: 'shelf_life_days',
  youtube_url: 'youtube_url',
  warranty_days: 'warranty_days',
  BSMI: 'BSMI',
  NCC: 'NCC',
  order_type: 'order_type',
  attention_item: 'attention_item',
  use_method: 'use_method',
  eType: 'eType',
  free_shipping: 'free_shipping',
};

function labelsForField(v: SheetHeaderLabels): readonly string[] {
  return typeof v === 'string' ? [v] : v;
}

/**
 * 與試算表第 2 列表頭比對用：略過換行、空白、全形括號、常見括號雜訊，避免對不到欄就整列空白。
 */
export function normalizeHeader(value: unknown) {
  return String(value ?? '')
    .replace(/\r?\n/g, '')
    .replace(/\[/g, '')
    .replace(/\]/g, '')
    .replace(/［/g, '')
    .replace(/］/g, '')
    .replace(/\s+/g, '')
    .replace(/（/g, '(')
    .replace(/）/g, ')')
    .trim();
}

function fieldMapKeys(fieldMap: Record<string, SheetHeaderLabels>) {
  return Object.keys(fieldMap) as (keyof GoogleSheetRow)[];
}

export function buildRowByHeaders(
  headers: string[],
  row: GoogleSheetRow,
  fieldMap: Record<string, SheetHeaderLabels> = GOOGLE_SHEET_FIELD_MAP,
): unknown[] {
  return headers.map((header) => {
    const normalizedHeader = normalizeHeader(header);
    const key = fieldMapKeys(fieldMap).find((k) =>
      labelsForField(fieldMap[k]).some((label) => normalizeHeader(label) === normalizedHeader),
    );
    if (!key) return '';
    return row[key] ?? '';
  });
}

/** 除錯用：試算表有表頭、但程式沒有任何欄位能對應到的欄位名（原始字串） */
export function sheetHeadersWithoutMapping(
  headers: string[],
  fieldMap: Record<string, SheetHeaderLabels> = GOOGLE_SHEET_FIELD_MAP,
): string[] {
  const mappedNorm = new Set<string>();
  for (const k of fieldMapKeys(fieldMap)) {
    for (const label of labelsForField(fieldMap[k])) {
      mappedNorm.add(normalizeHeader(label));
    }
  }
  return headers.filter((h) => {
    const nh = normalizeHeader(h);
    return nh !== '' && !mappedNorm.has(nh);
  });
}

async function createSheetsClient(config: GoogleSheetConfig) {
  const credentialPath = resolve(process.cwd(), config.cred_path);
  let credentials: Record<string, unknown>;
  try {
    const credentialRaw = await readFile(credentialPath, 'utf-8');
    credentials = JSON.parse(credentialRaw) as Record<string, unknown>;
  } catch (error) {
    throw new Error(
      `找不到 Google 憑證檔：${credentialPath}。請確認 sheetUploadConfig.ts 的 cred_path 是否正確。原始錯誤：${
        error instanceof Error ? error.message : String(error)
      }`,
    );
  }

  const clientEmail = String(credentials.client_email ?? '');
  const privateKey = String(credentials.private_key ?? '');
  const hasPlaceholderValue =
    clientEmail.includes('REPLACE_WITH_') ||
    privateKey.includes('REPLACE_WITH_') ||
    !clientEmail ||
    !privateKey;
  if (hasPlaceholderValue) {
    throw new Error(
      `Google 憑證檔尚未填入真實內容：${credentialPath}。請用服務帳戶金鑰取代 REPLACE_WITH_* 欄位。`,
    );
  }

  const auth = new google.auth.GoogleAuth({
    credentials,
    scopes: ['https://www.googleapis.com/auth/spreadsheets'],
  });
  return google.sheets({ version: 'v4', auth });
}

async function readSheetHeaders(
  sheets: sheets_v4.Sheets,
  config: GoogleSheetConfig,
): Promise<string[]> {
  const headerRange = `${config.worksheet_name}!${config.header_row}:${config.header_row}`;
  const headerResp = await sheets.spreadsheets.values.get({
    spreadsheetId: config.spreadsheet_id,
    range: headerRange,
  });
  const headers = (headerResp.data.values?.[0] ?? []).map((v) => String(v ?? ''));
  if (!headers.length) {
    throw new Error(
      `無法讀取 Google Sheet 表頭（worksheet=${config.worksheet_name}, header_row=${config.header_row})`,
    );
  }
  return headers;
}

export async function checkGoogleSheetHealth(
  config: GoogleSheetConfig,
): Promise<GoogleSheetHealthCheck> {
  const sheets = await createSheetsClient(config);
  const headers = await readSheetHeaders(sheets, config);
  return {
    ok: true,
    headerCount: headers.length,
    worksheet: config.worksheet_name,
    sampleHeaders: headers.slice(0, 5),
  };
}

/**
 * Google API 實作點：
 * 1) 讀憑證並建立 client
 * 2) 讀 worksheet_name 的 header_row
 * 3) 用 buildRowByHeaders 轉二維陣列
 * 4) append 到最後一列
 */
export async function appendRowsToGoogleSheet(
  rows: GoogleSheetRow[],
  config: GoogleSheetConfig,
): Promise<void> {
  if (!rows.length) return;

  const sheets = await createSheetsClient(config);
  const headers = await readSheetHeaders(sheets, config);

  const unmatched = sheetHeadersWithoutMapping(headers);
  if (unmatched.length) {
    console.warn(
      '[appendRowsToGoogleSheet] 試算表表頭未對應到欄位（請檢查 GOOGLE_SHEET_FIELD_MAP）：',
      unmatched,
    );
  }

  const values = rows.map((row) => buildRowByHeaders(headers, row));
  const appendRange = `${config.worksheet_name}!A${config.data_start_row}`;

  await sheets.spreadsheets.values.append({
    spreadsheetId: config.spreadsheet_id,
    range: appendRange,
    valueInputOption: 'USER_ENTERED',
    insertDataOption: 'INSERT_ROWS',
    requestBody: { values },
  });
}
