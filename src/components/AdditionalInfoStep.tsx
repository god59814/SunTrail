import { useMemo, useState } from 'react';
import type { ProductRow } from '../utils/validateProduct';

type Props = {
  products: ProductRow[];
  onBack: () => void;
};

type ExtraFields = {
  bsmi: string;
  ncc: string;
  inspection: string;
  energyReport: string;
  waterLabel: string;
};

type ExtraInfoMap = Record<string, ExtraFields>;

function inferRecommendedFields(product: ProductRow) {
  const text = [
    product.common.erp_product_name,
    product.common.sale_product_name,
    product.common.brand,
    product.common.product_description,
  ]
    .map((v) => String(v ?? '').toLowerCase())
    .join(' ');

  return {
    bsmi: /電|家電|吹風機|冰箱|洗衣機|除濕機|電風扇/.test(text),
    ncc: /藍牙|wifi|無線|耳機|路由器|手機|通訊/.test(text),
    inspection: /食品|玩具|電器|檢驗/.test(text),
    energyReport: /冰箱|冷氣|除濕機|洗衣機/.test(text),
    waterLabel: /水龍頭|蓮蓬頭|馬桶|省水/.test(text),
  };
}

export default function AdditionalInfoStep({ products, onBack }: Props) {
  const [selectedId, setSelectedId] = useState(products[0]?.id ?? '');
  const [extraInfo, setExtraInfo] = useState<ExtraInfoMap>({});

  const selectedProduct = useMemo(
    () => products.find((p) => p.id === selectedId) ?? null,
    [products, selectedId],
  );

  const recommended = selectedProduct ? inferRecommendedFields(selectedProduct) : null;

  const emptyFields: ExtraFields = {
    bsmi: '',
    ncc: '',
    inspection: '',
    energyReport: '',
    waterLabel: '',
  };

  const currentValue = extraInfo[selectedId] ?? emptyFields;

  const updateField = (field: keyof ExtraFields, value: string) => {
    setExtraInfo((prev) => ({
      ...prev,
      [selectedId]: {
        ...(prev[selectedId] ?? emptyFields),
        [field]: value,
      },
    }));
  };

  return (
    <div className="extraStep">
      <div className="extraHeader">
        <h2>商品補充資訊</h2>
        <button className="topBarBtn" type="button" onClick={onBack}>
          返回上一步
        </button>
      </div>

      <div className="extraLayout">
        <div className="extraSidebar">
          {products.map((product) => (
            <button
              key={product.id}
              type="button"
              className={`extraProductBtn ${product.id === selectedId ? 'active' : ''}`}
              onClick={() => setSelectedId(product.id)}
            >
              {product.common.erp_product_name || product.id}
            </button>
          ))}
        </div>

        <div className="extraForm">
          {selectedProduct ? (
            <>
              <div className="formBlock">
                <label>BSMI 許可字號 {recommended?.bsmi ? '（建議填寫）' : ''}</label>
                <input
                  value={currentValue.bsmi}
                  onChange={(e) => updateField('bsmi', e.target.value)}
                />
              </div>

              <div className="formBlock">
                <label>NCC 許可字號 {recommended?.ncc ? '（建議填寫）' : ''}</label>
                <input
                  value={currentValue.ncc}
                  onChange={(e) => updateField('ncc', e.target.value)}
                />
              </div>

              <div className="formBlock">
                <label>商檢字號 {recommended?.inspection ? '（建議填寫）' : ''}</label>
                <input
                  value={currentValue.inspection}
                  onChange={(e) => updateField('inspection', e.target.value)}
                />
              </div>

              <div className="formBlock">
                <label>能源效率分級檢驗報告書 {recommended?.energyReport ? '（建議填寫）' : ''}</label>
                <input
                  value={currentValue.energyReport}
                  onChange={(e) => updateField('energyReport', e.target.value)}
                />
              </div>

              <div className="formBlock">
                <label>省水標章 {recommended?.waterLabel ? '（建議填寫）' : ''}</label>
                <input
                  value={currentValue.waterLabel}
                  onChange={(e) => updateField('waterLabel', e.target.value)}
                />
              </div>
            </>
          ) : (
            <div className="emptyPanel">尚未選擇商品</div>
          )}
        </div>
      </div>
    </div>
  );
}
