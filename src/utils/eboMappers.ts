type EboRow = {
  item_no: string;
  item_name: string;
  category: string;
  volume: string;
  invoice_cost: string;
  all_platform_rate: string;
  momo_price: string;
  momo_rate: string;
  pchome_price: string;
  pchome_rate: string;
  shopee_price: string;
  shopee_rate: string;
};

function asString(v: unknown) {
  return String(v ?? '').trim();
}

export function rowsToEboMap(rows: Record<string, unknown>[]) {
  const map = new Map<string, EboRow>();

  rows.forEach((row) => {
    const itemNo = asString(row['品號'] ?? row.item_no ?? row['ERP品號'] ?? row.erp_sku);
    if (!itemNo) return;

    map.set(itemNo, {
      item_no: itemNo,
      item_name: asString(row['品名']),
      category: asString(row['大類']),
      volume: asString(row['材積(A*B*C 單位公分)'] ?? row['材積']),
      invoice_cost: asString(row['進貨發票價(含稅)']),
      all_platform_rate: asString(row['全平台']),
      momo_price: asString(row['Momo購物']),
      momo_rate: asString(row['Momo購物抽成%']),
      pchome_price: asString(row.PCHOME),
      pchome_rate: asString(row['PCHOME抽成%']),
      shopee_price: asString(row['蝦皮直送']),
      shopee_rate: asString(row['蝦皮直送抽成%']),
    });
  });

  return map;
}
