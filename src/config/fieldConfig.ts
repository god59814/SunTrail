export const PLATFORM_OPTIONS = [
  'momo',
  'friday',
  '東森',
  'pchome',
  '蝦皮',
  'showmore',
  'MO+',
  '誠品',
] as const;

export type PlatformOption = (typeof PLATFORM_OPTIONS)[number];

export const MVP_GRID_FIELDS = [
  'targets',
  'erp_product_name',
  'sale_product_name',
  'slogan',
  'feature',
  'product_description',
  'erp_sku',
  'barcode',
  'brand',
  'spec_name_1',
  'spec_value_1',
  'spec_name_2',
  'spec_value_2',
  'length_cm',
  'width_cm',
  'height_cm',
  'weight_kg',
  'sale_price',
  'cost_price',
  'stock_qty',
  'delivery_type',
  'temperature_type',
] as const;

export type MvpGridField = (typeof MVP_GRID_FIELDS)[number];

export const COMMON_REQUIRED_FIELDS = [
  'erp_product_name',
  'sale_product_name',
  'erp_sku',
  'brand',
  'sale_price',
  'stock_qty',
] as const;

export type CommonRequiredField = (typeof COMMON_REQUIRED_FIELDS)[number];

export const PLATFORM_REQUIRED_FIELDS: Record<string, string[]> = {
  momo: ['model_no'],
  friday: [],
  東森: [],
  pchome: [],
  蝦皮: [],
  showmore: [],
  'MO+': [],
  誠品: ['supplier_id', 'buyer_code'],
};

export const FIELD_LABELS: Record<string, string> = {
  targets: '上架平台',
  erp_product_name: 'ERP品名',
  sale_product_name: '銷售品名',
  slogan: '商品特色標語',
  feature: '商品特色',
  product_description: '商品文案',
  erp_sku: 'ERP品號',
  barcode: '國際條碼',
  model_no: '商品型號',
  brand: '品牌',
  spec_name_1: '規格名稱1',
  spec_value_1: '規格內容1',
  spec_name_2: '規格名稱2',
  spec_value_2: '規格內容2',
  length_cm: '長(cm)',
  width_cm: '寬(cm)',
  height_cm: '高(cm)',
  weight_kg: '重量(KG)',
  sale_price: '售價',
  cost_price: '成本價',
  stock_qty: '庫存',
  delivery_type: '配送方式',
  temperature_type: '商品溫層',
  supplier_id: '供應商ID',
  buyer_code: '採購人員代碼',
};

