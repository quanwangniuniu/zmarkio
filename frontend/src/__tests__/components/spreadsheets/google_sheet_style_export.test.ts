import { buildGoogleSheetExport, type AppCellFormat } from '@/components/spreadsheets/google_sheet_style_export';

const plain = (overrides: Partial<AppCellFormat> = {}): AppCellFormat => ({
  bold: false,
  italic: false,
  strikethrough: false,
  textColor: null,
  fontFamily: null,
  fontSize: null,
  numberFormat: null,
  ...overrides,
});

describe('google sheet style export', () => {
  it('keeps raw numbers and formula text', () => {
    const { matrix } = buildGoogleSheetExport({
      cells: [
        { row: 0, column: 0, raw: 'Hello' },
        { row: 0, column: 1, raw: '0.5' },
        { row: 0, column: 2, raw: '=A2+B2' },
      ],
      formats: [],
      highlights: [],
    });
    expect(matrix).toEqual([['Hello', '0.5', '=A2+B2']]);
  });

  it('maps text style, font, size, and red', () => {
    const { formats } = buildGoogleSheetExport({
      cells: [{ row: 0, column: 0, raw: 'Name' }],
      formats: [{
        row: 0,
        column: 0,
        format: plain({
          bold: true,
          italic: true,
          strikethrough: true,
          fontFamily: 'Arial, sans-serif',
          fontSize: 14,
          textColor: '#DC2626',
        }),
      }],
      highlights: [],
    });
    expect(formats[0].userEnteredFormat.textFormat).toEqual({
      bold: true,
      italic: true,
      strikethrough: true,
      fontFamily: 'Arial',
      fontSize: 14,
      foregroundColor: { red: 220 / 255, green: 38 / 255, blue: 38 / 255 },
    });
  });

  it('maps percent, the four currencies, and leaves an unknown currency as a number', () => {
    const at = (column: number, format: AppCellFormat['numberFormat']) => ({
      row: 0,
      column,
      format: plain({ numberFormat: format }),
    });
    const { formats } = buildGoogleSheetExport({
      cells: [],
      formats: [
        at(0, { type: 'NUMBER', decimalPlaces: 0 }),
        at(1, { type: 'NUMBER', decimalPlaces: 2 }),
        at(2, { type: 'PERCENT', decimalPlaces: 0 }),
        at(3, { type: 'PERCENT', decimalPlaces: 2 }),
        at(4, { type: 'CURRENCY', currencyCode: 'USD', decimalPlaces: 2 }),
        at(5, { type: 'CURRENCY', currencyCode: 'GBP', decimalPlaces: 2 }),
        at(6, { type: 'CURRENCY', currencyCode: 'EUR', decimalPlaces: 2 }),
        at(7, { type: 'CURRENCY', currencyCode: 'JPY', decimalPlaces: 0 }),
        at(8, { type: 'CURRENCY', currencyCode: 'AUD', decimalPlaces: 2 }),
      ],
      highlights: [],
    });
    const pattern = (column: number) =>
      formats.find((item) => item.column === column)?.userEnteredFormat.numberFormat;
    expect(pattern(0)).toEqual({ type: 'NUMBER', pattern: '#,##0' });
    expect(pattern(1)).toEqual({ type: 'NUMBER', pattern: '#,##0.00' });
    expect(pattern(2)).toEqual({ type: 'PERCENT', pattern: '0%' });
    expect(pattern(3)).toEqual({ type: 'PERCENT', pattern: '0.00%' });
    expect(pattern(4)).toEqual({ type: 'CURRENCY', pattern: '[$$-409]#,##0.00' });
    expect(pattern(5)).toEqual({ type: 'CURRENCY', pattern: '[$£-809]#,##0.00' });
    expect(pattern(6)).toEqual({ type: 'CURRENCY', pattern: '[$€-407]#,##0.00' });
    expect(pattern(7)).toEqual({ type: 'CURRENCY', pattern: '[$¥-411]#,##0' });
    expect(pattern(8)).toEqual({ type: 'NUMBER', pattern: '#,##0.00' });
  });

  it('keeps a blank highlighted cell and omits a plain cell', () => {
    const { matrix, formats } = buildGoogleSheetExport({
      cells: [{ row: 0, column: 0, raw: 'Ada' }],
      formats: [],
      highlights: [{ scope: 'CELL', row: 9, column: 0, color: '#FEF08A' }],
    });
    expect(matrix[9][0]).toBe('');
    expect(formats).toEqual([{
      row: 9,
      column: 0,
      userEnteredFormat: {
        backgroundColor: { red: 254 / 255, green: 240 / 255, blue: 138 / 255 },
      },
    }]);
  });
});