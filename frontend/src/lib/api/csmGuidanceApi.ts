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

export default class CsmGuidanceAPI {
  /** All entries in a project, or one Experience Group's entries in display order. */
  static async list(projectId: number, experienceGroupId?: number | null): Promise<GuidanceEntry[]> {
    const params: Record<string, number> = { project: projectId };
    if (experienceGroupId != null) params.experience_group = experienceGroupId;
    const res = await api.get(`${BASE}/`, { params });
    return unwrap<GuidanceEntry>(res.data);
  }

  static async create(projectId: number, data: GuidanceEntryPayload): Promise<GuidanceEntry> {
    const res = await api.post<GuidanceEntry>(`${BASE}/`, data, { params: { project: projectId } });
    return res.data;
  }

  static async update(id: number, data: Partial<GuidanceEntryPayload>): Promise<GuidanceEntry> {
    const res = await api.patch<GuidanceEntry>(`${BASE}/${id}/`, data);
    return res.data;
  }

  static async remove(id: number): Promise<void> {
    await api.delete(`${BASE}/${id}/`);
  }

  static async reorder(
    projectId: number,
    experienceGroupId: number,
    ids: number[],
  ): Promise<GuidanceEntry[]> {
    const res = await api.put<GuidanceEntry[]>(
      `${BASE}/reorder/`,
      { experience_group: experienceGroupId, ids },
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
