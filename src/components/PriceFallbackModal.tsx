import { useState } from 'react';

type PriceFallbackValues = {
  list_price: string;
  sale_price: string;
  cost_price: string;
};

type Props = {
  open: boolean;
  onCancel: () => void;
  onConfirm: (values: PriceFallbackValues) => void;
};

export default function PriceFallbackModal({ open, onCancel, onConfirm }: Props) {
  const [listPrice, setListPrice] = useState('');
  const [salePrice, setSalePrice] = useState('');
  const [costPrice, setCostPrice] = useState('');

  if (!open) return null;

  return (
    <div className="modalBackdrop">
      <div className="modalCard">
        <h3>尚未上傳 EBO</h3>
        <p>請先填入建議售價、售價與進價，系統會自動補到目前缺值的商品。</p>

        <div className="formBlock">
          <label htmlFor="fallback-list-price">建議售價</label>
          <input
            id="fallback-list-price"
            value={listPrice}
            onChange={(e) => setListPrice(e.target.value)}
            inputMode="numeric"
          />
        </div>

        <div className="formBlock">
          <label htmlFor="fallback-sale-price">售價</label>
          <input
            id="fallback-sale-price"
            value={salePrice}
            onChange={(e) => setSalePrice(e.target.value)}
            inputMode="numeric"
          />
        </div>

        <div className="formBlock">
          <label htmlFor="fallback-cost-price">進價</label>
          <input
            id="fallback-cost-price"
            value={costPrice}
            onChange={(e) => setCostPrice(e.target.value)}
            inputMode="numeric"
          />
        </div>

        <div className="modalActions">
          <button className="topBarBtn" type="button" onClick={onCancel}>
            取消
          </button>
          <button
            className="topBarBtn"
            type="button"
            onClick={() =>
              onConfirm({
                list_price: listPrice.trim(),
                sale_price: salePrice.trim(),
                cost_price: costPrice.trim(),
              })
            }
          >
            套用
          </button>
        </div>
      </div>
    </div>
  );
}
