export type GoogleColor = { red: number; green: number; blue: number };

export type GoogleTextFormat = {
  bold?: boolean;
  italic?: boolean;
  strikethrough?: boolean;
  fontFamily?: string;
  fontSize?: number;
  foregroundColor?: GoogleColor;
};

export type GoogleNumberFormat = {
  type: 'NUMBER' | 'PERCENT' | 'CURRENCY';
  pattern: string;
};

export type GoogleUserEnteredFormat = {
  textFormat?: GoogleTextFormat;
  backgroundColor?: GoogleColor;
  numberFormat?: GoogleNumberFormat;
};

export type GoogleSheetExportFormat = {
  row: number;
  column: number;
  userEnteredFormat: GoogleUserEnteredFormat;
};

export type AppNumberFormat = {
  type: 'GENERAL' | 'NUMBER' | 'CURRENCY' | 'PERCENT';
  currencyCode?: string | null;
  decimalPlaces?: number | null;
};

export type AppCellFormat = {
  bold: boolean;
  italic: boolean;
  strikethrough: boolean;
  textColor: string | null;
  fontFamily: string | null;
  fontSize: number | null;
  numberFormat: AppNumberFormat | null;
};

export type ExportCell = { row: number; column: number; raw: string };

export type ExportHighlight = {
  scope: 'CELL' | 'ROW' | 'COLUMN';
  row?: number;
  column?: number;
  color: string;
};

const GOOGLE_FONT_FAMILY: Record<string, string> = {
  'Arial, sans-serif': 'Arial',
  'Helvetica, Arial, sans-serif': 'Helvetica',
  'Georgia, serif': 'Georgia',
  '"Times New Roman", Times, serif': 'Times New Roman',
  monospace: 'Consolas',
};

const CURRENCY_PREFIX: Record<string, string> = {
  USD: '[$$-409]',
  GBP: '[$£-809]',
  EUR: '[$€-407]',
  JPY: '[$¥-411]',
};

export function googleColor(hex: string | null | undefined): GoogleColor | null {
  if (!hex) return null;
  const raw = hex.trim().replace(/^#/, '');
  if (!/^[0-9a-fA-F]{6}$/.test(raw)) return null;
  const value = Number.parseInt(raw, 16);
  return {
    red: ((value >> 16) & 255) / 255,
    green: ((value >> 8) & 255) / 255,
    blue: (value & 255) / 255,
  };
}

function decimalPlaces(value: number | null | undefined): number {
  if (value == null || !Number.isFinite(value)) return 2;
  return Math.max(0, Math.min(10, Math.trunc(value)));
}

export function googleNumberFormat(format: AppNumberFormat | null | undefined): GoogleNumberFormat | null {
  if (!format || format.type === 'GENERAL') return null;
  const places = decimalPlaces(format.decimalPlaces);
  const number = places === 0 ? '#,##0' : `#,##0.${'0'.repeat(places)}`;
  const percent = places === 0 ? '0%' : `0.${'0'.repeat(places)}%`;
  if (format.type === 'PERCENT') return { type: 'PERCENT', pattern: percent };
  if (format.type === 'NUMBER') return { type: 'NUMBER', pattern: number };
  if (format.type === 'CURRENCY') {
    const prefix = CURRENCY_PREFIX[(format.currencyCode || '').toUpperCase()];
    if (!prefix) return { type: 'NUMBER', pattern: number };
    return { type: 'CURRENCY', pattern: `${prefix}${number}` };
  }
  return null;
}

function userEnteredFormatFor(
  format: AppCellFormat | undefined,
  highlight: string | null,
): GoogleUserEnteredFormat | null {
  const result: GoogleUserEnteredFormat = {};
  const text: GoogleTextFormat = {};
  if (format?.bold) text.bold = true;
  if (format?.italic) text.italic = true;
  if (format?.strikethrough) text.strikethrough = true;
  const family = GOOGLE_FONT_FAMILY[(format?.fontFamily || '').trim()];
  if (family) text.fontFamily = family;
  if (format?.fontSize != null) text.fontSize = format.fontSize;
  const foreground = googleColor(format?.textColor);
  if (foreground) text.foregroundColor = foreground;
  if (Object.keys(text).length) result.textFormat = text;
  const numberFormat = googleNumberFormat(format?.numberFormat);
  if (numberFormat) result.numberFormat = numberFormat;
  const background = googleColor(highlight);
  if (background) result.backgroundColor = background;
  return Object.keys(result).length ? result : null;
}

export function buildGoogleSheetExport(input: {
  cells: ExportCell[];
  formats: Array<{ row: number; column: number; format: AppCellFormat }>;
  highlights: ExportHighlight[];
}): { matrix: string[][]; formats: GoogleSheetExportFormat[] } {
  const values = new Map<string, string>();
  const formatAt = new Map<string, AppCellFormat>();
  const cellFill = new Map<string, string>();
  const rowFill = new Map<number, string>();
  const columnFill = new Map<number, string>();
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
    formatAt.set(`${item.row}:${item.column}`, item.format);
    grow(item.row, item.column);
  }
  for (const highlight of input.highlights) {
    if (highlight.scope === 'CELL' && highlight.row != null && highlight.column != null) {
      cellFill.set(`${highlight.row}:${highlight.column}`, highlight.color);
      grow(highlight.row, highlight.column);
    } else if (highlight.scope === 'ROW' && highlight.row != null) {
      rowFill.set(highlight.row, highlight.color);
      grow(highlight.row, undefined);
    } else if (highlight.scope === 'COLUMN' && highlight.column != null) {
      columnFill.set(highlight.column, highlight.color);
      grow(undefined, highlight.column);
    }
  }

  if (maxRow < 0 || maxCol < 0) return { matrix: [['']], formats: [] };

  const matrix: string[][] = [];
  const formats: GoogleSheetExportFormat[] = [];
  for (let row = 0; row <= maxRow; row += 1) {
    const line: string[] = [];
    for (let column = 0; column <= maxCol; column += 1) {
      line.push(values.get(`${row}:${column}`) ?? '');
      const highlight =
        cellFill.get(`${row}:${column}`) ??
        rowFill.get(row) ??
        columnFill.get(column) ??
        null;
      const userEnteredFormat = userEnteredFormatFor(formatAt.get(`${row}:${column}`), highlight);
      if (userEnteredFormat) formats.push({ row, column, userEnteredFormat });
    }
    matrix.push(line);
  }
  return { matrix, formats };
}