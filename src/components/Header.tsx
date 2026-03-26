type HeaderProps = {
  onLoad: () => void;
  onSave: () => void;
  onValidate: () => void;
  onPlatformSettings: () => void;
};

export default function Header({ onLoad, onSave, onValidate, onPlatformSettings }: HeaderProps) {
  return (
    <header className="topBar">
      <div className="topBarTitle">商品上架工作台</div>

      <div className="topBarActions">
        <button className="topBarBtn" type="button" onClick={onLoad}>
          載入資料
        </button>
        <button className="topBarBtn" type="button" onClick={onSave}>
          儲存
        </button>
        <button className="topBarBtn" type="button" onClick={onValidate}>
          驗證
        </button>
        <button className="topBarBtn" type="button" onClick={onPlatformSettings}>
          平台設定
        </button>
      </div>
    </header>
  );
}

