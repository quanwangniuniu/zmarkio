import ExcelJS from 'exceljs';
import JSZip from 'jszip';
import { parseSheetStyles } from '@/components/spreadsheets/xlsx_style_import';
import { parseNumberFormat, parseSheetStyles } from '@/components/spreadsheets/xlsx_style_import';

function xlsxFile(bytes: Uint8Array): File {
  const file = new File([new Uint8Array(bytes)], 'book.xlsx');
  // jsdom's File does not implement Blob.arrayBuffer, which the importer calls.
  if (typeof file.arrayBuffer !== 'function') {
    const copy = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    Object.defineProperty(file, 'arrayBuffer', {
      value: async () => copy.slice(0),
    });
  }
  return file;
}

async function workbookFile(setup: (sheet: ExcelJS.Worksheet) => void): Promise<File> {
  const workbook = new ExcelJS.Workbook();
  const sheet = workbook.addWorksheet('Sheet1');
  setup(sheet);
  const buffer = await workbook.xlsx.writeBuffer();
  return xlsxFile(new Uint8Array(buffer));
}

async function parse(setup: (sheet: ExcelJS.Worksheet) => void) {
  return parseSheetStyles(await workbookFile(setup), 'Sheet1');
}

function formatAt(
  ops: Awaited<ReturnType<typeof parseSheetStyles>>['format_ops'],
  row: number,
  column: number,
) {
  return ops.find((op) => op.row === row && op.column === column);
}

function highlightAt(
  ops: Awaited<ReturnType<typeof parseSheetStyles>>['highlight_ops'],
  row: number,
  col: number,
) {
  return ops.find((op) => op.row === row && op.col === col);
}

describe('xlsx import values', () => {
  it('keeps a plain string', async () => {
    const { matrix } = await parse((sheet) => {
      sheet.getCell('A1').value = 'hello';
    });
    expect(matrix[0][0]).toBe('hello');
  });

  it('turns a finite number into a string', async () => {
    const { matrix } = await parse((sheet) => {
      sheet.getCell('A1').value = 42.5;
    });
    expect(matrix[0][0]).toBe('42.5');
  });

  it('turns booleans into TRUE and FALSE', async () => {
    const { matrix } = await parse((sheet) => {
      sheet.getCell('A1').value = true;
      sheet.getCell('A2').value = false;
    });
    expect(matrix[0][0]).toBe('TRUE');
    expect(matrix[1][0]).toBe('FALSE');
  });
});

describe('xlsx import formulas', () => {
  it('imports a formula with a leading equals and ignores the cached result', async () => {
    const { matrix } = await parse((sheet) => {
      sheet.getCell('A1').value = { formula: 'A1+1', result: 2 };
    });
    expect(matrix[0][0]).toBe('=A1+1');
    expect(matrix[0][0]).not.toBe('[object Object]');
  });

  it('shifts a shared formula instead of keeping the cached result', async () => {
    const { matrix } = await parse((sheet) => {
      sheet.getCell('A1').value = {
        formula: '$B1+C1',
        result: 2,
        shareType: 'shared',
        ref: 'A1:A12',
      };
      for (let row = 2; row <= 12; row += 1) {
        sheet.getCell(`A${row}`).value = { sharedFormula: 'A1', result: 3 };
      }
    });
    expect(matrix[0][0]).toBe('=$B1+C1');
    expect(matrix[8][0]).toBe('=$B9+C9');
    expect(matrix[8][0]).not.toBe('3');
  });
  
});

describe('xlsx import dates', () => {
  it('formats a UTC date-only value as YYYY-MM-DD', async () => {
    const { matrix } = await parse((sheet) => {
      sheet.getCell('A1').value = new Date(Date.UTC(2024, 0, 15));
      sheet.getCell('A1').numFmt = 'yyyy-mm-dd';
    });
    expect(matrix[0][0]).toBe('2024-01-15');
  });

  it('formats a UTC date and time as YYYY-MM-DD HH:mm', async () => {
    const { matrix } = await parse((sheet) => {
      sheet.getCell('A1').value = new Date(Date.UTC(2024, 0, 15, 13, 45, 0));
      sheet.getCell('A1').numFmt = 'yyyy-mm-dd hh:mm';
    });
    expect(matrix[0][0]).toBe('2024-01-15 13:45');
  });
});

describe('xlsx import font size', () => {
  it('uses the workbook Normal size for a plain cell and keeps an explicit size', async () => {
    const file = await workbookFile((sheet) => {
      sheet.getCell('A1').value = 'plain';
      sheet.getCell('B1').value = 'explicit';
      sheet.getCell('B1').font = { size: 9 };
    });
    const zip = await JSZip.loadAsync(await file.arrayBuffer());
    const xml = await zip.file('xl/styles.xml')?.async('string');
    expect(xml).toContain('<sz val="11"/>');
    zip.file('xl/styles.xml', xml!.replace('<sz val="11"/>', '<sz val="14"/>'));
    const patched = await zip.generateAsync({ type: 'uint8array' });
    const { format_ops } = await parseSheetStyles(xlsxFile(patched), 'Sheet1');

    expect(formatAt(format_ops, 0, 0)?.font_size).toBe(14);
    expect(formatAt(format_ops, 0, 1)?.font_size).toBe(9);
  });
});

describe('xlsx import font formatting', () => {
  it('keeps bold, italic, and strike', async () => {
    const { format_ops } = await parse((sheet) => {
      sheet.getCell('A1').value = 'bold';
      sheet.getCell('A1').font = { bold: true };
      sheet.getCell('A2').value = 'italic';
      sheet.getCell('A2').font = { italic: true };
      sheet.getCell('A3').value = 'strike';
      sheet.getCell('A3').font = { strike: true };
    });
    expect(formatAt(format_ops, 0, 0)?.bold).toBe(true);
    expect(formatAt(format_ops, 1, 0)?.italic).toBe(true);
    expect(formatAt(format_ops, 2, 0)?.strikethrough).toBe(true);
  });

  it('maps a supported font family and leaves an unknown family unset', async () => {
    const { format_ops } = await parse((sheet) => {
      sheet.getCell('A1').value = 'arial';
      sheet.getCell('A1').font = { name: 'Arial' };
      sheet.getCell('A2').value = 'calibri';
      sheet.getCell('A2').font = { name: 'Calibri' };
    });
    expect(formatAt(format_ops, 0, 0)?.font_family).toBe('Arial, sans-serif');
    expect(formatAt(format_ops, 1, 0)?.font_family).toBeNull();
  });
});

describe('xlsx import text colours', () => {
  it('keeps an exact toolbar colour', async () => {
    const { format_ops } = await parse((sheet) => {
      sheet.getCell('A1').value = 'red';
      sheet.getCell('A1').font = { color: { argb: 'FFDC2626' } };
    });
    expect(formatAt(format_ops, 0, 0)?.text_color).toBe('#DC2626');
  });

  it('keeps a pure red text colour', async () => {
    const { format_ops } = await parse((sheet) => {
      sheet.getCell('A1').value = 'pure red';
      sheet.getCell('A1').font = { color: { argb: 'FFFF0000' } };
    });
    expect(formatAt(format_ops, 0, 0)?.text_color).toBe('#FF0000');
  });

  it('keeps a dark blue text colour', async () => {
    const { format_ops } = await parse((sheet) => {
      sheet.getCell('A1').value = 'navy';
      sheet.getCell('A1').font = { color: { argb: 'FF376092' } };
    });
    expect(formatAt(format_ops, 0, 0)?.text_color).toBe('#376092');
  });

  it('drops exact black and exact white', async () => {
    const { format_ops } = await parse((sheet) => {
      sheet.getCell('A1').value = 'black';
      sheet.getCell('A1').font = { color: { argb: 'FF000000' } };
      sheet.getCell('A2').value = 'white';
      sheet.getCell('A2').font = { color: { argb: 'FFFFFFFF' } };
    });
    expect(formatAt(format_ops, 0, 0)?.text_color ?? null).toBeNull();
    expect(formatAt(format_ops, 1, 0)?.text_color ?? null).toBeNull();
  });
});

describe('xlsx import fills', () => {
  it('keeps an exact highlighter colour', async () => {
    const { highlight_ops } = await parse((sheet) => {
      sheet.getCell('A1').value = 'yellow';
      sheet.getCell('A1').fill = {
        type: 'pattern',
        pattern: 'solid',
        fgColor: { argb: 'FFFEF08A' },
      };
    });
    expect(highlightAt(highlight_ops, 0, 0)?.color).toBe('#FEF08A');
  });

  it('keeps a nearby yellow fill', async () => {
    const { highlight_ops } = await parse((sheet) => {
      sheet.getCell('A1').value = 'near yellow';
      sheet.getCell('A1').fill = {
        type: 'pattern',
        pattern: 'solid',
        fgColor: { argb: 'FFFDE047' },
      };
    });
    expect(highlightAt(highlight_ops, 0, 0)?.color).toBe('#FDE047');
  });

  it('keeps a dark blue fill', async () => {
    const { highlight_ops } = await parse((sheet) => {
      sheet.getCell('A1').value = 'navy';
      sheet.getCell('A1').fill = {
        type: 'pattern',
        pattern: 'solid',
        fgColor: { argb: 'FF376092' },
      };
    });
    expect(highlightAt(highlight_ops, 0, 0)?.color).toBe('#376092');
  });

  it('keeps a pure red fill', async () => {
    const { highlight_ops } = await parse((sheet) => {
      sheet.getCell('A1').value = 'red fill';
      sheet.getCell('A1').fill = {
        type: 'pattern',
        pattern: 'solid',
        fgColor: { argb: 'FFFF0000' },
      };
    });
    expect(highlightAt(highlight_ops, 0, 0)?.color).toBe('#FF0000');
  });

  it('ignores a non-solid fill', async () => {
    const { highlight_ops, matrix } = await parse((sheet) => {
      sheet.getCell('A1').value = 'pattern';
      sheet.getCell('A1').fill = {
        type: 'pattern',
        pattern: 'darkHorizontal',
        fgColor: { argb: 'FFFEF08A' },
      };
    });
    expect(matrix[0][0]).toBe('pattern');
    expect(highlightAt(highlight_ops, 0, 0)).toBeUndefined();
  });

  it('drops exact black and exact white fills', async () => {
    const { highlight_ops } = await parse((sheet) => {
      sheet.getCell('A1').value = 'black fill';
      sheet.getCell('A1').fill = {
        type: 'pattern',
        pattern: 'solid',
        fgColor: { argb: 'FF000000' },
      };
      sheet.getCell('A2').value = 'white fill';
      sheet.getCell('A2').fill = {
        type: 'pattern',
        pattern: 'solid',
        fgColor: { argb: 'FFFFFFFF' },
      };
    });
    expect(highlightAt(highlight_ops, 0, 0)).toBeUndefined();
    expect(highlightAt(highlight_ops, 1, 0)).toBeUndefined();
  });
});

describe('xlsx import number formats', () => {
  it('parses percent, plain numbers, and plain dollar currency', async () => {
    const { format_ops, matrix } = await parse((sheet) => {
      sheet.getCell('A1').value = 0.5;
      sheet.getCell('A1').numFmt = '0.00%';
      sheet.getCell('A2').value = 0.5;
      sheet.getCell('A2').numFmt = '0%';
      sheet.getCell('A3').value = 1.5;
      sheet.getCell('A3').numFmt = '#,##0.00';
      sheet.getCell('A4').value = 1;
      sheet.getCell('A4').numFmt = '$#,##0.00';
      sheet.getCell('A5').value = 1;
      sheet.getCell('A5').numFmt = 'General';
    });

    expect(matrix[0][0]).toBe('0.5');
    expect(formatAt(format_ops, 0, 0)?.number_format).toEqual({
      type: 'PERCENT',
      currency_code: null,
      decimal_places: 2,
    });
    expect(formatAt(format_ops, 1, 0)?.number_format).toEqual({
      type: 'PERCENT',
      currency_code: null,
      decimal_places: 0,
    });
    expect(formatAt(format_ops, 2, 0)?.number_format).toEqual({
      type: 'NUMBER',
      currency_code: null,
      decimal_places: 2,
    });
    expect(formatAt(format_ops, 3, 0)?.number_format).toEqual({
      type: 'CURRENCY',
      currency_code: 'USD',
      decimal_places: 2,
    });
    expect(formatAt(format_ops, 4, 0)?.number_format ?? null).toBeNull();
  });

  it('keeps supported locale currencies and does not turn AUD or CAD into USD', async () => {
    const { format_ops } = await parse((sheet) => {
      sheet.getCell('A1').value = 1;
      sheet.getCell('A1').numFmt = '[$$-409]#,##0.00';
      sheet.getCell('A2').value = 1;
      sheet.getCell('A2').numFmt = '[$£-809]#,##0.00';
      sheet.getCell('A3').value = 1;
      sheet.getCell('A3').numFmt = '[$€-407]#,##0.00';
      sheet.getCell('A4').value = 1;
      sheet.getCell('A4').numFmt = '[$¥-411]#,##0';
      sheet.getCell('A5').value = 1;
      sheet.getCell('A5').numFmt = '[$$-C09]#,##0.00';
      sheet.getCell('A6').value = 1;
      sheet.getCell('A6').numFmt = '[$$-1009]#,##0.0';
    });

    expect(formatAt(format_ops, 0, 0)?.number_format).toEqual({
      type: 'CURRENCY',
      currency_code: 'USD',
      decimal_places: 2,
    });
    expect(formatAt(format_ops, 1, 0)?.number_format).toEqual({
      type: 'CURRENCY',
      currency_code: 'GBP',
      decimal_places: 2,
    });
    expect(formatAt(format_ops, 2, 0)?.number_format).toEqual({
      type: 'CURRENCY',
      currency_code: 'EUR',
      decimal_places: 2,
    });
    expect(formatAt(format_ops, 3, 0)?.number_format).toEqual({
      type: 'CURRENCY',
      currency_code: 'JPY',
      decimal_places: 0,
    });
    expect(formatAt(format_ops, 4, 0)?.number_format ?? null).toBeNull();
    expect(formatAt(format_ops, 5, 0)?.number_format ?? null).toBeNull();

  });
});

describe('xlsx import empty cells', () => {
  it('skips an empty unformatted cell and does not invent the workbook font size', async () => {
    const { format_ops, matrix } = await parse((sheet) => {
      sheet.getCell('A1').value = 'kept';
    });
    expect(matrix[0][0]).toBe('kept');
    expect(format_ops.some((op) => op.row === 1)).toBe(false);
    expect(matrix[1]).toBeUndefined();
  });

  it('keeps formatting on an empty cell without applying the workbook font size', async () => {
    const { format_ops, matrix } = await parse((sheet) => {
      sheet.getCell('A1').font = { bold: true };
    });
    expect(matrix[0][0]).toBe('');
    expect(formatAt(format_ops, 0, 0)).toEqual(
      expect.objectContaining({ bold: true, font_size: null }),
    );
  });
});

describe('xlsx import merged cells', () => {
  it('keeps the master value and leaves the merged slave empty', async () => {
    const { matrix, format_ops, highlight_ops } = await parse((sheet) => {
      sheet.mergeCells('A1:B1');
      sheet.getCell('A1').value = 'Hello';
    });
    expect(matrix[0][0]).toBe('Hello');
    expect(matrix[0][1] ?? '').toBe('');
    expect(format_ops.some((op) => op.row === 0 && op.column === 1)).toBe(false);
    expect(highlight_ops.some((op) => op.row === 0 && op.col === 1)).toBe(false);
  });
});

describe('xlsx import rich text and other cell objects', () => {
  it('flattens rich text to the visible characters', async () => {
    const { matrix } = await parse((sheet) => {
      sheet.getCell('A1').value = {
        richText: [{ text: 'Hello ' }, { font: { bold: true }, text: 'world' }],
      };
    });
    expect(matrix[0][0]).toBe('Hello world');
  });

  it('keeps an error value as text', async () => {
    const { matrix } = await parse((sheet) => {
      sheet.getCell('A1').value = { error: '#DIV/0!' };
    });
    expect(matrix[0][0]).toBe('#DIV/0!');
  });
});

describe('parseNumberFormat currencies', () => {
  it('keeps the four supported currencies and does not treat a dong token as USD', () => {
    expect(parseNumberFormat('$#,##0.00')).toEqual({ type: 'CURRENCY', currency_code: 'USD', decimal_places: 2 });
    expect(parseNumberFormat('[$$-409]#,##0.00')).toEqual({ type: 'CURRENCY', currency_code: 'USD', decimal_places: 2 });
    expect(parseNumberFormat('£#,##0.00')).toEqual({ type: 'CURRENCY', currency_code: 'GBP', decimal_places: 2 });
    expect(parseNumberFormat('[$£-809]#,##0.00')).toEqual({ type: 'CURRENCY', currency_code: 'GBP', decimal_places: 2 });
    expect(parseNumberFormat('€#,##0.00')).toEqual({ type: 'CURRENCY', currency_code: 'EUR', decimal_places: 2 });
    expect(parseNumberFormat('[$€-407]#,##0.00')).toEqual({ type: 'CURRENCY', currency_code: 'EUR', decimal_places: 2 });
    expect(parseNumberFormat('¥#,##0')).toEqual({ type: 'CURRENCY', currency_code: 'JPY', decimal_places: 0 });
    expect(parseNumberFormat('[$¥-411]#,##0')).toEqual({ type: 'CURRENCY', currency_code: 'JPY', decimal_places: 0 });
    expect(parseNumberFormat('[$¥]#,##0')).toEqual({ type: 'CURRENCY', currency_code: 'JPY', decimal_places: 0 });
    expect(parseNumberFormat('[$$-C09]#,##0.00')).toBeNull();
    expect(parseNumberFormat('[$$-1009]#,##0.0')).toBeNull();
    expect(parseNumberFormat('"AUD" #,##0.00')).toBeNull();
    expect(parseNumberFormat('[$₫]#,##0')).toBeNull();
    expect(parseNumberFormat('[$₫-42A]#,##0')).toBeNull();
  });

  it('keeps a dong amount as a number and drops the currency format', async () => {
    const { matrix, format_ops } = await parse((sheet) => {
      sheet.getCell('A1').value = 80;
      sheet.getCell('A1').numFmt = '[$₫]#,##0';
    });
    expect(matrix[0][0]).toBe('80');
    expect(formatAt(format_ops, 0, 0)?.number_format ?? null).toBeNull();
  });
});