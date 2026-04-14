import { type PlatformOption } from '../config/fieldConfig';
import type { PrimitiveValue, ProductRow } from './validateProduct';

function asString(v: unknown) {
  return String(v ?? '').trim();
}

function toPrimitive(v: unknown): PrimitiveValue {
  const text = asString(v);
  if (text === '') return '';
  const asNumber = Number(text);
  if (!Number.isNaN(asNumber)) return asNumber;
  return text;
}

function normalizePlatformKey(v: string): string {
  return v.trim().replaceAll('＋', '+').replaceAll(/\s/g, '').toLowerCase();
}

function parseTargets(value: unknown, availableOptions: string[]): PlatformOption[] {
  const raw = asString(value);
  if (!raw) return [];

  const normalized = raw
    .replaceAll('\r', '')
    .replaceAll('\n', '|')
    .replaceAll('；', ';')
    .replaceAll('，', ',')
    .trim();

  // 優先比對 template platform sheet 裡的選項（完整名稱）
  const optionMap = new Map(
    availableOptions.map((option) => [normalizePlatformKey(option), option]),
  );

  const matchPlatform = (v: string): PlatformOption | null => {
    const s = normalizePlatformKey(v);
    if (!s) return null;

    // 精確比對 template 選項（正規化後）
    if (optionMap.has(s)) return optionMap.get(s)!;

    return null;
  };

  return Array.from(
    new Set(
      normalized
        .split(/[|,、;；]/)
        .map((s) => matchPlatform(s))
        .filter((s): s is PlatformOption => s !== null),
    ),
  );
}

export function rowsToProducts(rows: Record<string, unknown>[], availableOptions: string[] = []): ProductRow[] {
  return rows.map((row, index) => ({
    id: `upload-${index + 1}`,
    source: {
      sheetName: String(row.__sheetName ?? ''),
      excelRow: Number(row.__excelRow ?? 0),
    },
    targets: parseTargets(row.platform ?? row.targets ?? row['上架平台'], availableOptions),
    common: {
      erp_product_name: toPrimitive(row.erp_product_name ?? row['ERP品名']),
      sale_product_name: toPrimitive(row.sale_product_name ?? row['銷售品名(相同當作同一賣場)'] ?? row['銷售品名']),
      slogan: toPrimitive(row.slogan ?? row['商品特色標語']),
      feature: toPrimitive(row.feature ?? row['商品特色']),
      product_description: toPrimitive(row.product_description ?? row['商品文案']),
      erp_sku: toPrimitive(row.erp_sku ?? row['ERP品號']),
      barcode: toPrimitive(row.barcode ?? row['國際條碼']),
      brand: toPrimitive(row.brand ?? row['品牌']),
      spec_name_1: toPrimitive(row.spec_name_1 ?? row['規格名稱1']),
      spec_value_1: toPrimitive(row.spec_value_1 ?? row['規格內容1']),
      spec_name_2: toPrimitive(row.spec_name_2 ?? row['規格名稱2']),
      spec_value_2: toPrimitive(row.spec_value_2 ?? row['規格內容2']),
      length_cm: toPrimitive(row.length_cm ?? row['商品材積\n長(cm)'] ?? row['長(cm)']),
      width_cm: toPrimitive(row.width_cm ?? row['商品材積\n寬(cm)'] ?? row['寬(cm)']),
      height_cm: toPrimitive(row.height_cm ?? row['商品材積\n高(cm)'] ?? row['高(cm)']),
      weight_kg: toPrimitive(row.weight_kg ?? row['重量(KG)']),
      list_price: toPrimitive(row.list_price ?? row['價格\n建議售價'] ?? row['建議售價']),
      sale_price: toPrimitive(row.sale_price ?? row['價格\n售價'] ?? row['售價']),
      cost_price: toPrimitive(row.cost_price ?? row['價格\n進價/成本價'] ?? row['進價/成本價']),
      stock_qty: toPrimitive(row.stock_qty ?? row['庫存']),
      delivery_type: toPrimitive(row.delivery_type ?? row['配送方式']),
      temperature_type: toPrimitive(row.temperature_type ?? row['商品溫層']),
      model_no: toPrimitive(row.model_no ?? row['商品型號\n(momo大家電必填)'] ?? row['商品型號']),
    },
    platformData: {
      誠品: {
        supplier_id: toPrimitive(row.supplier_id ?? row['(誠品)\n供應商ID']),
        buyer_code: toPrimitive(row.buyer_code ?? row['(誠品)\n採購人員代碼']),
      },
    },
  }));
}
