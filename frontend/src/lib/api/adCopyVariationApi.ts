import axios from 'axios';
import api from '@/lib/api';
import type {
  AdCopyVariation,
  AdCopyVariationCopy,
  AdCopyVariationSourceMode,
  AdCopyVariationStatus,
  BatchGenerateResponse,
  GenerateVariationRequest,
  SaveVariationRequest,
} from '@/types/adCopyVariation';

const BASE = '/api/ad_copy_variation/variations';

/** Generate waits on Vertex; the shared axios client is 10s and is too short. */
const GENERATE_TIMEOUT_MS = 120_000;

function parseBatchGenerateResponse(data: unknown): BatchGenerateResponse | null {
  if (!data || typeof data !== 'object') return null;
  const candidate = data as {
    batch_id?: unknown;
    count_requested?: unknown;
    count_succeeded?: unknown;
    count_failed?: unknown;
    results?: unknown;
    failed_indices?: unknown;
    error?: unknown;
  };
  if (typeof candidate.batch_id !== 'string') return null;
  if (typeof candidate.count_requested !== 'number') return null;
  if (typeof candidate.count_succeeded !== 'number') return null;
  if (typeof candidate.count_failed !== 'number') return null;
  if (!Array.isArray(candidate.results)) return null;
  if (!Array.isArray(candidate.failed_indices)) return null;
  if (
    !candidate.results.every((row) => {
      if (typeof row !== 'object' || row === null) return false;
      const draft = row as { id?: unknown; status?: unknown };
      return typeof draft.id === 'number' && typeof draft.status === 'string';
    })
  ) {
    return null;
  }
  return data as BatchGenerateResponse;
}

export async function generateVariation(
  req: GenerateVariationRequest
): Promise<BatchGenerateResponse> {
  const body = { ...req, count: req.count ?? 1 };
  try {
    const { data } = await api.post(`${BASE}/generate/`, body, {
      timeout: GENERATE_TIMEOUT_MS,
    });
    const parsed = parseBatchGenerateResponse(data);
    if (parsed) return parsed;
  } catch (err) {
    if (axios.isAxiosError(err)) {
      const parsed = parseBatchGenerateResponse(err.response?.data);
      if (parsed) return parsed;
    }
    throw err;
  }
  throw new Error(
    'Generate response did not include persisted draft IDs. Please restart the backend and ensure migrations are applied.'
  );
}

export async function saveVariation(
  req: SaveVariationRequest
): Promise<AdCopyVariation> {
  const { data } = await api.post<AdCopyVariation>(`${BASE}/`, req);
  return data;
}

export async function bulkSaveVariations(
  items: SaveVariationRequest[]
): Promise<{ saved: AdCopyVariation[]; failedIndices: number[] }> {
  const saved: AdCopyVariation[] = new Array(items.length) as AdCopyVariation[];
  const failedIndices: number[] = [];
  await Promise.all(
    items.map(async (item, idx) => {
      try {
        const v = await saveVariation(item);
        saved[idx] = v;
      } catch (_err) {
        failedIndices.push(idx);
      }
    })
  );
  const cleanSaved = saved.filter((v): v is AdCopyVariation => Boolean(v));
  failedIndices.sort((a, b) => a - b);
  return { saved: cleanSaved, failedIndices };
}

export interface ListVariationsResult {
  results: AdCopyVariation[];
  total: number;
  page?: number;
  pageSize?: number;
}

export interface ListAiVariationsParams {
  project_id?: number | string;
  creative?: number;
  status?: AdCopyVariationStatus | AdCopyVariationStatus[] | string;
  source_mode?: AdCopyVariationSourceMode | '';
  batch_id?: string;
  page?: number;
  page_size?: number;
}

export async function listAiVariations(
  params: ListAiVariationsParams
): Promise<ListVariationsResult> {
  const normalized: Record<string, string | number> = {};
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return;
    normalized[key] = Array.isArray(value) ? value.join(',') : value;
  });
  const { data } = await api.get(`${BASE}/`, { params: normalized });
  if (Array.isArray(data)) {
    return { results: data as AdCopyVariation[], total: data.length };
  }
  if (data && Array.isArray((data as { results?: unknown }).results)) {
    const paged = data as {
      results: AdCopyVariation[];
      count?: number;
      page?: number;
      page_size?: number;
    };
    return {
      results: paged.results,
      total: typeof paged.count === 'number' ? paged.count : paged.results.length,
      page: paged.page,
      pageSize: paged.page_size,
    };
  }
  return { results: [], total: 0 };
}

export async function getLatestVariationBatch(projectId: number | string): Promise<{
  batch_id: string | null;
  count: number;
  results: AdCopyVariation[];
}> {
  const { data } = await api.get(`${BASE}/latest_batch/`, {
    params: { project_id: projectId },
  });
  return data;
}

export async function reviewVariationBatch(req: {
  project_id: number | string;
  batch_id: string;
  selected_ids: number[];
}): Promise<{
  batch_id: string;
  reviewed_count: number;
  deleted_count: number;
  results: AdCopyVariation[];
}> {
  const { data } = await api.post(`${BASE}/review_batch/`, req);
  return data;
}

export async function bulkReviewVariations(req: {
  project_id: number | string;
  selected_ids: number[];
}): Promise<{
  reviewed_count: number;
  results: AdCopyVariation[];
}> {
  const { data } = await api.post(`${BASE}/bulk_review/`, req);
  return data;
}

export async function bulkDeleteVariations(req: {
  project_id: number | string;
  selected_ids: number[];
  status: AdCopyVariationStatus;
}): Promise<{
  deleted_count: number;
  deleted_ids: number[];
}> {
  const { data } = await api.post(`${BASE}/bulk_delete/`, req);
  return data;
}

export async function getVariation(idOrSlug: number | string): Promise<AdCopyVariation> {
  const { data } = await api.get(`${BASE}/${idOrSlug}/`);
  return data as AdCopyVariation;
}

export async function updateVariation(
  id: number | string,
  fields: Partial<AdCopyVariationCopy & Pick<AdCopyVariation, 'status'>>
): Promise<AdCopyVariation> {
  const { data } = await api.patch<AdCopyVariation>(`${BASE}/${id}/`, fields);
  return data;
}

export async function deleteVariation(id: number | string): Promise<void> {
  await api.delete(`${BASE}/${id}/`);
}
