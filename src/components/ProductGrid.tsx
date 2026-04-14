import { useMemo } from 'react';
import { AgGridReact } from 'ag-grid-react';
import type { ColDef } from 'ag-grid-community';
import type { ValidationIssue } from '../utils/validateProduct';

import 'ag-grid-community/styles/ag-grid.css';
import 'ag-grid-community/styles/ag-theme-alpine.css';

type Props = {
  issues: ValidationIssue[];
};

export default function ProductGrid({ issues }: Props) {
  const rowData = useMemo(
    () =>
      issues.map((issue, index) => ({
        id: `${issue.productId}-${issue.columnKey}-${index}`,
        sheetName: issue.sheetName,
        excelRow: issue.excelRow,
        columnLabel: issue.columnLabel,
        columnKey: issue.columnKey,
        issueType: issue.issueType === 'missing' ? '缺漏' : '格式錯誤',
        platform: issue.platform ?? '',
        message: issue.message,
      })),
    [issues],
  );

  const columnDefs = useMemo<ColDef[]>(
    () => [
      { headerName: '資料表', field: 'sheetName', minWidth: 140, flex: 1 },
      { headerName: '列號', field: 'excelRow', minWidth: 90, width: 90 },
      { headerName: '欄位名稱', field: 'columnLabel', minWidth: 180, flex: 1.2 },
      { headerName: '欄位 key', field: 'columnKey', minWidth: 180, flex: 1.2 },
      { headerName: '平台', field: 'platform', minWidth: 120, flex: 0.8 },
      { headerName: '問題類型', field: 'issueType', minWidth: 120, width: 120 },
      { headerName: '說明', field: 'message', minWidth: 240, flex: 1.5 },
    ],
    [],
  );

  return (
    <div className="gridWrap ag-theme-alpine">
      <AgGridReact
        rowData={rowData}
        columnDefs={columnDefs}
        defaultColDef={{
          sortable: true,
          resizable: true,
          filter: true,
          editable: false,
        }}
        getRowId={(params) => params.data.id}
        rowHeight={44}
        suppressClickEdit={true}
      />
    </div>
  );
}

