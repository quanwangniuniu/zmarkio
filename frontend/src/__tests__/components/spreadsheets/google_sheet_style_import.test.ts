import { mapGoogleSheetStyles } from '@/components/spreadsheets/google_sheet_style_import';

const cell = (col: number, userEnteredFormat: object, effectiveFormat?: object) => ({
  row: 0,
  col,
  userEnteredFormat,
  effectiveFormat,
});

describe('google sheet style import', () => {
  it('maps bold, italic, strikethrough, size, and a known font', () => {
    const { format_ops } = mapGoogleSheetStyles({
      matrix: [['a', 'b', 'c', 'd']],
      cells: [
        cell(0, { textFormat: { bold: true } }),
        cell(1, { textFormat: { italic: true } }),
        cell(2, { textFormat: { strikethrough: true } }),
        cell(3, { textFormat: { fontSize: 15, fontFamily: 'Arial' } }),
      ],
    });
    expect(format_ops[0]).toMatchObject({ column: 0, bold: true });
    expect(format_ops[1]).toMatchObject({ column: 1, italic: true });
    expect(format_ops[2]).toMatchObject({ column: 2, strikethrough: true });
    expect(format_ops[3]).toMatchObject({ font_size: 16, font_family: 'Arial, sans-serif' });
  });

  it('keeps the imported text and fill colours and drops black', () => {
    const { format_ops, highlight_ops } = mapGoogleSheetStyles({
      matrix: [['a', 'b']],
      cells: [
        cell(0, { textFormat: { foregroundColor: { red: 0.86, green: 0.15, blue: 0.15 } } }),
        cell(1, {
          textFormat: { foregroundColor: { red: 0, green: 0, blue: 0 } },
          backgroundColor: { red: 0.99, green: 0.94, blue: 0.55 },
        }),
      ],
    });
    expect(format_ops[0].text_color).toBe('#DB2626');
    expect(format_ops.some((op) => op.column === 1)).toBe(false);
    expect(highlight_ops[0]).toMatchObject({ col: 1, color: '#FCF08C' });
  });

  it('keeps a percent fraction and a dollar amount, and ignores an unknown currency', () => {
    const { format_ops } = mapGoogleSheetStyles({
      matrix: [['0.5', '1.5', '2']],
      cells: [
        cell(0, { numberFormat: { type: 'PERCENT', pattern: '0.00%' } }),
        cell(1, { numberFormat: { type: 'CURRENCY', pattern: '$#,##0.00' } }),
        cell(2, { numberFormat: { type: 'CURRENCY', pattern: '"AUD" #,##0.00' } }),
      ],
    });
    expect(format_ops[0].number_format).toEqual({ type: 'PERCENT', currency_code: null, decimal_places: 2 });
    expect(format_ops[1].number_format).toEqual({ type: 'CURRENCY', currency_code: 'USD', decimal_places: 2 });
    expect(format_ops.some((op) => op.column === 2)).toBe(false);
  });

  it('does not emit an op for an unstyled value', () => {
    const { format_ops, highlight_ops, matrix } = mapGoogleSheetStyles({
      matrix: [['Ada']],
      cells: [],
    });
    expect(matrix).toEqual([['Ada']]);
    expect(format_ops).toEqual([]);
    expect(highlight_ops).toEqual([]);
  });
  it('uses the spreadsheet default size for a valued cell with no explicit size', () => {
    const { format_ops } = mapGoogleSheetStyles({
      matrix: [['Hello']],
      cells: [],
      default_font_size: 10,
    });
    expect(format_ops).toEqual([
      expect.objectContaining({ row: 0, column: 0, font_size: 10, font_family: null }),
    ]);
  });
  
  it('keeps an explicit size ahead of the spreadsheet default', () => {
    const { format_ops } = mapGoogleSheetStyles({
      matrix: [['Hello']],
      cells: [cell(0, { textFormat: { fontSize: 14 } })],
      default_font_size: 10,
    });
    expect(format_ops[0].font_size).toBe(14);
  });
  
  it('does not invent a size for an empty cell', () => {
    const { format_ops } = mapGoogleSheetStyles({
      matrix: [['']],
      cells: [cell(0, { textFormat: { bold: true } })],
      default_font_size: 10,
    });
    expect(format_ops).toEqual([
      expect.objectContaining({ column: 0, bold: true, font_size: null }),
    ]);
  });
  
  it('leaves size unset when Google sends no default', () => {
    const { format_ops } = mapGoogleSheetStyles({
      matrix: [['Hello']],
      cells: [],
      default_font_size: null,
    });
    expect(format_ops).toEqual([]);
  });
  
  it('still maps an explicit font family', () => {
    const { format_ops } = mapGoogleSheetStyles({
      matrix: [['Hello']],
      cells: [cell(0, { textFormat: { fontFamily: 'Georgia' } })],
      default_font_size: 10,
    });
    expect(format_ops[0].font_family).toBe('Georgia, serif');
    expect(format_ops[0].font_size).toBe(10);
  });
  
  it('does not invent a font family from the default size', () => {
    const { format_ops } = mapGoogleSheetStyles({
      matrix: [['Hello']],
      cells: [],
      default_font_size: 10,
    });
    expect(format_ops[0].font_family).toBeNull();
    expect(format_ops[0].font_size).toBe(10);
  });

  it('maps a scientific pattern to the same plain number format Excel produces', () => {
    const { format_ops } = mapGoogleSheetStyles({
      matrix: [['1500']],
      cells: [cell(0, { numberFormat: { type: 'SCIENTIFIC', pattern: '0.00E+00' } })],
    });
    expect(format_ops[0].number_format).toEqual({
      type: 'NUMBER',
      currency_code: null,
      decimal_places: 4,
    });
  });

  it('does not turn a dong format token into USD', () => {
    const { format_ops } = mapGoogleSheetStyles({
      matrix: [['80', '60']],
      cells: [
        cell(0, { numberFormat: { type: 'CURRENCY', pattern: '[$₫]#,##0' } }),
        cell(1, { numberFormat: { type: 'CURRENCY', pattern: '[$$-409]#,##0.00' } }),
      ],
    });
    expect(format_ops.some((op) => op.column === 0)).toBe(false);
    expect(format_ops[0].number_format).toEqual({ type: 'CURRENCY', currency_code: 'USD', decimal_places: 2 });
  });

  it('drops an Australian locale currency and keeps a bracketed US dollar', () => {
    const { matrix, format_ops } = mapGoogleSheetStyles({
      matrix: [['60', '60']],
      cells: [
        cell(0, { numberFormat: { type: 'CURRENCY', pattern: '[$$-C09]#,##0.00' } }),
        cell(1, { numberFormat: { type: 'CURRENCY', pattern: '[$$-409]#,##0.00' } }),
      ],
    });
    expect(matrix[0][0]).toBe('60');
    expect(format_ops.some((op) => op.column === 0)).toBe(false);
    expect(format_ops[0].number_format).toEqual({
      type: 'CURRENCY',
      currency_code: 'USD',
      decimal_places: 2,
    });
  });
});