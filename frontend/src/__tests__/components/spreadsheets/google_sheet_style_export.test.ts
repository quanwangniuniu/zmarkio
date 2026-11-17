import { buildGoogleSheetExport } from '@/components/spreadsheets/google_sheet_style_export';

describe('google sheet export matrix', () => {
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

  it('grows the grid to reach formatted and highlighted blank cells', () => {
    const { matrix } = buildGoogleSheetExport({
      cells: [{ row: 0, column: 0, raw: 'Ada' }],
      formats: [{ row: 2, column: 3 }],
      highlights: [{ scope: 'CELL', row: 9, column: 0, color: '#FEF08A' }],
    });
    expect(matrix).toHaveLength(10);
    expect(matrix[0]).toEqual(['Ada', '', '', '']);
    expect(matrix[2][3]).toBe('');
    expect(matrix[9][0]).toBe('');
  });
});