export type AdditionalInfoField = {
  key: string;
  label: string;
  placeholder?: string;
  recommended?: boolean;
  inputType?: 'text' | 'textarea' | 'select';
  options?: string[];
};

export const ADDITIONAL_INFO_FIELDS: AdditionalInfoField[] = [
  {
    key: 'bsmi',
    label: 'BSMI 許可字號',
    placeholder: '請輸入 BSMI 許可字號',
    recommended: true,
    inputType: 'text',
  },
  {
    key: 'ncc',
    label: 'NCC 許可字號',
    placeholder: '請輸入 NCC 許可字號',
    inputType: 'text',
  },
  {
    key: 'inspection',
    label: '商檢字號',
    placeholder: '請輸入商檢字號',
    inputType: 'text',
  },
  {
    key: 'energyReport',
    label: '能源效率分級檢驗報告書',
    placeholder: '請輸入能源效率分級檢驗報告書',
    inputType: 'text',
  },
  {
    key: 'waterLabel',
    label: '省水標章',
    placeholder: '請輸入省水標章',
    inputType: 'text',
  },
];
