// Build the values grid for a Google Sheets export. The backend reads the stored
// cell formats and highlights and builds the Google formatting, so this grid only
// needs to reach every cell that has a value, a format or a highlight.

export type ExportCell = { row: number; column: number; raw: string };

export type ExportPosition = { row: number; column: number };

export type ExportHighlight = {
  scope: 'CELL' | 'ROW' | 'COLUMN';
  row?: number;
  column?: number;
  color: string;
};

export function buildGoogleSheetExport(input: {
  cells: ExportCell[];
  formats: ExportPosition[];
  highlights: ExportHighlight[];
}): { matrix: string[][] } {
  const values = new Map<string, string>();
  let maxRow = -1;
  let maxCol = -1;
  const grow = (row?: number, column?: number) => {
    if (row != null) maxRow = Math.max(maxRow, row);
    if (column != null) maxCol = Math.max(maxCol, column);
  };

  for (const cell of input.cells) {
    if (cell.raw === '') continue;
    values.set(`${cell.row}:${cell.column}`, cell.raw);
    grow(cell.row, cell.column);
  }
  for (const item of input.formats) {
    grow(item.row, item.column);
  }
  for (const highlight of input.highlights) {
    if (highlight.scope === 'CELL' && highlight.row != null && highlight.column != null) {
      grow(highlight.row, highlight.column);
    } else if (highlight.scope === 'ROW' && highlight.row != null) {
      grow(highlight.row, undefined);
    } else if (highlight.scope === 'COLUMN' && highlight.column != null) {
      grow(undefined, highlight.column);
    }
  }

  if (maxRow < 0 || maxCol < 0) return { matrix: [['']] };

  const matrix: string[][] = [];
  for (let row = 0; row <= maxRow; row += 1) {
    const line: string[] = [];
    for (let column = 0; column <= maxCol; column += 1) {
      line.push(values.get(`${row}:${column}`) ?? '');
    }
    matrix.push(line);
  }
  return { matrix };
}