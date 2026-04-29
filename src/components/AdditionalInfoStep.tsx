import { useMemo, useState } from 'react';
import { ADDITIONAL_INFO_FIELDS, type AdditionalInfoField } from '../config/additionalInfoFields';
import type { ProductRow } from '../utils/validateProduct';
import type { BuildGoogleSheetRowsResult } from '../server/buildGoogleSheetRows';

type Props = {
  products: ProductRow[];
  onBack: () => void;
  extraInfo: Record<string, Record<string, string>>;
  onChangeExtraInfo: (next: Record<string, Record<string, string>>) => void;
  onSubmitToGoogleSheet: () => void;
  submitLoading?: boolean;
  submitWarnings?: BuildGoogleSheetRowsResult | null;
};

type ExtraInfoMap = Record<string, Record<string, string>>;

function inferRecommendedFieldKeys(product: ProductRow) {
  const text = [
    product.common.erp_product_name,
    product.common.sale_product_name,
    product.common.brand,
    product.common.product_description,
  ]
    .map((v) => String(v ?? '').toLowerCase())
    .join(' ');

  const keys = new Set<string>();
  if (/電|家電|吹風機|冰箱|洗衣機|除濕機|電風扇/.test(text)) keys.add('bsmi');
  if (/藍牙|wifi|無線|耳機|路由器|手機|phone|通訊/.test(text)) keys.add('ncc');
  if (/食品|玩具|電器|檢驗/.test(text)) keys.add('inspection');
  if (/冰箱|冷氣|除濕機|洗衣機/.test(text)) keys.add('energyReport');
  if (/水龍頭|蓮蓬頭|馬桶|省水/.test(text)) keys.add('waterLabel');
  return keys;
}

function renderFieldControl(
  field: AdditionalInfoField,
  value: string,
  onChange: (value: string) => void,
) {
  if (field.inputType === 'textarea') {
    return (
      <textarea
        value={value}
        placeholder={field.placeholder ?? ''}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }

  if (field.inputType === 'select') {
    return (
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">請選擇</option>
        {(field.options ?? []).map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    );
  }

  return (
    <input
      value={value}
      placeholder={field.placeholder ?? ''}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

export default function AdditionalInfoStep({
  products,
  onBack,
  extraInfo,
  onChangeExtraInfo,
  onSubmitToGoogleSheet,
  submitLoading = false,
  submitWarnings,
}: Props) {
  const [selectedId, setSelectedId] = useState(products[0]?.id ?? '');
  const [enabledFieldKeys, setEnabledFieldKeys] = useState<string[]>(
    ADDITIONAL_INFO_FIELDS.map((field) => field.key),
  );

  const effectiveSelectedId = useMemo(() => {
    if (!products.length) return '';
    return products.some((p) => p.id === selectedId) ? selectedId : products[0]?.id ?? '';
  }, [products, selectedId]);

  const selectedProduct = useMemo(
    () => products.find((p) => p.id === effectiveSelectedId) ?? null,
    [products, effectiveSelectedId],
  );

  const recommendedKeys = useMemo(() => {
    return selectedProduct ? inferRecommendedFieldKeys(selectedProduct) : new Set<string>();
  }, [selectedProduct]);
  const selectedSku = String(selectedProduct?.common.erp_sku ?? '').trim() || selectedId;
  const currentValue = extraInfo[selectedSku] ?? {};
  const visibleFields = ADDITIONAL_INFO_FIELDS.filter((field) =>
    enabledFieldKeys.includes(field.key),
  );

  const updateField = (fieldKey: string, value: string) => {
    const next: ExtraInfoMap = {
      ...extraInfo,
      [selectedSku]: {
        ...(extraInfo[selectedSku] ?? {}),
        [fieldKey]: value,
      },
    };
    onChangeExtraInfo(next);
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
              className={`extraProductBtn ${product.id === effectiveSelectedId ? 'active' : ''}`}
              onClick={() => setSelectedId(product.id)}
            >
              {String(product.common.erp_product_name || product.common.sale_product_name || product.id)}
            </button>
          ))}
        </div>

        <div className="extraForm">
          {selectedProduct ? (
            <>
              <div className="extraFieldSelector">
                {ADDITIONAL_INFO_FIELDS.map((field) => {
                  const checked = enabledFieldKeys.includes(field.key);
                  return (
                    <label key={field.key} className="extraFieldOption">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={(e) => {
                          if (e.target.checked) {
                            setEnabledFieldKeys((prev) =>
                              prev.includes(field.key) ? prev : [...prev, field.key],
                            );
                          } else {
                            setEnabledFieldKeys((prev) => prev.filter((key) => key !== field.key));
                          }
                        }}
                      />
                      {field.label}
                    </label>
                  );
                })}
              </div>

              {visibleFields.map((field) => {
                const isRecommended = Boolean(field.recommended) || recommendedKeys.has(field.key);

                return (
                  <div className="formBlock" key={field.key}>
                    <label>
                      {field.label}
                      {isRecommended ? '（建議填寫）' : ''}
                    </label>

                    {renderFieldControl(field, currentValue[field.key] ?? '', (value) =>
                      updateField(field.key, value),
                    )}
                  </div>
                );
              })}

              <div className="submitBar">
                <button
                  className="topBarBtn"
                  type="button"
                  onClick={onSubmitToGoogleSheet}
                  disabled={submitLoading}
                >
                  {submitLoading ? '送出中...' : '送出到 Google Sheet'}
                </button>
              </div>

              {submitWarnings &&
              (submitWarnings.missingPriceTagSkus.length > 0 ||
                submitWarnings.missingPlatformMappings.length > 0) ? (
                <div className="submitWarningPanel">
                  {submitWarnings.missingPriceTagSkus.length > 0 ? (
                    <div>
                      缺少對應 price_tag SKU：{submitWarnings.missingPriceTagSkus.join(', ')}
                    </div>
                  ) : null}
                  {submitWarnings.missingPlatformMappings.length > 0 ? (
                    <div>
                      缺少 price_mapping 平台：{submitWarnings.missingPlatformMappings.join(', ')}
                    </div>
                  ) : null}
                </div>
              ) : null}
            </>
          ) : (
            <div className="emptyPanel">尚未選擇商品</div>
          )}
        </div>
      </div>
    </div>
  );
}
