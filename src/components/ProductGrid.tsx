import { useEffect, useMemo, useRef } from 'react';
import { AgGridReact } from 'ag-grid-react';
import type { CellValueChangedEvent, ColDef, GridApi, GridReadyEvent, SelectionChangedEvent } from 'ag-grid-community';
import { FIELD_LABELS, MVP_GRID_FIELDS, type MvpGridField } from '../config/fieldConfig';
import type { ProductRow } from '../utils/validateProduct';
import TargetsCellRenderer from './TargetsCellRenderer';

import 'ag-grid-community/styles/ag-grid.css';
import 'ag-grid-community/styles/ag-theme-alpine.css';

type Props = {
  products: ProductRow[];
  selectedId: string;
  onSelectProduct: (id: string) => void;
  onUpdateCommonField: (productId: string, field: string, value: string) => void;
  onUpdateTargets: (productId: string, targets: string[]) => void;
};

type GridRow = { id: string; targets: string[] } & Record<string, unknown>;

export default function ProductGrid({
  products,
  selectedId,
  onSelectProduct,
  onUpdateCommonField,
  onUpdateTargets,
}: Props) {
  const gridApiRef = useRef<GridApi | null>(null);

  const rowData = useMemo<GridRow[]>(() => {
    return products.map((p) => {
      const base: GridRow = {
        id: p.id,
        targets: p.targets,
      };

      for (const f of MVP_GRID_FIELDS) {
        if (f === 'targets') continue;
        base[f] = p.common?.[f];
      }

      return base;
    });
  }, [products]);

  const columnDefs = useMemo<ColDef<GridRow>[]>(() => {
    return MVP_GRID_FIELDS.map((f: MvpGridField) => {
      const headerName = FIELD_LABELS[f] ?? f;
      const isTargets = f === 'targets';
      return {
        headerName,
        field: f,
        editable: !isTargets,
        cellRenderer: isTargets ? TargetsCellRenderer : undefined,
        valueFormatter: isTargets
          ? (params) => (Array.isArray(params.value) ? params.value.join(',') : '')
          : undefined,
        suppressFillHandle: isTargets,
        flex: isTargets ? 0.8 : 1,
        minWidth: isTargets ? 150 : 160,
      } satisfies ColDef<GridRow>;
    });
  }, []);

  const defaultColDef = useMemo<ColDef<GridRow>>(
    () => ({
      sortable: true,
      resizable: true,
      filter: true,
      editable: true,
      wrapText: true,
      autoHeight: true,
    }),
    [],
  );

  const onGridReady = (event: GridReadyEvent<GridRow>) => {
    gridApiRef.current = event.api;
  };

  // 當外部改變 selectedId 時，確保網格選取狀態同步
  useEffect(() => {
    const api = gridApiRef.current;
    if (!api) return;

    api.forEachNode((node) => {
      if (node.data?.id === selectedId) node.setSelected(true);
      else node.setSelected(false);
    });
  }, [selectedId]);

  const onSelectionChanged = (event: SelectionChangedEvent<GridRow, unknown>) => {
    const node = event.api.getSelectedNodes()[0];
    const id = node?.data?.id;
    if (id) onSelectProduct(id);
  };

  const onCellValueChanged = (event: CellValueChangedEvent<GridRow>) => {
    const field = event.colDef.field;
    if (!field) return;
    if (field === 'targets') return;

    const productId = event.data.id;
    const nextValue = String(event.newValue ?? '');
    onUpdateCommonField(productId, field, nextValue);
  };

  const gridContext = useMemo(() => {
    return {
      updateRowTargets: (rowId: string, targets: string[]) => onUpdateTargets(rowId, targets),
    };
  }, [onUpdateTargets]);

  return (
    <div className="gridWrap ag-theme-alpine">
      <AgGridReact<GridRow>
        rowData={rowData}
        columnDefs={columnDefs}
        defaultColDef={defaultColDef}
        context={gridContext}
        getRowId={(params) => params.data.id}
        // 為了讓 cell selection / fill handle 正常顯示，避免點擊被 row selection 吃掉
        rowSelection={{ mode: 'singleRow', enableClickSelection: false }}
        cellSelection={{ handle: { mode: 'fill' } }}
        theme="legacy"
        onGridReady={onGridReady}
        onSelectionChanged={onSelectionChanged}
        onCellClicked={(event) => {
          const id = event.data?.id;
          if (id) onSelectProduct(id);
        }}
        onCellValueChanged={onCellValueChanged}
        tooltipShowDelay={400}
      />
    </div>
  );
}

