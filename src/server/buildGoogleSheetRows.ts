import type { ParsedWorkbookData } from './parseWorkbook';

/** 與 Google Sheet 第 2 列英文 key 一致，供 append 動態對欄 */
export type GoogleSheetRow = {
  platform: string;
  erp_product_name?: string;
  sale_product_name?: string;
  slogan?: string;
  feature?: string;
  product_description?: string;
  erp_sku: string;
  barcode?: string;
  model_no?: string;
  brand?: string;
  spec_name_1?: string;
  spec_value_1?: string;
  spec_name_2?: string;
  spec_value_2?: string;
  length_cm?: string | number;
  width_cm?: string | number;
  height_cm?: string | number;
  weight_kg?: string | number;
  tax_type?: string;
  list_price?: string | number;
  sale_price?: string | number;
  cost_price?: string | number;
  stock_qty?: string | number;
  carton_qty?: string | number;
  is_preorder?: string | boolean;
  expected_ship_date?: string;
  ship_days_after_order?: string | number;
  is_scheduled_delivery?: string | boolean;
  is_recyclable?: string | boolean;
  is_large_appliance?: string | boolean;
  delivery_type?: string;
  temperature_type?: string;
  product_attribute?: string;
  origin_country?: string;
  shelf_life_days?: string | number;
  youtube_url?: string;
  warranty_days?: string | number;
  BSMI?: string;
  NCC?: string;
  order_type?: string;
  attention_item?: string;
  use_method?: string;
  eType?: string;
  free_shipping?: string | boolean;
};

export type BuildGoogleSheetRowsParams = ParsedWorkbookData & {
  editedPriceTagRows?: ParsedWorkbookData['priceTagRows'];
  extraInfoBySku?: Record<string, Record<string, string>>;
};

export type BuildGoogleSheetRowsResult = {
  rows: GoogleSheetRow[];
  missingPriceTagSkus: string[];
  missingPlatformMappings: string[];
};

function normalizeCell(value: unknown): string {
  return String(value ?? '').trim();
}

function parsePercent(value: unknown): number | null {
  const text = normalizeCell(value).replace('％', '%');
  if (!text) return null;

  if (text.endsWith('%')) {
    const num = Number(text.slice(0, -1));
    return Number.isNaN(num) ? null : num / 100;
  }

  const num = Number(text);
  if (Number.isNaN(num)) return null;
  return num > 1 ? num / 100 : num;
}

function parseNumeric(value: unknown): number | null {
  const text = normalizeCell(value).replaceAll(',', '');
  if (!text) return null;
  const num = Number(text);
  return Number.isFinite(num) ? num : null;
}

function computeCostPrice(salePrice: unknown, fee: unknown): number | '' {
  const sale = parseNumeric(salePrice);
  const feeRatio = parsePercent(fee);
  if (sale === null || feeRatio === null) return '';
  return sale * (1 - feeRatio);
}

function resolveSalePrice(platformPrice: unknown, allPrice: unknown): unknown {
  if (normalizeCell(platformPrice) !== '') return platformPrice;
  if (normalizeCell(allPrice) !== '') return allPrice;
  return '';
}

function resolveCostPrice(mappedCost: unknown, salePrice: unknown, fee: unknown): number | string {
  if (normalizeCell(mappedCost) !== '') return normalizeCell(mappedCost);
  return computeCostPrice(salePrice, fee);
}

function mainCell(row: Record<string, unknown>, ...keys: string[]): string {
  for (const key of keys) {
    const v = row[key];
    if (v !== undefined && v !== null && normalizeCell(v) !== '') return normalizeCell(v);
  }
  return '';
}

export function buildGoogleSheetRows(params: BuildGoogleSheetRowsParams): BuildGoogleSheetRowsResult {
  const {
    mainRows,
    priceTagRows,
    editedPriceTagRows,
    priceMapping,
    selectedPlatforms,
    extraInfoBySku = {},
  } = params;
  const sourcePriceTagRows = editedPriceTagRows ?? priceTagRows;

  const priceTagBySku = new Map<string, (typeof sourcePriceTagRows)[number]>();
  for (const row of sourcePriceTagRows) {
    const sku = normalizeCell(row.erp_sku);
    if (!sku) continue;
    priceTagBySku.set(sku, row);
  }

  const rows: GoogleSheetRow[] = [];
  const missingPriceTagSkus = new Set<string>();
  const missingPlatformMappings = new Set<string>();

  for (const mainRow of mainRows) {
    const sku = normalizeCell(mainRow.erp_sku);
    if (!sku) continue;

    const priceTagRow = priceTagBySku.get(sku);
    if (!priceTagRow) {
      missingPriceTagSkus.add(sku);
      continue;
    }

    const main = mainRow as Record<string, unknown>;
    const extra = extraInfoBySku[sku] ?? {};

    for (const platform of selectedPlatforms) {
      const mapping = priceMapping[platform];
      if (!mapping) {
        missingPlatformMappings.add(platform);
        continue;
      }

      const platformPrice = mapping.priceField ? priceTagRow[mapping.priceField] : '';
      const allPrice = priceTagRow.all_price;
      const salePrice = resolveSalePrice(platformPrice, allPrice);

      const fee = mapping.feeField ? priceTagRow[mapping.feeField] : '';
      const mappedCost = mapping.costField ? priceTagRow[mapping.costField] : '';
      const costPrice = resolveCostPrice(mappedCost, salePrice, fee);

      rows.push({
        platform,
        erp_product_name: mainCell(main, 'erp_product_name'),
        sale_product_name: mainCell(main, 'sale_product_name'),
        slogan: mainCell(main, 'slogan'),
        feature: mainCell(main, 'feature'),
        product_description: mainCell(main, 'product_description'),
        erp_sku: sku,
        barcode: mainCell(main, 'barcode'),
        model_no: mainCell(main, 'model_no'),
        brand: mainCell(main, 'brand'),
        spec_name_1: mainCell(main, 'spec_name_1'),
        spec_value_1: mainCell(main, 'spec_value_1'),
        spec_name_2: mainCell(main, 'spec_name_2'),
        spec_value_2: mainCell(main, 'spec_value_2'),
        length_cm: mainCell(main, 'length_cm'),
        width_cm: mainCell(main, 'width_cm'),
        height_cm: mainCell(main, 'height_cm'),
        weight_kg: mainCell(main, 'weight_kg'),
        tax_type: mainCell(main, 'tax_type'),
        list_price: normalizeCell(priceTagRow.list_price ?? main.list_price),
        sale_price: normalizeCell(salePrice),
        cost_price: typeof costPrice === 'number' ? costPrice : normalizeCell(costPrice),
        stock_qty: mainCell(main, 'stock_qty'),
        carton_qty: mainCell(main, 'carton_qty'),
        is_preorder: mainCell(main, 'is_preorder'),
        expected_ship_date: mainCell(main, 'expected_ship_date'),
        ship_days_after_order: mainCell(main, 'ship_days_after_order'),
        is_scheduled_delivery: mainCell(main, 'is_scheduled_delivery'),
        is_recyclable: mainCell(main, 'is_recyclable'),
        is_large_appliance: mainCell(main, 'is_large_appliance'),
        delivery_type: mainCell(main, 'delivery_type'),
        temperature_type: mainCell(main, 'temperature_type'),
        product_attribute: mainCell(main, 'product_attribute'),
        origin_country: mainCell(main, 'origin_country'),
        shelf_life_days: mainCell(main, 'shelf_life_days'),
        youtube_url: mainCell(main, 'youtube_url'),
        warranty_days: mainCell(main, 'warranty_days'),
        BSMI: normalizeCell(extra.bsmi || main.BSMI || main.bsmi),
        NCC: normalizeCell(extra.ncc || main.NCC || main.ncc),
        order_type: mainCell(main, 'order_type'),
        attention_item: mainCell(main, 'attention_item'),
        use_method: mainCell(main, 'use_method'),
        eType: mainCell(main, 'eType'),
        free_shipping: mainCell(main, 'free_shipping'),
      });
    }
  }

  return {
    rows,
    missingPriceTagSkus: Array.from(missingPriceTagSkus),
    missingPlatformMappings: Array.from(missingPlatformMappings),
  };
}
