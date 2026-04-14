import {
  COMMON_REQUIRED_FIELDS,
  PLATFORM_REQUIRED_FIELDS,
  FIELD_LABELS,
  type PlatformOption,
} from '../config/fieldConfig';

export type PrimitiveValue = string | number | boolean | null;

/**
 * MVP 先用寬鬆 common 結構：
 * - 先支撐「表格編輯 + 驗證缺欄位」
 * - 之後再依欄位型別（number/boolean/date）逐步收斂
 */
export type ProductCommon = Record<string, PrimitiveValue>;

export type ProductRow = {
  id: string;
  source: {
    sheetName: string;
    excelRow: number;
  };
  targets: PlatformOption[];
  common: ProductCommon;
  platformData: Record<string, Record<string, PrimitiveValue> | undefined>;
};

export type ValidationIssue = {
  productId: string;
  sheetName: string;
  excelRow: number;
  columnKey: string;
  columnLabel: string;
  issueType: 'missing' | 'invalid_number';
  message: string;
  platform?: string;
};

export type ValidationResult = {
  isValid: boolean;
  commonMissing: string[];
  commonInvalidNumeric: string[];
  platformMissing: Partial<Record<PlatformOption, string[]>>;
  totalIssueCount: number;
  issues: ValidationIssue[];
};

function isEmptyValue(value: unknown) {
  if (value === null || value === undefined) return true;
  if (typeof value === 'string') return value.trim().length === 0;
  return false;
}

function isNumericValue(value: unknown) {
  if (typeof value === 'number') return Number.isFinite(value);
  if (typeof value !== 'string') return false;
  const text = value.trim();
  if (!text) return false;
  const normalized = text.replaceAll(',', '');
  const asNumber = Number(normalized);
  return Number.isFinite(asNumber);
}

export function validateProduct(product: ProductRow): ValidationResult {
  const commonMissing = COMMON_REQUIRED_FIELDS.filter((field) => isEmptyValue(product.common[field]));
  const numericFields = [
    'list_price',
    'sale_price',
    'cost_price',
    'stock_qty',
    'length_cm',
    'width_cm',
    'height_cm',
    'weight_kg',
  ] as const;

  const commonInvalidNumeric = numericFields.filter((field) => {
    const value = product.common[field];
    if (isEmptyValue(value)) return false; // 空值不算格式錯誤（交由缺失規則處理）
    return !isNumericValue(value);
  });

  const platformMissing: Partial<Record<PlatformOption, string[]>> = {};

  for (const target of product.targets) {
    const requiredFields = PLATFORM_REQUIRED_FIELDS[target] ?? [];

    const missing = requiredFields.filter((fieldKey) => {
      // 先採「兩段式」查找：common → platformData[target]
      if (!isEmptyValue(product.common[fieldKey])) return false;
      const platformObj = product.platformData[target];
      return isEmptyValue(platformObj?.[fieldKey]);
    });

    if (missing.length) platformMissing[target] = missing;
  }

  const issues: ValidationIssue[] = [
    ...commonMissing.map((field) => ({
      productId: product.id,
      sheetName: product.source.sheetName,
      excelRow: product.source.excelRow,
      columnKey: field,
      columnLabel: FIELD_LABELS[field] ?? field,
      issueType: 'missing' as const,
      message: '必填欄位缺失',
    })),
    ...commonInvalidNumeric.map((field) => ({
      productId: product.id,
      sheetName: product.source.sheetName,
      excelRow: product.source.excelRow,
      columnKey: field,
      columnLabel: FIELD_LABELS[field] ?? field,
      issueType: 'invalid_number' as const,
      message: '格式錯誤，需為數字',
    })),
    ...Object.entries(platformMissing).flatMap(([platform, fields]) =>
      (fields ?? []).map((field) => ({
        productId: product.id,
        sheetName: product.source.sheetName,
        excelRow: product.source.excelRow,
        columnKey: field,
        columnLabel: FIELD_LABELS[field] ?? field,
        issueType: 'missing' as const,
        message: `平台必填欄位缺失（${platform}）`,
        platform,
      })),
    ),
  ];

  return {
    isValid: issues.length === 0,
    commonMissing: [...commonMissing],
    commonInvalidNumeric,
    platformMissing,
    totalIssueCount: issues.length,
    issues,
  };
}

