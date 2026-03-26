import { COMMON_REQUIRED_FIELDS, PLATFORM_REQUIRED_FIELDS, type PlatformOption } from '../config/fieldConfig';

export type PrimitiveValue = string | number | boolean | null;

/**
 * MVP 先用寬鬆 common 結構：
 * - 先支撐「表格編輯 + 驗證缺欄位」
 * - 之後再依欄位型別（number/boolean/date）逐步收斂
 */
export type ProductCommon = Record<string, PrimitiveValue>;

export type ProductRow = {
  id: string;
  targets: PlatformOption[];
  common: ProductCommon;
  platformData: Record<string, Record<string, PrimitiveValue> | undefined>;
};

export type ValidationResult = {
  isValid: boolean;
  commonMissing: string[];
  platformMissing: Partial<Record<PlatformOption, string[]>>;
  totalMissingCount: number;
};

function isEmptyValue(value: unknown) {
  if (value === null || value === undefined) return true;
  if (typeof value === 'string') return value.trim().length === 0;
  return false;
}

export function validateProduct(product: ProductRow): ValidationResult {
  const commonMissing = COMMON_REQUIRED_FIELDS.filter((field) => isEmptyValue(product.common[field]));

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

  const totalMissingCount =
    commonMissing.length + Object.values(platformMissing).reduce((sum, arr) => sum + (arr?.length ?? 0), 0);

  return {
    isValid: totalMissingCount === 0,
    commonMissing: [...commonMissing],
    platformMissing,
    totalMissingCount,
  };
}

