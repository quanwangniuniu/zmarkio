import CsmQualityAPI, { toQueryParams } from '@/lib/api/csmQualityApi';
import api, { LONG_REQUEST_TIMEOUT_MS } from '@/lib/api';

jest.mock('@/lib/api', () => ({
  __esModule: true,
  default: { get: jest.fn(), post: jest.fn() },
  LONG_REQUEST_TIMEOUT_MS: 300_000,
}));

const mockedApi = api as unknown as { get: jest.Mock; post: jest.Mock };

beforeEach(() => {
  jest.clearAllMocks();
});

describe('toQueryParams', () => {
  it('keeps arrays as arrays so axios serialises repeated keys', () => {
    const params = toQueryParams({ agent: [1, 2, 'unassigned'], tag: ['vip'] });

    expect(params.agent).toEqual([1, 2, 'unassigned']);
    expect(params.tag).toEqual(['vip']);
  });

  it('drops empty arrays, blank strings and nullish values', () => {
    const params = toQueryParams({
      agent: [],
      tag: [],
      customer_search: '',
      date_from: undefined,
      date_to: '2026-03-31',
    });

    expect(params).toEqual({ date_to: '2026-03-31' });
  });

  it('merges extra params but still drops blanks', () => {
    expect(toQueryParams({ queue: [7] }, { page: 2, page_size: undefined })).toEqual({
      queue: [7],
      page: 2,
    });
  });
});

describe('listConversations', () => {
  it('unwraps a paginated payload and reports the total count', async () => {
    mockedApi.get.mockResolvedValue({ data: { results: [{ id: 1 }], count: 42 }, headers: {} });

    const page = await CsmQualityAPI.listConversations({ channel: ['email'] }, 3, 25);

    expect(mockedApi.get).toHaveBeenCalledWith('/api/csm/quality/conversations/', {
      params: { channel: ['email'], page: 3, page_size: 25 },
    });
    expect(page.results).toEqual([{ id: 1 }]);
    expect(page.count).toBe(42);
  });

  it('accepts a bare array payload and falls back to its length', async () => {
    mockedApi.get.mockResolvedValue({ data: [{ id: 1 }, { id: 2 }], headers: {} });

    const page = await CsmQualityAPI.listConversations({}, 1);

    expect(page.results).toHaveLength(2);
    expect(page.count).toBe(2);
  });
});

describe('saveReview', () => {
  it('posts the rating and comment to the review action', async () => {
    mockedApi.post.mockResolvedValue({ data: { id: 5, rating: 'poor' } });

    const review = await CsmQualityAPI.saveReview(12, 'poor', 'Missed the policy.');

    expect(mockedApi.post).toHaveBeenCalledWith(
      '/api/csm/quality/conversations/12/review/',
      { rating: 'poor', comment: 'Missed the policy.' },
    );
    expect(review.rating).toBe('poor');
  });
});

describe('exportCsv', () => {
  it('requests a blob with the long timeout and reads the filename from the header', async () => {
    const blob = new Blob(['Section,Key']);
    mockedApi.get.mockResolvedValue({
      data: blob,
      headers: { 'content-disposition': 'attachment; filename="quality-inspection-x.csv"' },
    });

    const result = await CsmQualityAPI.exportCsv({ channel: ['email'] });

    expect(mockedApi.get).toHaveBeenCalledWith('/api/csm/quality/report/export.csv/', {
      params: { channel: ['email'] },
      responseType: 'blob',
      timeout: LONG_REQUEST_TIMEOUT_MS,
    });
    expect(result.filename).toBe('quality-inspection-x.csv');
    expect(result.blob).toBe(blob);
  });

  it('falls back to a default filename when the header is missing', async () => {
    mockedApi.get.mockResolvedValue({ data: new Blob(), headers: {} });

    const result = await CsmQualityAPI.exportCsv({});

    expect(result.filename).toBe('quality-inspection.csv');
  });
});

describe('getReport and getFilterOptions', () => {
  it('sends the filters to the report endpoint', async () => {
    mockedApi.get.mockResolvedValue({ data: { totals: { reviews: 0 } }, headers: {} });

    await CsmQualityAPI.getReport({ date_from: '2026-03-01', bucket: 'day' });

    expect(mockedApi.get).toHaveBeenCalledWith('/api/csm/quality/report/', {
      params: { date_from: '2026-03-01', bucket: 'day' },
    });
  });

  it('fetches filter options without params', async () => {
    mockedApi.get.mockResolvedValue({ data: { tags: [] }, headers: {} });

    await CsmQualityAPI.getFilterOptions();

    expect(mockedApi.get).toHaveBeenCalledWith('/api/csm/quality/filter-options/');
  });
});
