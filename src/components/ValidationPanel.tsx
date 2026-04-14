import type { ValidationIssue } from '../utils/validateProduct';

type Props = {
  issues: ValidationIssue[];
  lastValidatedAt: number | null;
};

function formatTs(ts: number) {
  const d = new Date(ts);
  return `${d.getFullYear()}/${String(d.getMonth() + 1).padStart(2, '0')}/${String(d.getDate()).padStart(
    2,
    '0',
  )} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

export default function ValidationPanel({ issues, lastValidatedAt }: Props) {
  const missingCount = issues.filter((i) => i.issueType === 'missing').length;
  const invalidCount = issues.filter((i) => i.issueType === 'invalid_number').length;

  return (
    <div className="panel">
      <div className="panelHeader">
        <div className="panelTitle">缺漏與錯誤清單</div>
        <p className="panelSub">
          {issues.length === 0 ? (
            <span className="panelOk">全部完成</span>
          ) : (
            `共 ${issues.length} 個問題（缺漏 ${missingCount} / 格式錯誤 ${invalidCount}）`
          )}
        </p>
        {lastValidatedAt ? <div className="panelHint">最後驗證：{formatTs(lastValidatedAt)}</div> : null}
      </div>

      <div className="panelSection">
        <div className="panelLabel">顯示內容</div>
        <div className="panelValue">資料表 / 列號 / 欄位 / 問題類型 / 說明</div>
      </div>

      <div className="panelSection">
        <div className="panelLabel">規則</div>
        <ul className="panelList">
          <li>表格僅顯示缺漏或錯誤資料</li>
          <li>不提供網頁端直接修改</li>
          <li>請回原始 Excel 修正後重新上傳</li>
        </ul>
      </div>
    </div>
  );
}

