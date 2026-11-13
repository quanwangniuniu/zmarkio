
import ExcelJS from 'exceljs';
import JSZip from 'jszip';

export const FONT_FAMILY: Record<string, string> = {
  'arial': 'Arial, sans-serif',
  'helvetica': 'Helvetica, Arial, sans-serif',
  'georgia': 'Georgia, serif',
  'times new roman': '"Times New Roman", Times, serif',
  'courier new': 'monospace',
  'consolas': 'monospace',
}

export const FONT_SIZES = [9, 10, 11, 12, 14, 16, 18, 20, 24, 28]

// Read the workbook's Normal font size from xl/styles.xml. Returns null when it is missing.
async function readWorkbookDefaultFontSize(buf: ArrayBuffer): Promise<number | null> {
  const zip = await JSZip.loadAsync(buf);
  const xml = await zip.file('xl/styles.xml')?.async('string');
  if (!xml) return null;

  const doc = new DOMParser().parseFromString(xml, 'application/xml');
  const fontsRoot = doc.getElementsByTagName('fonts')[0];
  const fontEls = fontsRoot ? fontsRoot.getElementsByTagName('font') : [];
  if (!fontEls.length) return null;

  let xfId = 0;
  const cellStyles = doc.getElementsByTagName('cellStyle');
  for (let i = 0; i < cellStyles.length; i++) {
    if (cellStyles[i].getAttribute('name') === 'Normal') {
      xfId = Number(cellStyles[i].getAttribute('xfId') ?? 0);
      break;
    }
  }

  const styleXfs = doc.getElementsByTagName('cellStyleXfs')[0];
  const xfs = styleXfs ? styleXfs.getElementsByTagName('xf') : [];
  const fontId = Number(xfs[xfId]?.getAttribute('fontId') ?? 0);

  const sz = fontEls[fontId]?.getElementsByTagName('sz')[0]?.getAttribute('val') ?? fontEls[0]?.getElementsByTagName('sz')[0]?.getAttribute('val');
  const size = Number(sz);
  return Number.isFinite(size) ? size : null;
}

// Return every worksheet name in the file.
export async function listXlsxSheetNames(file: File): Promise<string[]> {
  const workbook = new ExcelJS.Workbook();
  await workbook.xlsx.load(await file.arrayBuffer());
  return workbook.worksheets.map((ws) => ws.name);
}

// Read one worksheet with ExcelJS and return its values, format operations, and highlight operations.
export async function parseSheetStyles(file: File, sheetName: string) {
  const buf = await file.arrayBuffer();
  const defaultSize = await readWorkbookDefaultFontSize(buf);
  const themePalette = await readThemePalette(buf);

  const workbook = new ExcelJS.Workbook();
  await workbook.xlsx.load(await file.arrayBuffer());
  const worksheet = workbook.getWorksheet(sheetName);
  if (!worksheet) throw new Error(`Worksheet not found: ${sheetName}`);
  const matrix: string[][] = [];
  const format_ops: Array<{
    row: number; column: number;
    bold: boolean; italic: boolean; strikethrough: boolean;
    text_color: string | null; font_family: string | null; font_size: number | null;
    number_format: { type?: 'PERCENT' | 'CURRENCY' | 'NUMBER'; currency_code?: string | null; decimal_places?: number | null } | null;
  }> = [];
  const highlight_ops: Array<{
    scope: 'CELL'; operation: 'SET'; row: number; col: number; color: string;
  }> = [];

  worksheet.eachRow({ includeEmpty: true }, (row, rowNumber) => {
    row.eachCell({ includeEmpty: true }, (cell, colNumber) => {
      if (cell.type === ExcelJS.ValueType.Merge) return;
      const r = rowNumber - 1;
      const c = colNumber - 1;
      const font = cell.font ?? {};
      const value = cell.value;
      const bold = Boolean(font.bold);
      const italic = Boolean(font.italic);
      const strike = Boolean(font.strike);
      const fillType = cell.fill && 'pattern' in cell.fill ? cell.fill.pattern : undefined;
      const text_color = argbToHex(resolveExcelColor(font.color, themePalette));
      const fg = cell.fill && 'fgColor' in cell.fill ? cell.fill.fgColor : undefined;
      const fill = fillType === 'solid' ? argbToHex(resolveExcelColor(fg, themePalette)) : null;
      const family = FONT_FAMILY[(font.name || '').trim().toLowerCase()] ?? null;
      const number_format = parseNumberFormat(typeof cell.numFmt === 'string' ? cell.numFmt : null);
      const workbookSize = nearestSize(defaultSize);
      const reportedSize = nearestSize(font.size);
      const hasValue = value != null;
      const hasExplicitFormat = Boolean(
        bold || italic || strike || text_color || family || number_format || fill,
      );
      if (!hasValue && !hasExplicitFormat) return;

      while (matrix.length <= r) matrix.push([]);
      while (matrix[r].length <= c) matrix[r].push('');
      const formula = typeof cell.formula === 'string' ? cell.formula.trim() : '';
      matrix[r][c] = formula
        ? (formula.startsWith('=') ? formula : `=${formula}`)
        : cellToMatrixValue(value);
      const size = hasValue ? (reportedSize ?? workbookSize) : null;
      if (bold || italic || strike || text_color || family || size || number_format) {
        format_ops.push({
          row: r, column: c, bold, italic, strikethrough: strike,
          text_color, font_family: family, font_size: size, number_format,
        });
      }
      if (fill) {
        highlight_ops.push({ scope: 'CELL', operation: 'SET', row: r, col: c, color: fill });
      }
    });
  });
  return { matrix, format_ops, highlight_ops };
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

// Convert a #RRGGBB color to hue, saturation, and lightness.
function colourHsl(hex: string): { h: number; s: number; l: number } {
  const channel = (i: number) => parseInt(hex.slice(i, i + 2), 16) / 255;
  const r = channel(1);
  const g = channel(3);
  const b = channel(5);
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  let h = 0;
  let s = 0;
  const l = (max + min) / 2;
  if (max !== min) {
    const d = max - min;
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    switch (max) {
      case r: h = (g - b) / d + (g < b ? 6 : 0); break;
      case g: h = (b - r) / d + 2; break;
      default: h = (r - g) / d + 4; break;
    }
    h *= 60;
  }
  return { h, s, l };
}


// Convert hue, saturation, and lightness back to a #RRGGBB string.
function hslToHex(h: number, s: number, l: number): string {
  const hue2rgb = (p: number, q: number, t: number) => {
    if (t < 0) t += 1;
    if (t > 1) t -= 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  };
  let r: number;
  let g: number;
  let b: number;
  if (s === 0) {
    r = g = b = l;
  } else {
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    const p = 2 * l - q;
    const hk = h / 360;
    r = hue2rgb(p, q, hk + 1 / 3);
    g = hue2rgb(p, q, hk);
    b = hue2rgb(p, q, hk - 1 / 3);
  }
  const channel = (x: number) => Math.round(x * 255).toString(16).padStart(2, '0');
  return `#${channel(r)}${channel(g)}${channel(b)}`;
}

// Lighten or darken a theme color by Excel's tint. No tint returns the original hex.
function applyTint(hex: string, tint: number): string {
  if (!tint) return hex;
  const { h, s, l } = colourHsl(hex.startsWith('#') ? hex : `#${hex}`);
  const next = tint < 0 ? l * (1 + tint) : l * (1 - tint) + tint;
  return hslToHex(h, s, Math.min(1, Math.max(0, next)));
}

// Read Excel's theme color slots from xl/theme/theme1.xml, in palette order.
async function readThemePalette(buf: ArrayBuffer): Promise<Array<string | null>> {
  const zip = await JSZip.loadAsync(buf);
  const xml = await zip.file('xl/theme/theme1.xml')?.async('string');
  if (!xml) return [];
  const doc = new DOMParser().parseFromString(xml, 'application/xml');
  const all = doc.getElementsByTagName('*');
  let scheme: Element | undefined;
  for (let i = 0; i < all.length; i++) {
    if (all[i].localName === 'clrScheme') {
      scheme = all[i];
      break;
    }
  }
  if (!scheme) return [];
  const byName = new Map<string, string | null>();
  for (let i = 0; i < scheme.children.length; i++) {
    const slot = scheme.children[i];
    let val: string | null = null;
    for (let j = 0; j < slot.children.length; j++) {
      const node = slot.children[j];
      if (node.localName === 'srgbClr') val = node.getAttribute('val');
      if (node.localName === 'sysClr') val = node.getAttribute('lastClr');
    }
    byName.set(slot.localName, val ? val.toUpperCase() : null);
  }
  const order = [
    'lt1', 'dk1', 'lt2', 'dk2',
    'accent1', 'accent2', 'accent3', 'accent4', 'accent5', 'accent6',
    'hlink', 'folHlink',
  ];
  return order.map((name) => byName.get(name) ?? null);
}

// Resolve an Excel color to an AARRGGBB string from either an explicit value or a theme color plus tint.
function resolveExcelColor(
  color: { argb?: string; theme?: number; tint?: number } | undefined,
  palette: readonly (string | null)[],
): string | undefined {
  if (!color) return undefined;
  if (typeof color.argb === 'string') return color.argb;
  const base = color.theme == null ? null : palette[color.theme];
  if (!base) return undefined;
  const tinted = applyTint(`#${base}`, color.tint ?? 0);
  return `FF${tinted.replace('#', '')}`;
}

// Drop exact black and white, and extract colour
function argbToHex(argb: string | undefined): string | null {
  if (!argb || typeof argb !== 'string') return null; // theme / missing
    const rgb = argb.slice(-6);
    if (rgb.toUpperCase() === '000000' || rgb.toUpperCase() === 'FFFFFF') return null;
    return `#${rgb.toUpperCase()}`;
  }

// Snap a font size to the nearest supported size. An equal distance keeps the larger size.
export function nearestSize(size: number | null | undefined): number | null {
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





