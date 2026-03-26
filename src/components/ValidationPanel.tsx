import { useMemo } from 'react';
import { FIELD_LABELS } from '../config/fieldConfig';
import type { ProductRow, ValidationResult } from '../utils/validateProduct';

type Props = {
  product: ProductRow;
  validation: ValidationResult;
  lastValidatedAt: number | null;
};

function formatTs(ts: number) {
  const d = new Date(ts);
  return `${d.getFullYear()}/${String(d.getMonth() + 1).padStart(2, '0')}/${String(d.getDate()).padStart(
    2,
    '0',
  )} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

export default function ValidationPanel({ product, validation, lastValidatedAt }: Props) {
  const platformMissingTotal = useMemo(() => {
    return Object.values(validation.platformMissing).reduce((sum, arr) => sum + arr.length, 0);
  }, [validation.platformMissing]);

  return (
    <div className="panel">
      <div className="panelHeader">
        <div className="panelTitle">缺欄位檢查</div>
        <p className="panelSub">
          {validation.isValid ? (
            <span className="panelOk">全部完成</span>
          ) : (
            `尚缺 ${validation.totalMissingCount} 個必填欄位（共用 ${validation.commonMissing.length} / 平台 ${platformMissingTotal}）`
          )}
        </p>
        {lastValidatedAt ? <div className="panelHint">最後驗證：{formatTs(lastValidatedAt)}</div> : null}
      </div>

      <div className="panelSection">
        <div className="panelLine">
          <span className="panelLabel">目前選到哪一筆商品</span>
          <span className="panelValue">{product.id}</span>
        </div>
      </div>

      <div className="panelSection">
        <div className="panelLabel">這筆商品選了哪些平台</div>
        <div className="panelValue" style={{ marginBottom: 8 }}>
          {product.targets.length ? (
            product.targets.map((t) => <span key={t} className="badge">{t}</span>)
          ) : (
            <span className="panelHint">尚未選平台</span>
          )}
        </div>
      </div>

      <div className="panelSection">
        <div className="panelLabel">哪些欄位是共用缺失</div>
        {validation.commonMissing.length ? (
          <ul className="panelList">
            {validation.commonMissing.map((f) => (
              <li key={f}>
                {FIELD_LABELS[f] ? `${FIELD_LABELS[f]}（${f}）` : f}
              </li>
            ))}
          </ul>
        ) : (
          <div className="panelOk">無</div>
        )}
      </div>

      <div className="panelSection">
        <div className="panelLabel">哪些欄位是平台缺失</div>
        {product.targets.length ? (
          <div>
            {product.targets.map((target) => {
              const missing = validation.platformMissing[target] ?? [];
              return (
                <div key={target} className="panelLine">
                  <span className="panelValue">{target}</span>
                  <span className="panelValue">
                    {missing.length
                      ? missing
                          .map((k) => (FIELD_LABELS[k] ? `${FIELD_LABELS[k]}（${k}）` : k))
                          .join(', ')
                      : '無'}
                  </span>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="panelHint">尚未選平台</div>
        )}
      </div>
    </div>
  );
}

