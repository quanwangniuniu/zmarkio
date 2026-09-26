import api from '../api';
import type {
  ConversationGuidance,
  GuidanceCapabilities,
  GuidanceEntry,
  GuidanceEntryPayload,
} from '@/types/csmGuidance';

const BASE = '/api/csm/guidance';

function unwrap<T>(data: unknown): T[] {
  return Array.isArray(data) ? data : ((data as { results?: T[] })?.results ?? []);
}

/** The server rejected a write because another admin changed the data first. */
export function isGuidanceConflict(err: unknown): boolean {
  return (err as { response?: { status?: number } })?.response?.status === 409;
}

export default class CsmGuidanceAPI {
  /** All entries in a project, or one Experience Group's entries in display order. */
  static async list(projectId: number, experienceGroupId?: number | null): Promise<GuidanceEntry[]> {
    const params: Record<string, number> = { project: projectId };
    if (experienceGroupId != null) params.experience_group = experienceGroupId;
    const res = await api.get(`${BASE}/`, { params });
    return unwrap<GuidanceEntry>(res.data);
  }

  /** Entries whose Experience Groups were all deleted. */
  static async listUnassigned(projectId: number): Promise<GuidanceEntry[]> {
    const res = await api.get(`${BASE}/`, { params: { project: projectId, unassigned: true } });
    return unwrap<GuidanceEntry>(res.data);
  }

  static async create(projectId: number, data: GuidanceEntryPayload): Promise<GuidanceEntry> {
    const res = await api.post<GuidanceEntry>(`${BASE}/`, data, { params: { project: projectId } });
    return res.data;
  }

  /** ``expectedUpdatedAt`` is the entry's updated_at as last seen; a stale one gets a 409. */
  static async update(
    id: number,
    data: Partial<GuidanceEntryPayload>,
    expectedUpdatedAt?: string,
  ): Promise<GuidanceEntry> {
    const body = expectedUpdatedAt ? { ...data, expected_updated_at: expectedUpdatedAt } : data;
    const res = await api.patch<GuidanceEntry>(`${BASE}/${id}/`, body);
    return res.data;
  }

  static async remove(id: number, expectedUpdatedAt?: string): Promise<void> {
    await api.delete(`${BASE}/${id}/`, {
      params: expectedUpdatedAt ? { expected_updated_at: expectedUpdatedAt } : undefined,
    });
  }

  static async reorder(
    projectId: number,
    experienceGroupId: number,
    ids: number[],
    /** The order shown before the drag; the server returns 409 if it has since changed. */
    expectedIds?: number[],
  ): Promise<GuidanceEntry[]> {
    const res = await api.put<GuidanceEntry[]>(
      `${BASE}/reorder/`,
      { experience_group: experienceGroupId, ids, ...(expectedIds ? { expected_ids: expectedIds } : {}) },
      { params: { project: projectId } },
    );
    return res.data;
  }

  static async capabilities(projectId: number): Promise<GuidanceCapabilities> {
    const res = await api.get<GuidanceCapabilities>(`${BASE}/capabilities/`, {
      params: { project: projectId },
    });
    return res.data;
  }

  /** Agent workspace: guidance for the conversation's matched Experience Group. */
  static async forConversation(conversationId: number): Promise<ConversationGuidance> {
    const res = await api.get<ConversationGuidance>(
      `/api/csm/conversations/${conversationId}/guidance/`,
    );
    return res.data;
  }
}
