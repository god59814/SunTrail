import { useMemo, useState } from 'react';
import './App.css';
import Header from './components/Header';
import ProductGrid from './components/ProductGrid';
import ValidationPanel from './components/ValidationPanel';
import { mockProducts } from './data/mockProducts';
import {
  validateProduct,
  type ProductRow,
} from './utils/validateProduct';

export default function App() {
  const [products, setProducts] = useState<ProductRow[]>(mockProducts);
  const [selectedId, setSelectedId] = useState<string>(mockProducts[0]?.id ?? '');
  const [lastValidatedAt, setLastValidatedAt] = useState<number | null>(null);

  const selectedProduct = useMemo(() => {
    return products.find((p) => p.id === selectedId) ?? null;
  }, [products, selectedId]);

  const validation = useMemo(() => {
    if (!selectedProduct) return null;
    return validateProduct(selectedProduct);
  }, [selectedProduct]);

  const updateCommonField = (productId: string, field: string, value: string) => {
    setProducts((prev) =>
      prev.map((p) => (p.id === productId ? { ...p, common: { ...p.common, [field]: value } } : p)),
    );
  };

  const updateTargets = (productId: string, targets: string[]) => {
    setProducts((prev) =>
      prev.map((p) => (p.id === productId ? { ...p, targets: targets as ProductRow['targets'] } : p)),
    );
  };

  return (
    <div className="appShell">
      <Header
        onLoad={function () {
          setProducts(mockProducts);
          setSelectedId(mockProducts[0]?.id ?? '');
          setLastValidatedAt(null);
        }}
        onSave={function () {
          // MVP：先不串出 API / CSV
          // 這裡用 console 讓你可確認目前狀態
          // eslint-disable-next-line no-console
          console.log('MVP save (no-op):', products);
        }}
        onValidate={function () {
          setLastValidatedAt(Date.now());
        }}
        onPlatformSettings={function () {
          alert('MVP：平台設定尚未實作（下一步再做）。');
        }}
      />

      <div className="content">
        <div className="gridPane">
          <ProductGrid
            products={products}
            selectedId={selectedId}
            onSelectProduct={setSelectedId}
            onUpdateCommonField={updateCommonField}
            onUpdateTargets={updateTargets}
          />
        </div>

        <aside className="sidePane">
          {selectedProduct && validation ? (
            <ValidationPanel
              product={selectedProduct}
              validation={validation}
              lastValidatedAt={lastValidatedAt}
            />
          ) : (
            <div className="emptyPanel">尚未選擇商品</div>
          )}
        </aside>
      </div>
    </div>
  );
}
