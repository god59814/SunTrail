import type { ValidationIssue } from '../utils/validateProduct';

type Props = {
  issues: ValidationIssue[];
  lastValidatedAt: number | null;
  successProductCount?: number;
  onValidate: () => void;
  onSubmitToGoogleSheet: () => void;
  showSubmitToGoogleSheet: boolean;
  canSubmitToGoogleSheet: boolean;
  submitLoading?: boolean;
};

function formatTs(ts: number) {
  const d = new Date(ts);
  return `${d.getFullYear()}/${String(d.getMonth() + 1).padStart(2, '0')}/${String(d.getDate()).padStart(
    2,
    '0',
  )} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

export default function ValidationPanel({
  issues,
  lastValidatedAt,
  successProductCount = 0,
  onValidate,
  onSubmitToGoogleSheet,
  showSubmitToGoogleSheet,
  canSubmitToGoogleSheet,
  submitLoading = false,
}: Props) {
  const missingCount = issues.filter((i) => i.issueType === 'missing').length;
  const invalidCount = issues.filter((i) => i.issueType === 'invalid_number').length;
  const isAllClear = issues.length === 0;

  return (
    <div className="panel">
      <div className="panelHeader">
        <div className="panelTitle">缺漏與錯誤清單</div>
        <p className="panelSub">
          {isAllClear ? (
            <>
              <span className="panelOk">全部完成</span>
              <br />
              <span className="panelSuccessCount">{`共 ${successProductCount} 筆商品`}</span>
            </>
          ) : (
            <>
              <span>{`共 ${issues.length} 個問題`}</span>
              <br />
              <span className="panelSubDetail">{`（缺漏${missingCount}/格式錯誤${invalidCount}）`}</span>
            </>
          )}
        </p>
        <div className="panelActions">
          <button className="topBarBtn panelActionBtn panelActionValidate" type="button" onClick={onValidate}>
            驗證
          </button>
          {showSubmitToGoogleSheet ? (
            <button
              className="topBarBtn panelActionBtn panelActionSubmit"
              type="button"
              onClick={onSubmitToGoogleSheet}
              disabled={!canSubmitToGoogleSheet || submitLoading}
            >
              {submitLoading ? '上傳中...' : '上傳到 Google Sheet'}
            </button>
          ) : null}
        </div>
        {lastValidatedAt ? <div className="panelHint">最後驗證：{formatTs(lastValidatedAt)}</div> : null}
      </div>

      {!isAllClear ? (
        <div className="panelErrorGuide">
          請依左側表格列出的缺漏與錯誤，回到原始 xlsm 檔案修正後再重新上傳並驗證。
        </div>
      ) : null}
    </div>
  );
}

