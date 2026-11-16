import { googleDocsApi } from '@/lib/api/googleDocsApi';

jest.mock('@/lib/api', () => ({
  __esModule: true,
  SYNC_TIMEOUT_MS: 120000,
  default: {
    get: jest.fn(),
    post: jest.fn(),
    interceptors: {
      request: { use: jest.fn() },
      response: { use: jest.fn() },
    },
  },
}));

import api, { SYNC_TIMEOUT_MS } from '@/lib/api';

const mockApi = api as jest.Mocked<typeof api>;

describe('googleDocsApi sheet export', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('posts the spreadsheet slug and sheet id with the current timeout', async () => {
    mockApi.post.mockResolvedValue({
      data: { spreadsheet_id: 'dest', url: 'https://docs.google.com/spreadsheets/d/dest/edit' },
    } as never);
    const matrix = [['CMP-11000']];

    await googleDocsApi.exportToGoogleSheets('Budget-Sheet1', matrix, 'budget', 119);

    expect(mockApi.post).toHaveBeenCalledWith(
      '/api/google-docs/sheets/export/',
      {
        title: 'Budget-Sheet1',
        matrix,
        spreadsheet_id: 'budget',
        sheet_id: 119,
      },
      { timeout: SYNC_TIMEOUT_MS },
    );
  });
});