import { useMemo, useRef, useState } from 'react';
import './App.css';
import Header from './components/Header.tsx';
import ProductGrid from './components/ProductGrid.tsx';
import ValidationPanel from './components/ValidationPanel.tsx';
import AdditionalInfoStep from './components/AdditionalInfoStep';
import { mockProducts } from './data/mockProducts';
import { PLATFORM_OPTIONS } from './config/fieldConfig';
import { parseAnyTabularFile, parsePlatformOptions } from './utils/fileParsers';
import { rowsToProducts } from './utils/productMappers';
import { validateProduct, type ProductRow } from './utils/validateProduct';

export default function App() {
  const [products, setProducts] = useState<ProductRow[]>(mockProducts);
  const [lastValidatedAt, setLastValidatedAt] = useState<number | null>(null);
  const [uploadedFileName, setUploadedFileName] = useState<string>('');
  const [currentStep, setCurrentStep] = useState<'grid' | 'extra'>('grid');
  const [, setValidatedProducts] = useState<ProductRow[]>(mockProducts);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const allValidationResults = useMemo(() => {
    return products.map((product) => ({
      product,
      result: validateProduct(product),
    }));
  }, [products]);

  const allIssues = useMemo(() => {
    return allValidationResults.flatMap(({ result }) => result.issues);
  }, [allValidationResults]);

  const allProductsValid = allIssues.length === 0;

  const handleDownloadTemplate = async () => {
    const templatePath = '/template.xlsm';
    const templateExists = await fetch(templatePath, { method: 'HEAD' }).then((res) => res.ok);
    if (!templateExists) {
      alert('找不到模板 template.xlsm，請先把檔案放到 public/template.xlsm');
      return;
    }

    const link = document.createElement('a');
    link.href = templatePath;
    link.download = 'template.xlsm';
    document.body.appendChild(link);
    link.click();
    link.remove();
  };

  const handleUploadMainFile = async (file: File) => {
    const [rows, parsedPlatformOptions] = await Promise.all([
      parseAnyTabularFile(file),
      parsePlatformOptions(file),
    ]);

    const nextPlatformOptions = parsedPlatformOptions.length
      ? parsedPlatformOptions
      : [...PLATFORM_OPTIONS];

    const parsedProducts = rowsToProducts(rows, nextPlatformOptions);
    if (!parsedProducts.length) return;

    setProducts(parsedProducts);
    setValidatedProducts(parsedProducts);
    setLastValidatedAt(null);
    setUploadedFileName(file.name);
    setCurrentStep('grid');
  };

  const handleValidate = () => {
    setValidatedProducts(products);
    setLastValidatedAt(Date.now());
  };

  const handleNext = () => {
    if (!allProductsValid) {
      alert('請先完成驗證並修正缺失欄位，再進入下一步');
      return;
    }
    setCurrentStep('extra');
  };

  return (
    <div className="appShell">
      <Header
        onDownloadTemplate={handleDownloadTemplate}
        onUploadFile={() => fileInputRef.current?.click()}
        onValidate={handleValidate}
        onNext={handleNext}
        uploadedFileName={uploadedFileName}
        canGoNext={allProductsValid}
      />
      <input
        ref={fileInputRef}
        type="file"
        accept=".csv,.xlsx,.xls,.xlsm,text/csv,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        className="hiddenInput"
        onChange={async (event) => {
          const input = event.currentTarget;
          const file = input.files?.[0];
          if (file) await handleUploadMainFile(file);
          input.value = '';
        }}
      />

      {currentStep === 'grid' ? (
        <div className="content">
          <div className="gridPane">
            {allIssues.length === 0 ? (
              <div className="emptyPanel">目前沒有缺漏或格式錯誤</div>
            ) : (
              <ProductGrid issues={allIssues} />
            )}
          </div>

          <aside className="sidePane">
            <ValidationPanel issues={allIssues} lastValidatedAt={lastValidatedAt} />
          </aside>
        </div>
      ) : (
        <AdditionalInfoStep products={products} onBack={() => setCurrentStep('grid')} />
      )}
    </div>
  );
}
