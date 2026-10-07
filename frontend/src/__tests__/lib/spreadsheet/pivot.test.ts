import {
  normalizeColumnConfig,
  buildPivotTable,
  formatPivotValue,
  pivotResultToCellOperations,
  generateClearOperationsForStaleCells,
  generatePivotSheetName,
  createEmptyPivotConfig,
  isPivotConfigValid,
  type SourceColumn,
  type SourceRow,
} from '@/lib/spreadsheet/pivot';

const columns: SourceColumn[] = [
  { index: 0, header: 'Region' },
  { index: 1, header: 'Product' },
  { index: 2, header: 'Sales' },
  { index: 3, header: 'Qty' },
];

const sourceRows: SourceRow[] = [
  { 0: 'East', 1: 'A', 2: '10', 3: '2' },
  { 0: 'East', 1: 'B', 2: '20', 3: '4' },
  { 0: 'West', 1: 'A', 2: '30', 3: '6' },
  { 0: 'West', 1: 'B', 2: '40', 3: '8' },
  { 0: '', 1: 'A', 2: '99', 3: '1' }, // skipped: empty row field
];

describe('normalizeColumnConfig', () => {
  it('normalizes string columns to asc sort', () => {
    expect(normalizeColumnConfig(['Product', 'Region'])).toEqual([
      { field: 'Product', sort: 'asc' },
      { field: 'Region', sort: 'asc' },
    ]);
  });

  it('preserves object columns and defaults missing sort', () => {
    expect(
      normalizeColumnConfig([
        { field: 'Product', sort: 'desc' },
        { field: 'Region' },
      ])
    ).toEqual([
      { field: 'Product', sort: 'desc' },
      { field: 'Region', sort: 'asc' },
    ]);
  });
});

describe('buildPivotTable', () => {
  it('returns empty result when rows or values are missing', () => {
    expect(
      buildPivotTable(sourceRows, columns, {
        rows: [],
        columns: [],
        values: [{ field: 'Sales', aggregation: 'SUM' }],
      })
    ).toEqual({ headers: [], body: [], rowCount: 0, colCount: 0 });

    expect(
      buildPivotTable(sourceRows, columns, {
        rows: ['Region'],
        columns: [],
        values: [],
      })
    ).toEqual({ headers: [], body: [], rowCount: 0, colCount: 0 });
  });

  it('returns empty when field names do not match source headers', () => {
    expect(
      buildPivotTable(sourceRows, columns, {
        rows: ['Missing'],
        columns: [],
        values: [{ field: 'Nope', aggregation: 'SUM' }],
      })
    ).toEqual({ headers: [], body: [], rowCount: 0, colCount: 0 });
  });

  it('aggregates with SUM/COUNT/AVG/MIN/MAX/MEDIAN and optional column field', () => {
    const sum = buildPivotTable(sourceRows, columns, {
      rows: ['Region'],
      columns: ['Product'],
      values: [{ field: 'Sales', aggregation: 'SUM' }],
    });
    expect(sum.headers[0]).toEqual(expect.arrayContaining(['Region', 'A', 'B']));
    expect(sum.body.some((row) => row[0] === 'East')).toBe(true);
    expect(sum.body.some((row) => row[0] === 'Total')).toBe(true);

    for (const aggregation of ['COUNT', 'AVG', 'MIN', 'MAX', 'MEDIAN'] as const) {
      const result = buildPivotTable(sourceRows, columns, {
        rows: ['Region'],
        columns: [],
        values: [{ field: 'Sales', aggregation }],
        showGrandTotalRow: false,
      });
      expect(result.body.length).toBeGreaterThan(0);
      expect(result.headers[0]).toContain(`${aggregation}(Sales)`);
    }
  });

  it('supports multi-column headers, desc sort, and percentage display modes', () => {
    const rows: SourceRow[] = [
      { 0: 'East', 1: 'A', 2: '10', 3: 'Q1' },
      { 0: 'East', 1: 'B', 2: '30', 3: 'Q1' },
      { 0: 'West', 1: 'A', 2: '20', 3: 'Q2' },
    ];
    const cols: SourceColumn[] = [
      { index: 0, header: 'Region' },
      { index: 1, header: 'Product' },
      { index: 2, header: 'Sales' },
      { index: 3, header: 'Quarter' },
    ];

    const result = buildPivotTable(rows, cols, {
      rows: ['Region'],
      columns: [
        { field: 'Product', sort: 'desc' },
        { field: 'Quarter', sort: 'asc' },
      ],
      values: [
        { field: 'Sales', aggregation: 'SUM', display: 'ROW_PERCENT' },
        { field: 'Sales', aggregation: 'SUM', display: 'COLUMN_PERCENT' },
        { field: 'Sales', aggregation: 'SUM', display: 'TOTAL_PERCENT' },
      ],
    });

    expect(result.headers.length).toBeGreaterThan(1);
    expect(result.body.some((row) => String(row[1]).includes('%'))).toBe(true);
    expect(result.body.some((row) => row[0] === 'Total')).toBe(true);
  });

  it('treats non-numeric values as zero and keeps empty col keys as total', () => {
    const rows: SourceRow[] = [
      { 0: 'East', 1: '', 2: 'abc' },
      { 0: 'East', 1: 'A', 2: '5' },
    ];
    const result = buildPivotTable(rows, columns, {
      rows: ['Region'],
      columns: ['Product'],
      values: [{ field: 'Sales', aggregation: 'SUM' }],
      showGrandTotalRow: false,
    });
    expect(result.body.length).toBe(1);
  });
});

describe('formatPivotValue / cell ops / helpers', () => {
  it('formats integers and decimals', () => {
    expect(formatPivotValue(12)).toBe((12).toLocaleString());
    expect(formatPivotValue(12.345)).toContain('12');
  });

  it('converts pivot results to set/clear operations', () => {
    const result = buildPivotTable(sourceRows, columns, {
      rows: ['Region'],
      columns: [],
      values: [{ field: 'Sales', aggregation: 'SUM' }],
      showGrandTotalRow: false,
    });
    const ops = pivotResultToCellOperations(result);
    expect(ops.every((op) => op.operation === 'set')).toBe(true);
    expect(ops.length).toBeGreaterThan(0);

    const clears = generateClearOperationsForStaleCells(5, 5, 2, 2);
    expect(clears.some((op) => op.operation === 'clear')).toBe(true);
    expect(generateClearOperationsForStaleCells(2, 2, 5, 5)).toEqual([]);
  });

  it('generates unique pivot sheet names and validates config', () => {
    expect(generatePivotSheetName([])).toBe('Pivot Table 1');
    expect(generatePivotSheetName(['Pivot Table 1'])).toBe('Pivot Table 2');
    expect(generatePivotSheetName(['Pivot Table 1', 'Pivot Table 3'])).toBe(
      'Pivot Table 4'
    );

    const empty = createEmptyPivotConfig(7);
    expect(empty.sourceSheetId).toBe(7);
    expect(isPivotConfigValid(empty)).toBe(false);
    expect(
      isPivotConfigValid({
        ...empty,
        rows: ['Region'],
        values: [{ field: 'Sales', aggregation: 'SUM' }],
      })
    ).toBe(true);
  });
});
