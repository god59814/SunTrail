import { useMemo, type ReactNode } from 'react';
import { FIELD_LABELS } from '../config/fieldConfig';
import type { ParsedRecord, PriceFieldMeta } from '../utils/fileParsers';

type Props = {
  records: ParsedRecord[];
  priceFieldLookup?: Record<string, PriceFieldMeta>;
  selectedPlatforms?: string[];
  onChangeRecords?: (records: ParsedRecord[]) => void;
  hint?: string;
};

type EditableRecord = ParsedRecord;

function toDisplayValue(value: unknown) {
  return String(value ?? '');
}

const PRIORITY_COLUMNS = ['erp_product_name', 'erp_sku', 'list_price', 'all_price'];
const HIDDEN_COLUMNS = ['__sheetName', '__excelRow', 'platform'];

function parseNumeric(value: unknown): number | null {
  const text = String(value ?? '').trim().replaceAll(',', '');
  if (!text) return null;
  const asNumber = Number(text);
  if (!Number.isFinite(asNumber)) return null;
  return asNumber;
}

function parseFeeRatio(value: unknown): number | null {
  const text = String(value ?? '').trim().replace('％', '%');
  if (!text) return null;

  if (text.endsWith('%')) {
    const percent = parseNumeric(text.slice(0, -1));
    if (percent === null) return null;
    return percent / 100;
  }

  const number = parseNumeric(text);
  if (number === null) return null;
  return number > 1 ? number / 100 : number;
}

function formatNumber(value: number): string {
  return value.toFixed(4).replace(/\.?0+$/, '');
}

function applyCostFormula(
  row: EditableRecord,
  priceFieldLookup: Record<string, PriceFieldMeta> = {},
  selectedPlatforms: string[] = [],
): EditableRecord {
  const nextRow = { ...row };
  const allPrice = parseNumeric(nextRow.all_price);
  const selectedSet = new Set(selectedPlatforms.map((p) => p.trim().toLowerCase()));

  const grouped = new Map<string, Partial<Record<'price' | 'fee' | 'cost', string>>>();
  Object.entries(priceFieldLookup).forEach(([fieldKey, meta]) => {
    if (selectedSet.size > 0 && !selectedSet.has(meta.platform.toLowerCase())) return;
    if (!grouped.has(meta.platform)) grouped.set(meta.platform, {});
    grouped.get(meta.platform)![meta.kind] = fieldKey;
  });

  for (const [, fields] of grouped.entries()) {
    const priceField = fields.price;
    const feeField = fields.fee;
    const costField = fields.cost;
    if (!priceField || !feeField || !costField) continue;

    const platformPrice = parseNumeric(nextRow[priceField]);
    const effectivePrice = platformPrice ?? allPrice;
    const feeRatio = parseFeeRatio(nextRow[feeField]);

    if (effectivePrice === null || feeRatio === null) {
      nextRow[costField] = '';
      continue;
    }

    nextRow[costField] = formatNumber(effectivePrice * (1 - feeRatio));
  }

  return nextRow;
}

function isEditableColumn(key: string, priceFieldLookup: Record<string, PriceFieldMeta> = {}) {
  if (key === 'erp_product_name' || key === 'erp_sku') return false;
  if (HIDDEN_COLUMNS.includes(key)) return false;
  const meta = priceFieldLookup[key];
  if (meta?.kind === 'cost') return false;
  return key === 'list_price' || key === 'all_price' || meta?.kind === 'price' || meta?.kind === 'fee';
}

export default function PriceTagEditor({
  records,
  priceFieldLookup,
  selectedPlatforms = [],
  onChangeRecords,
  hint,
}: Props) {
  const rows = useMemo(
    () => records.map((row) => applyCostFormula(row, priceFieldLookup, selectedPlatforms)),
    [records, priceFieldLookup, selectedPlatforms],
  );

  const columns = useMemo(() => {
    if (!rows.length) return [];
    const selectedSet = new Set(selectedPlatforms.map((p) => p.trim().toLowerCase()));

    const keySet = new Set<string>();
    rows.forEach((row) => {
      Object.keys(row).forEach((key) => {
        if (HIDDEN_COLUMNS.includes(key)) return;
        const meta = priceFieldLookup?.[key];
        if (meta && selectedSet.size > 0 && !selectedSet.has(meta.platform.toLowerCase())) return;
        keySet.add(key);
      });
    });

    const allKeys = Array.from(keySet);
    const priority = PRIORITY_COLUMNS.filter((key) => allKeys.includes(key));
    const others = allKeys.filter((key) => !PRIORITY_COLUMNS.includes(key));
    return [...priority, ...others];
  }, [rows, priceFieldLookup, selectedPlatforms]);

  const columnLabels = useMemo(() => {
    const labels: Record<string, ReactNode> = {};
    columns.forEach((key) => {
      const fromConfig = FIELD_LABELS[key];
      if (fromConfig) {
        labels[key] = fromConfig;
        return;
      }

      const meta = priceFieldLookup?.[key];
      if (meta) {
        const kindLabel =
          meta.kind === 'price' ? '價格售價' : meta.kind === 'fee' ? '平台抽成' : '平台進價';
        labels[key] = (
          <span className="priceTagHeaderTwoLines">
            <span>{meta.platform}</span>
            <span>{kindLabel}</span>
          </span>
        );
        return;
      }

      labels[key] = key;
    });
    return labels;
  }, [columns, priceFieldLookup]);

  const updateCell = (rowIndex: number, key: string, value: string) => {
    const nextRows = rows.map((row, index) =>
      index === rowIndex
        ? {
            ...row,
            [key]: value,
          }
        : row,
    );
    nextRows[rowIndex] = applyCostFormula(nextRows[rowIndex], priceFieldLookup, selectedPlatforms);
    onChangeRecords?.(nextRows);
  };

  if (!rows.length) {
    return <div className="emptyPanel">找不到 price_tag 資料</div>;
  }

  const getColumnClassName = (key: string) => {
    const classes = [`priceTagCol-${key}`];
    const meta = priceFieldLookup?.[key];
    if (meta?.kind) {
      classes.push(`priceTagKind-${meta.kind}`);
    }
    return classes.join(' ');
  };

  return (
    <div className="priceTagEditor">
      {hint ? <div className="priceTagHint">{hint}</div> : null}

      <div className="priceTagTableWrap">
        <table className="priceTagTable">
          <thead>
            <tr>
              {columns.map((key) => (
                <th key={key} className={getColumnClassName(key)}>
                  {columnLabels[key] ?? key}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={`${row.__sheetName ?? 'price_tag'}-${row.__excelRow ?? rowIndex}`}>
                {columns.map((key) => (
                  <td key={key} className={getColumnClassName(key)}>
                    {isEditableColumn(key, priceFieldLookup) ? (
                      <input
                        value={toDisplayValue(row[key])}
                        onChange={(e) => updateCell(rowIndex, key, e.target.value)}
                      />
                    ) : (
                      <span>{toDisplayValue(row[key])}</span>
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
