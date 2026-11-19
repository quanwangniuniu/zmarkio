import ExcelJS from 'exceljs';

// Read one worksheet's values with ExcelJS as the grid's text matrix.
// Excel formatting is converted on the backend when the file is saved as the import source.
export async function parseSheetValues(file: File, sheetName: string): Promise<string[][]> {
  const workbook = new ExcelJS.Workbook();
  await workbook.xlsx.load(await file.arrayBuffer());
  const worksheet = workbook.getWorksheet(sheetName);
  if (!worksheet) throw new Error(`Worksheet not found: ${sheetName}`);
  const matrix: string[][] = [];

  worksheet.eachRow({ includeEmpty: true }, (row, rowNumber) => {
    row.eachCell({ includeEmpty: true }, (cell, colNumber) => {
      if (cell.type === ExcelJS.ValueType.Merge) return;
      const value = cell.value;
      // A styled empty cell still counts toward the imported range (its formatting is
      // converted on the backend), as it did before. Unstyled empty cells are skipped.
      const styled = Object.keys(cell.style ?? {}).length > 0;
      if (value == null && !styled) return;
      const r = rowNumber - 1;
      const c = colNumber - 1;
      while (matrix.length <= r) matrix.push([]);
      while (matrix[r].length <= c) matrix[r].push('');
      if (value == null) return;
      const formula = typeof cell.formula === 'string' ? cell.formula.trim() : '';
      matrix[r][c] = formula
        ? (formula.startsWith('=') ? formula : `=${formula}`)
        : cellToMatrixValue(value);
    });
  });
  return matrix;
}

// Turn an ExcelJS cell value into the string saved in the web grid.
function cellToMatrixValue(value: unknown): string {
  if (value == null) return '';
  if (value instanceof Date) {
    const y = value.getUTCFullYear();
    const m = String(value.getUTCMonth() + 1).padStart(2, '0');
    const d = String(value.getUTCDate()).padStart(2, '0');
    if (value.getUTCHours() === 0 && value.getUTCMinutes() === 0 && value.getUTCSeconds() === 0) {
      return `${y}-${m}-${d}`;
    }
    const hh = String(value.getUTCHours()).padStart(2, '0');
    const mm = String(value.getUTCMinutes()).padStart(2, '0');
    return `${y}-${m}-${d} ${hh}:${mm}`;
  }
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : '';
  if (typeof value === 'boolean') return value ? 'TRUE' : 'FALSE';
  if (typeof value === 'string') return value;
  if (typeof value === 'object') {
    const cell = value as {
      richText?: { text?: string }[];
      formula?: string;
      result?: unknown;
      error?: string;
      text?: string;
    };
    if (Array.isArray(cell.richText)) return cell.richText.map((part) => part.text ?? '').join('');
    if (typeof cell.formula === 'string') {
      return cell.formula.startsWith('=') ? cell.formula : `=${cell.formula}`;
    }
    if (cell.result != null) return cellToMatrixValue(cell.result);
    if (cell.error != null) return String(cell.error);
    if (typeof cell.text === 'string') return cell.text;
  }
  return '';
}