import ExcelJS from 'exceljs';
import { parseSheetValues } from '@/components/spreadsheets/xlsx_style_import';

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
  return { matrix: await parseSheetValues(await workbookFile(setup), 'Sheet1') };
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

describe('xlsx import empty cells', () => {
  it('skips empty cells after the last value', async () => {
    const { matrix } = await parse((sheet) => {
      sheet.getCell('A1').value = 'kept';
    });
    expect(matrix[0][0]).toBe('kept');
    expect(matrix[1]).toBeUndefined();
  });

  it('keeps a formatted empty cell in the imported range', async () => {
    const { matrix } = await parse((sheet) => {
      sheet.getCell('A2').value = 'kept';
      sheet.getCell('C4').font = { bold: true };
    });
    expect(matrix[1][0]).toBe('kept');
    expect(matrix[3][2]).toBe('');
  });
});

describe('xlsx import merged cells', () => {
  it('keeps the master value and leaves the merged slave empty', async () => {
    const { matrix } = await parse((sheet) => {
      sheet.mergeCells('A1:B1');
      sheet.getCell('A1').value = 'Hello';
    });
    expect(matrix[0][0]).toBe('Hello');
    expect(matrix[0][1] ?? '').toBe('');
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