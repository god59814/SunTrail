import { FIELD_LABELS, type PlatformOption } from '../config/fieldConfig';
import type { PriceFieldMeta } from './fileParsers';

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
  raw: Record<string, unknown>;
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

const MAIN_REQUIRED_FIELDS = [
  'erp_product_name',
  'sale_product_name',
  'brand',
  'stock_qty',
  'delivery_type',
  'temperature_type',
] as const;

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

function isPercentageValue(value: unknown) {
  if (typeof value === 'number') return Number.isFinite(value);
  if (typeof value !== 'string') return false;

  const text = value.trim().replace('％', '%');
  if (!text) return false;

  if (text.endsWith('%')) {
    const numericPart = text.slice(0, -1).trim().replaceAll(',', '');
    if (!numericPart) return false;
    const asNumber = Number(numericPart);
    return Number.isFinite(asNumber);
  }

  const asNumber = Number(text.replaceAll(',', ''));
  return Number.isFinite(asNumber);
}

function buildIssue(
  product: ProductRow,
  columnKey: string,
  issueType: 'missing' | 'invalid_number',
  message: string,
  platform?: string,
): ValidationIssue {
  return {
    productId: product.id,
    sheetName: product.source.sheetName,
    excelRow: product.source.excelRow,
    columnKey,
    columnLabel: FIELD_LABELS[columnKey] ?? columnKey,
    issueType,
    message,
    platform,
  };
}

function validateMain(product: ProductRow): ValidationResult {
  const commonMissing = MAIN_REQUIRED_FIELDS.filter((field) => isEmptyValue(product.common[field]));
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
    if (isEmptyValue(value)) return false;
    return !isNumericValue(value);
  });

  const platformMissing: Partial<Record<PlatformOption, string[]>> = {};

  const issues: ValidationIssue[] = [
    ...commonMissing.map((field) => buildIssue(product, field, 'missing', '必填欄位缺失')),
    ...commonInvalidNumeric.map((field) =>
      buildIssue(product, field, 'invalid_number', '格式錯誤，需為數字'),
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

function validatePriceTag(
  product: ProductRow,
  priceFieldLookup: Record<string, PriceFieldMeta>,
  selectedPlatforms: string[],
): ValidationResult {
  const issues: ValidationIssue[] = [];
  const commonMissing: string[] = [];
  const commonInvalidNumeric: string[] = [];
  const platformMissing: Partial<Record<PlatformOption, string[]>> = {};

  const listPrice = product.raw.list_price;
  const allPrice = product.raw.all_price;

  if (isEmptyValue(listPrice)) {
    commonMissing.push('list_price');
    issues.push(buildIssue(product, 'list_price', 'missing', '建議售價缺失'));
  } else if (!isNumericValue(listPrice)) {
    commonInvalidNumeric.push('list_price');
    issues.push(buildIssue(product, 'list_price', 'invalid_number', '格式錯誤，需為數字'));
  }

  if (!isEmptyValue(allPrice) && !isNumericValue(allPrice)) {
    commonInvalidNumeric.push('all_price');
    issues.push(buildIssue(product, 'all_price', 'invalid_number', '格式錯誤，需為數字'));
  }

  const hasAllPrice = !isEmptyValue(allPrice);
  const selectedPlatformSet = new Set(selectedPlatforms.map((p) => p.trim().toLowerCase()));
  const grouped = new Map<string, Partial<Record<'price' | 'fee' | 'cost', string>>>();
  for (const [fieldKey, meta] of Object.entries(priceFieldLookup)) {
    if (selectedPlatformSet.size > 0 && !selectedPlatformSet.has(meta.platform.toLowerCase())) continue;
    if (!grouped.has(meta.platform)) grouped.set(meta.platform, {});
    grouped.get(meta.platform)![meta.kind] = fieldKey;
  }

  for (const [platform, fields] of grouped.entries()) {
    const priceFieldKey = fields.price;
    const feeFieldKey = fields.fee;
    const priceValue = priceFieldKey ? product.raw[priceFieldKey] : '';
    const feeValue = feeFieldKey ? product.raw[feeFieldKey] : '';

    const hasPlatformPrice = !isEmptyValue(priceValue);
    if (!hasAllPrice && priceFieldKey && !hasPlatformPrice) {
      issues.push(buildIssue(product, priceFieldKey, 'missing', `平台售價缺失（${platform}）`, platform));
    }
    if (hasPlatformPrice && priceFieldKey && !isNumericValue(priceValue)) {
      issues.push(
        buildIssue(product, priceFieldKey, 'invalid_number', `平台售價格式錯誤（${platform}）`, platform),
      );
    }

    const hasUsablePrice = hasAllPrice || hasPlatformPrice;
    if (hasUsablePrice && feeFieldKey && isEmptyValue(feeValue)) {
      issues.push(buildIssue(product, feeFieldKey, 'missing', `平台抽成缺失（${platform}）`, platform));
    } else if (feeFieldKey && !isEmptyValue(feeValue) && !isPercentageValue(feeValue)) {
      issues.push(
        buildIssue(product, feeFieldKey, 'invalid_number', `平台抽成格式錯誤（${platform}）`, platform),
      );
    }
  }

  return {
    isValid: issues.length === 0,
    commonMissing,
    commonInvalidNumeric,
    platformMissing,
    totalIssueCount: issues.length,
    issues,
  };
}

export function validateProduct(
  product: ProductRow,
  priceFieldLookup: Record<string, PriceFieldMeta> = {},
  selectedPlatforms: string[] = [],
): ValidationResult {
  const sheetName = product.source.sheetName.trim().toLowerCase();
  if (sheetName === 'price_tag') return validatePriceTag(product, priceFieldLookup, selectedPlatforms);
  return validateMain(product);
}

