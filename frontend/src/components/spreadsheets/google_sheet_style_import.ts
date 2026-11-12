const FONT_FAMILY: Record<string, string> = {
  'arial': 'Arial, sans-serif',
  'helvetica': 'Helvetica, Arial, sans-serif',
  'georgia': 'Georgia, serif',
  'times new roman': '"Times New Roman", Times, serif',
  'courier new': 'monospace',
  'consolas': 'monospace',
}

const FONT_SIZES = [9, 10, 11, 12, 14, 16, 18, 20, 24, 28]

// Snap a font size to the nearest supported size. An equal distance keeps the larger size.
function nearestSize(size: number | null | undefined): number | null {
  if (size == null) return null;
  return FONT_SIZES.reduce((a, b) =>
    Math.abs(a - size) < Math.abs(b - size) ? a : b);
}

// Turn an Excel or Google number pattern into PERCENT, CURRENCY, NUMBER, or null.
export function parseNumberFormat(code: string | null): {
  type: 'PERCENT' | 'CURRENCY' | 'NUMBER';
  currency_code: string | null;
  decimal_places: number;
} | null {
  if (!code || code === 'General') return null;
  if (code.includes('%')) {
    const zeroCount = (code.match(/0/g) ?? []).length;

    return {
      type: 'PERCENT',
      currency_code: null,
      decimal_places: code.includes('.') ? zeroCount - 1 : 0,
    };
  }

  const symbols: Record<string, string> = { '£': 'GBP', '€': 'EUR', '¥': 'JPY', '$': 'USD' };
  const localeCurrency: Record<string, string> = {
    '$-409': 'USD',
    '£-809': 'GBP',
    '€-407': 'EUR',
    '¥-411': 'JPY',
  };
  const supportedCodes = new Set(['USD', 'GBP', 'EUR', 'JPY']);
  const decimals = () => {
    const frac = code.includes('.') ? code.split('.').pop() ?? '' : '';
    return [...frac].filter((c) => c === '0').length;
  };

  const currency = (codeName: string) => ({
    type: 'CURRENCY' as const,
    currency_code: codeName,
    decimal_places: decimals(),
  });

  const bracket = /\[\$([^\]]*)\]/.exec(code);
  if (bracket) {
    const body = bracket[1];
    const split = body.match(/^(.*)-([0-9A-Fa-f]+)$/);
    const symbol = (split ? split[1] : body).trim();
    const locale = split ? split[2].toUpperCase() : null;
    const byLocale = locale ? localeCurrency[`${symbol}-${locale}`] : undefined;
    if (byLocale) return currency(byLocale);
    if (!locale && symbols[symbol]) return currency(symbols[symbol]);
    if (!locale && supportedCodes.has(symbol.toUpperCase())) return currency(symbol.toUpperCase());
    return null;
  }

  const quoted = /"([^"]*)"/.exec(code);
  if (quoted && /^[A-Za-z]{3}$/.test(quoted[1].trim())) {
    const token = quoted[1].trim().toUpperCase();
    return supportedCodes.has(token) ? currency(token) : null;
  }

  const symbol = ['£', '€', '¥', '$'].find((ch) => code.includes(ch));
  if (symbol) return currency(symbols[symbol]);
  if (code.includes('0')) {
    return { type: 'NUMBER', currency_code: null, decimal_places: decimals() };
  }
  return null;
}
type GoogleColor = { red?: number; green?: number; blue?: number; alpha?: number };
type GoogleTextFormat = {
  bold?: boolean;
  italic?: boolean;
  strikethrough?: boolean;
  fontFamily?: string;
  fontSize?: number;
  foregroundColor?: GoogleColor;
  foregroundColorStyle?: { rgbColor?: GoogleColor; themeColor?: string };
};
type GoogleFormat = {
  numberFormat?: { type?: string; pattern?: string };
  backgroundColor?: GoogleColor;
  backgroundColorStyle?: { rgbColor?: GoogleColor; themeColor?: string };
  textFormat?: GoogleTextFormat;
};
export type GoogleSheetCell = {
  row: number;
  col: number;
  userEnteredFormat?: GoogleFormat;
  effectiveFormat?: GoogleFormat;
};

function rgbHex(color: GoogleColor | undefined): string | null {
  if (!color || color.alpha === 0) return null;
  const byte = (value?: number) =>
    Math.round(Math.min(1, Math.max(0, value ?? 0)) * 255)
      .toString(16)
      .padStart(2, '0');
  const hex = `${byte(color.red)}${byte(color.green)}${byte(color.blue)}`.toUpperCase();
  if (hex === '000000' || hex === 'FFFFFF') return null;
  return `#${hex}`;
}

function pickColor(entered: GoogleColor | undefined, theme: string | undefined, resolved: GoogleColor | undefined): string | null {
  if (!entered && !theme) return null;
  return rgbHex(entered || resolved);
}

function numberFormatFromGoogle(format: GoogleFormat | undefined) {
  const kind = format?.numberFormat?.type;
  if (!kind || kind === 'TEXT' || kind === 'DATE' || kind === 'TIME' || kind === 'DATE_TIME') {
    return null;
  }
  const parsed = parseNumberFormat(format?.numberFormat?.pattern ?? null);
  if (kind === 'PERCENT') {
    return parsed?.type === 'PERCENT'
      ? parsed
      : { type: 'PERCENT' as const, currency_code: null, decimal_places: 0 };
  }
  if (kind === 'CURRENCY') {
    return parsed?.type === 'CURRENCY' ? parsed : null;
  }
  if (kind === 'NUMBER') {
    return parsed?.type === 'NUMBER'
      ? parsed
      : { type: 'NUMBER' as const, currency_code: null, decimal_places: 0 };
  }
  if (kind === 'SCIENTIFIC') {
    return parsed?.type === 'NUMBER' ? parsed : null;
  }

  return null;
}

function googleFontFamily(value: string | undefined): string | null {
    const name = (value || '')
      .split(',')[0]
      .trim()
      .replace(/^["']|["']$/g, '')
      .trim()
      .toLowerCase();
    return FONT_FAMILY[name] ?? null;
}

export function mapGoogleSheetStyles(payload: { matrix: string[][]; cells?: GoogleSheetCell[]; default_font_size?: number | null; }) {
  const format_ops = [];
  const highlight_ops = [];
  const defaultSize = payload.default_font_size == null ? null : nearestSize(payload.default_font_size);
  const seen = new Set<string>();
  for (const cell of payload.cells ?? []) {
    const entered = cell.userEnteredFormat ?? {};
    const effective = cell.effectiveFormat ?? {};
    const text = entered.textFormat ?? {};
    const text_color_hex = pickColor(
      text.foregroundColor || text.foregroundColorStyle?.rgbColor,
      text.foregroundColorStyle?.themeColor,
      effective.textFormat?.foregroundColor || effective.textFormat?.foregroundColorStyle?.rgbColor,
    );
    const fill_hex = pickColor(
      entered.backgroundColor || entered.backgroundColorStyle?.rgbColor,
      entered.backgroundColorStyle?.themeColor,
      effective.backgroundColor || effective.backgroundColorStyle?.rgbColor,
    );
    const text_color = text_color_hex;
    const fill = fill_hex;
    const font_family = googleFontFamily(text.fontFamily);
    seen.add(`${cell.row}:${cell.col}`);
    const explicitSize = text.fontSize == null ? null : nearestSize(text.fontSize);
    const hasValue = (payload.matrix[cell.row]?.[cell.col] ?? '') !== '';
    const font_size = explicitSize ?? (hasValue ? defaultSize : null);
    const number_format = numberFormatFromGoogle(entered);
    const bold = Boolean(text.bold);
    const italic = Boolean(text.italic);
    const strikethrough = Boolean(text.strikethrough);
    if (bold || italic || strikethrough || text_color || font_family || font_size || number_format) {
      format_ops.push({
        row: cell.row,
        column: cell.col,
        bold,
        italic,
        strikethrough,
        text_color,
        font_family,
        font_size,
        number_format,
      });
    }
    if (fill) {
      highlight_ops.push({ scope: 'CELL' as const, operation: 'SET' as const, row: cell.row, col: cell.col, color: fill });
    }
  }
  if (defaultSize != null) {
    payload.matrix.forEach((row, rowIndex) => {
      row.forEach((value, colIndex) => {
        if (value === '' || seen.has(`${rowIndex}:${colIndex}`)) return;
        format_ops.push({
          row: rowIndex,
          column: colIndex,
          bold: false,
          italic: false,
          strikethrough: false,
          text_color: null,
          font_family: null,
          font_size: defaultSize,
          number_format: null,
        });
      });
    });
  }
  return { matrix: payload.matrix, format_ops, highlight_ops };
}
