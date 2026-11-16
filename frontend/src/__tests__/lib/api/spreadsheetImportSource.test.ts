import { SpreadsheetAPI } from '@/lib/api/spreadsheetApi';

jest.mock('@/lib/api', () => ({
    __esModule: true,
    LONG_REQUEST_TIMEOUT_MS: 300000,
    default: {
      get: jest.fn(),
      post: jest.fn(),
      interceptors: {
        request: { use: jest.fn() },
        response: { use: jest.fn() },
      },
    },
}));

import api from '@/lib/api';

const mockApi = api as jest.Mocked<typeof api>;

describe('SpreadsheetAPI import source', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('posts the excel file as multipart', async () => {
    mockApi.post.mockResolvedValue({ data: { id: 1, origin: 'EXCEL', worksheet_name: 'Budget' } } as any);
    const file = new File([new Uint8Array([1, 2])], 'Book.xlsx');

    await SpreadsheetAPI.saveExcelImportSource(7, 9, file, 'Budget');

    const [url, body] = mockApi.post.mock.calls[0];
    expect(url).toBe('/api/spreadsheet/spreadsheets/7/sheets/9/import-source/');
    expect(body).toBeInstanceOf(FormData);
    expect((body as FormData).get('origin')).toBe('EXCEL');
    expect((body as FormData).get('worksheet_name')).toBe('Budget');
    expect((body as FormData).get('file')).toBe(file);
  });

  it('posts google ids as json, including sheet id 0', async () => {
    mockApi.post.mockResolvedValue({ data: { id: 2, origin: 'GOOGLE', worksheet_name: 'Budget' } } as any);

    await SpreadsheetAPI.saveGoogleImportSource(7, 9, {
      google_spreadsheet_id: 'sheet123',
      google_sheet_id: 0,
      worksheet_name: 'Budget',
      source_url: 'https://docs.google.com/spreadsheets/d/sheet123/edit',
    });

    expect(mockApi.post).toHaveBeenCalledWith(
      '/api/spreadsheet/spreadsheets/7/sheets/9/import-source/',
      {
        origin: 'GOOGLE',
        google_spreadsheet_id: 'sheet123',
        google_sheet_id: 0,
        worksheet_name: 'Budget',
        source_url: 'https://docs.google.com/spreadsheets/d/sheet123/edit',
      },
    );
  });
});