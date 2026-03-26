export const COMMON_REQUIRED_FIELDS = [
  'erp_product_name',
  'sale_product_name',
  'erp_sku',
  'barcode',
  'brand',
  'feature',
  'product_description',
] as const;

export const PLATFORM_REQUIRED_FIELDS = {
  momo: ['category_code'],
  showmore: ['main_image_url', 'sale_price'],
  friday: ['friday_category', 'friday_price'],
} as const;

export type PlatformTarget = keyof typeof PLATFORM_REQUIRED_FIELDS;

export const PLATFORM_TARGETS = Object.keys(
  PLATFORM_REQUIRED_FIELDS,
) as Array<PlatformTarget>;

export type PlatformRequiredField<T extends PlatformTarget> =
  (typeof PLATFORM_REQUIRED_FIELDS)[T][number];

