import { useCallback, useMemo } from 'react';
import { AgGridReact } from 'ag-grid-react';
import type { ColDef, GridReadyEvent, GridSizeChangedEvent } from 'ag-grid-community';
import type { ValidationIssue } from '../utils/validateProduct';

import 'ag-grid-community/styles/ag-grid.css';
import 'ag-grid-community/styles/ag-theme-alpine.css';

type Props = {
  issues: ValidationIssue[];
};

export default function ProductGrid({ issues }: Props) {
  const fitColumns = useCallback((event: GridReadyEvent | GridSizeChangedEvent) => {
    event.api.sizeColumnsToFit();
  }, []);

  const rowData = useMemo(
    () =>
      issues.map((issue, index) => ({
        id: `${issue.productId}-${issue.columnKey}-${index}`,
        sheetName: issue.sheetName,
        excelRow: issue.excelRow,
        columnLabel: issue.columnLabel,
        columnKey: issue.columnKey,
        issueType: issue.issueType === 'missing' ? '缺漏' : '格式錯誤',
        message: issue.message,
      })),
    [issues],
  );

  const columnDefs = useMemo<ColDef[]>(
    () => [
      { headerName: '資料表', field: 'sheetName', minWidth: 88, flex: 0.8 },
      { headerName: '列號', field: 'excelRow', minWidth: 68, flex: 0.55 },
      { headerName: '欄位名稱', field: 'columnLabel', minWidth: 120, flex: 1.1 },
      { headerName: '欄位 key', field: 'columnKey', minWidth: 120, flex: 1.1 },
      { headerName: '問題類型', field: 'issueType', minWidth: 96, flex: 0.8 },
      { headerName: '說明', field: 'message', minWidth: 160, flex: 1.45 },
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
        onGridReady={fitColumns}
        onGridSizeChanged={fitColumns}
      />
    </div>
  );
}

