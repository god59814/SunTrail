type HeaderProps = {
  onDownloadTemplate: () => void;
  onUploadFile: () => void;
  onValidate: () => void;
  onNext: () => void;
  uploadedFileName?: string;
  canGoNext: boolean;
};

export default function Header({
  onDownloadTemplate,
  onUploadFile,
  onValidate,
  onNext,
  uploadedFileName,
  canGoNext,
}: HeaderProps) {
  return (
    <header className="topBar">
      <div className="topBarTitle">商品上架工作台</div>

      <div className="topBarActions">
        {uploadedFileName ? <span className="uploadStatus">已上傳：{uploadedFileName}</span> : null}

        <button className="topBarBtn" type="button" onClick={onDownloadTemplate}>
          下載模板
        </button>
        <button className="topBarBtn" type="button" onClick={onUploadFile}>
          上傳檔案
        </button>
        <button className="topBarBtn" type="button" onClick={onValidate}>
          驗證
        </button>
        <button className="topBarBtn" type="button" onClick={onNext} disabled={!canGoNext}>
          下一步
        </button>
      </div>
    </header>
  );
}
