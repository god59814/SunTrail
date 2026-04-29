import { parseAnyTabularFile, parsePriceMapping, type ParsedRecord } from '../utils/fileParsers';

export type MainRow = ParsedRecord & {
  __sheetName: 'main';
  erp_sku: string;
};

export type PriceTagRow = ParsedRecord & {
  __sheetName: 'price_tag';
  erp_sku: string;
};

export type PriceMappingEntry = {
  platform: string;
  priceField: string;
  feeField: string;
  costField: string;
};

export type PriceMapping = Record<string, PriceMappingEntry>;

export type ParsedWorkbookData = {
  mainRows: MainRow[];
  priceTagRows: PriceTagRow[];
  priceMapping: PriceMapping;
  selectedPlatforms: string[];
};

function asText(value: unknown) {
  return String(value ?? '').trim();
}

export async function parseWorkbook(file: File): Promise<ParsedWorkbookData> {
  const [tabularData, fieldLookup] = await Promise.all([
    parseAnyTabularFile(file),
    parsePriceMapping(file),
  ]);

  const mainRows = tabularData.records
    .filter((row) => row.__sheetName.toLowerCase() === 'main')
    .map((row) => ({
      ...row,
      __sheetName: 'main' as const,
      erp_sku: asText(row.erp_sku),
    }));

  const priceTagRows = tabularData.records
    .filter((row) => row.__sheetName.toLowerCase() === 'price_tag')
    .map((row) => ({
      ...row,
      __sheetName: 'price_tag' as const,
      erp_sku: asText(row.erp_sku),
    }));

  const priceMapping: PriceMapping = {};
  Object.entries(fieldLookup).forEach(([fieldKey, meta]) => {
    const current = priceMapping[meta.platform] ?? {
      platform: meta.platform,
      priceField: '',
      feeField: '',
      costField: '',
    };
    if (meta.kind === 'price') current.priceField = fieldKey;
    if (meta.kind === 'fee') current.feeField = fieldKey;
    if (meta.kind === 'cost') current.costField = fieldKey;
    priceMapping[meta.platform] = current;
  });

  return {
    mainRows,
    priceTagRows,
    priceMapping,
    selectedPlatforms: tabularData.selectedPlatforms,
  };
}
