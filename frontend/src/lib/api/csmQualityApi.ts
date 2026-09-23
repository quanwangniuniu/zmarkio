import api, { LONG_REQUEST_TIMEOUT_MS } from '../api';
import { filenameFromContentDisposition } from '../downloadBlob';
import {
  ConversationQualityReview,
  QualityConversationDetail,
  QualityConversationPage,
  QualityConversationRow,
  QualityFilterOptions,
  QualityFilters,
  QualityRating,
  QualityReport,
} from '@/types/csmQuality';

const BASE = '/api/csm/quality';

/**
 * Flatten filters into axios params.
 *
 * Arrays stay arrays: the shared axios instance is configured with
 * `paramsSerializer: { indexes: null }`, so they serialise as repeated keys
 * (`?agent=1&agent=2`), which is what the backend reads with `getlist`.
 * Empty arrays and blank strings are dropped so the URL stays readable.
 */
export function toQueryParams(
  filters: Partial<QualityFilters>,
  extra: Record<string, unknown> = {},
): Record<string, unknown> {
  const params: Record<string, unknown> = {};
  Object.entries(filters).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return;
    if (Array.isArray(value)) {
      if (value.length > 0) params[key] = value;
      return;
    }
    params[key] = value;
  });
  Object.entries(extra).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') params[key] = value;
  });
  return params;
}

function unwrap<T>(data: unknown): T[] {
  return Array.isArray(data) ? data : ((data as { results?: T[] })?.results ?? []);
}

export default class CsmQualityAPI {
  static async listConversations(
    filters: Partial<QualityFilters>,
    page = 1,
    pageSize?: number,
  ): Promise<QualityConversationPage> {
    const res = await api.get(`${BASE}/conversations/`, {
      params: toQueryParams(filters, { page, page_size: pageSize }),
    });
    const results = unwrap<QualityConversationRow>(res.data);
    const count = (res.data as { count?: number })?.count ?? results.length;
    return { results, count };
  }

  static async getConversation(id: number): Promise<QualityConversationDetail> {
    const res = await api.get<QualityConversationDetail>(`${BASE}/conversations/${id}/`);
    return res.data;
  }

  static async saveReview(
    id: number,
    rating: QualityRating,
    comment: string,
  ): Promise<ConversationQualityReview> {
    const res = await api.post<ConversationQualityReview>(
      `${BASE}/conversations/${id}/review/`,
      { rating, comment },
    );
    return res.data;
  }

  static async getReport(filters: Partial<QualityFilters>): Promise<QualityReport> {
    const res = await api.get<QualityReport>(`${BASE}/report/`, {
      params: toQueryParams(filters),
    });
    return res.data;
  }

  /**
   * Option values and their tallies.
   *
   * Takes the current filters so the tallies reflect them; the server leaves
   * out each facet's own filter when counting it.
   */
  static async getFilterOptions(
    filters: Partial<QualityFilters> = {},
  ): Promise<QualityFilterOptions> {
    const res = await api.get<QualityFilterOptions>(`${BASE}/filter-options/`, {
      params: toQueryParams(filters),
    });
    return res.data;
  }

  static async exportCsv(
    filters: Partial<QualityFilters>,
  ): Promise<{ blob: Blob; filename: string }> {
    const res = await api.get<Blob>(`${BASE}/report/export.csv/`, {
      params: toQueryParams(filters),
      responseType: 'blob',
      // The export builds the whole report before streaming; the 10s default
      // is not enough for a wide date range.
      timeout: LONG_REQUEST_TIMEOUT_MS,
    });
    return {
      blob: res.data,
      filename: filenameFromContentDisposition(
        res.headers['content-disposition'] as string | undefined,
        'quality-inspection.csv',
      ),
    };
  }
}
