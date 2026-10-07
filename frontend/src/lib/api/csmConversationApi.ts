import api from '../api';
import {
  Conversation,
  ConversationDetail,
  ConversationMessage,
  SendMessagePayload,
  CreateTicketPayload,
  UpdateConversationPayload,
  QuickReplyTemplate,
  QuickReplyTemplatePayload,
  QuickReplyTemplateHistory,
  TemplateTag,
  TemplatePreviewTeam,
  Ticket,
  AssignableAgent,
} from '@/types/csmConversation';
import { Queue } from '@/types/csm';

const BASE = '/api/csm/conversations';

function unwrap<T>(data: unknown): T[] {
  return Array.isArray(data) ? data : ((data as { results?: T[] })?.results ?? []);
}

export default class CsmConversationAPI {
  static async list(params?: { queue?: number | null }): Promise<Conversation[]> {
    const query = params?.queue ? { queue: params.queue } : undefined;
    const res = await api.get(`${BASE}/`, { params: query });
    return unwrap<Conversation>(res.data);
  }

  static async get(conversationId: number): Promise<ConversationDetail> {
    const res = await api.get<ConversationDetail>(`${BASE}/${conversationId}/`);
    return res.data;
  }

  static async create(data: Partial<Conversation>): Promise<Conversation> {
    const res = await api.post<Conversation>(`${BASE}/`, data);
    return res.data;
  }

  static async update(conversationId: number, data: UpdateConversationPayload): Promise<Conversation> {
    const res = await api.patch<Conversation>(`${BASE}/${conversationId}/`, data);
    return res.data;
  }

  static async claim(conversationId: number): Promise<Ticket> {
    const res = await api.post<Ticket>(`${BASE}/${conversationId}/claim/`);
    return res.data;
  }

  static async assignableAgents(conversationId: number): Promise<AssignableAgent[]> {
    const res = await api.get<AssignableAgent[]>(`${BASE}/${conversationId}/assignable_agents/`);
    return Array.isArray(res.data) ? res.data : [];
  }

  static async availableQueues(params?: { organisation?: number }): Promise<Queue[]> {
    const res = await api.get(`${BASE}/available_queues/`, { params });
    return unwrap<Queue>(res.data);
  }

  static async sendMessage(
    conversationId: number,
    payload: SendMessagePayload & { image?: File | null }
  ): Promise<ConversationMessage> {
    if (payload.image) {
      const form = new FormData();
      if (payload.content) form.append('content', payload.content);
      form.append('image', payload.image);
      const res = await api.post<ConversationMessage>(`${BASE}/${conversationId}/messages/`, form);
      return res.data;
    }
    const res = await api.post<ConversationMessage>(`${BASE}/${conversationId}/messages/`, payload);
    return res.data;
  }

  static async createTicket(
    conversationId: number,
    payload: CreateTicketPayload
  ): Promise<{ ticket: object; system_message: ConversationMessage }> {
    const res = await api.post(`${BASE}/${conversationId}/create_ticket/`, payload);
    return res.data;
  }
}

const TMPL_BASE = '/api/csm/templates';

export class TicketAPI {
  static async list(params?: {
    queue?: number;
    status?: string;
    assigned_to?: string;
    ordering?: 'priority' | '-priority' | string;
  }): Promise<Ticket[]> {
    const res = await api.get('/api/csm/tickets/', { params });
    return Array.isArray(res.data) ? res.data : (res.data?.results ?? []);
  }

  static async listByQueue(queueId: number, status?: string, ordering?: string): Promise<Ticket[]> {
    return TicketAPI.list({ queue: queueId, ...(status ? { status } : {}), ...(ordering ? { ordering } : {}) });
  }

  static async myTickets(ordering?: string): Promise<Ticket[]> {
    return TicketAPI.list({ assigned_to: 'me', ...(ordering ? { ordering } : {}) });
  }

  static async get(id: number): Promise<Ticket> {
    const res = await api.get<Ticket>(`/api/csm/tickets/${id}/`);
    return res.data;
  }

  static async claim(id: number): Promise<Ticket> {
    const res = await api.post<Ticket>(`/api/csm/tickets/${id}/claim/`);
    return res.data;
  }

  static async close(id: number): Promise<Ticket> {
    const res = await api.post<Ticket>(`/api/csm/tickets/${id}/close/`);
    return res.data;
  }

  static async update(id: number, data: Partial<Ticket>): Promise<Ticket> {
    const res = await api.patch<Ticket>(`/api/csm/tickets/${id}/`, data);
    return res.data;
  }
}

export class QuickReplyTemplateAPI {
  static async list(params?: {
    organisation?: number;
    tag?: string;
    search?: string;
    // CSM admins only: list templates as an agent in this team sees them.
    view_as_team?: number | 'none';
  }): Promise<QuickReplyTemplate[]> {
    const res = await api.get(`${TMPL_BASE}/`, { params });
    return Array.isArray(res.data) ? res.data : (res.data?.results ?? []);
  }

  static async previewTeams(organisation: number): Promise<TemplatePreviewTeam[]> {
    const res = await api.get<TemplatePreviewTeam[]>(`${TMPL_BASE}/preview-teams/`, {
      params: { organisation },
    });
    return Array.isArray(res.data) ? res.data : [];
  }

  static async get(slug: string): Promise<QuickReplyTemplate> {
    const res = await api.get<QuickReplyTemplate>(`${TMPL_BASE}/${slug}/`);
    return res.data;
  }

  static async create(payload: QuickReplyTemplatePayload): Promise<QuickReplyTemplate> {
    const res = await api.post<QuickReplyTemplate>(`${TMPL_BASE}/`, payload);
    return res.data;
  }

  static async update(slug: string, payload: Partial<QuickReplyTemplatePayload>): Promise<QuickReplyTemplate> {
    const res = await api.patch<QuickReplyTemplate>(`${TMPL_BASE}/${slug}/`, payload);
    return res.data;
  }

  static async remove(slug: string): Promise<void> {
    await api.delete(`${TMPL_BASE}/${slug}/`);
  }

  static async history(slug: string): Promise<QuickReplyTemplateHistory[]> {
    const res = await api.get<QuickReplyTemplateHistory[]>(`${TMPL_BASE}/${slug}/history/`);
    return Array.isArray(res.data) ? res.data : [];
  }
}

const TAG_BASE = '/api/csm/template-tags';

export class TemplateTagAPI {
  static async list(params?: { organisation?: number }): Promise<TemplateTag[]> {
    const res = await api.get(`${TAG_BASE}/`, { params });
    return Array.isArray(res.data) ? res.data : (res.data?.results ?? []);
  }

  static async create(payload: { organisation: number; name: string }): Promise<TemplateTag> {
    const res = await api.post<TemplateTag>(`${TAG_BASE}/`, payload);
    return res.data;
  }

  static async remove(id: number): Promise<void> {
    await api.delete(`${TAG_BASE}/${id}/`);
  }
}
