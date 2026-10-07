import api from '../api';
import type {
  Meeting,
  MeetingStatus,
  MeetingCreateRequest,
  MeetingUpdateRequest,
  MeetingPartialUpdateRequest,
  AgendaItem,
  AgendaItemCreateRequest,
  AgendaItemUpdateRequest,
  AgendaItemPartialUpdateRequest,
  AgendaItemsReorderRequest,
  ParticipantLink,
  ParticipantLinkCreateRequest,
  ParticipantLinkPartialUpdateRequest,
  ArtifactLink,
  ArtifactLinkCreateRequest,
  MeetingListQueryParams,
  MeetingListItem,
  MeetingListParticipant,
  MeetingListTag,
  KnowledgeNavigationLink,
  PaginatedMeetingsList,
  MeetingDocument,
  MeetingActionItem,
  MeetingActionItemCreateRequest,
  MeetingActionItemPartialUpdateRequest,
  ConvertActionItemToTaskRequest,
  BulkConvertActionItemsRequest,
  ZoomPostMeeting,
  ZoomPostMeetingUserFeedbackCode,
} from '@/types/meeting';
import type { TaskData } from '@/types/task';

const basePath = (projectId: number | string) => `/api/projects/${projectId}/meetings`;

type MeetingTemplate = {
  id: string;
  name: string;
  layout_config: unknown;
};

function buildMeetingListParams(
  params?: MeetingListQueryParams,
): Record<string, string | number | number[] | string[]> {
  if (!params) return {};
  const q: Record<string, string | number | number[] | string[]> = {};
  if (params.q?.trim()) q.q = params.q.trim();
  if (params.meeting_type?.length) {
    q.meeting_type = params.meeting_type.map((s) => s.trim()).filter(Boolean);
  }
  if (params.tag?.trim()) q.tag = params.tag.trim();
  if (params.participant?.length) {
    q.participant = params.participant.filter((id) => Number.isFinite(id) && id >= 1);
  }
  if (params.exclude_participant?.length) {
    q.exclude_participant = params.exclude_participant.filter(
      (id) => Number.isFinite(id) && id >= 1,
    );
  }
  if (params.date_from) q.date_from = params.date_from;
  if (params.date_to) q.date_to = params.date_to;
  if (params.is_archived === true) q.is_archived = 'true';
  if (params.is_archived === false) q.is_archived = 'false';
  if (params.has_generated_decisions === true) q.has_generated_decisions = 'true';
  if (params.has_generated_decisions === false) q.has_generated_decisions = 'false';
  if (params.has_generated_tasks === true) q.has_generated_tasks = 'true';
  if (params.has_generated_tasks === false) q.has_generated_tasks = 'false';
  if (params.ordering) q.ordering = params.ordering;
  if (params.page != null && params.page >= 1) q.page = params.page;
  return q;
}

function normalizeKnowledgeLinks(raw: unknown): KnowledgeNavigationLink[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((entry) => entry as Record<string, unknown>)
    .filter((x) => {
      const id = Number(x?.id);
      const detailUrlRaw = x?.detail_url ?? x?.detailUrl;
      const urlRaw = x?.url;
      const link =
        typeof detailUrlRaw === 'string'
          ? String(detailUrlRaw)
          : typeof urlRaw === 'string'
            ? String(urlRaw)
            : '';
      return x != null && Number.isFinite(id) && link.length > 0;
    })
    .map((x) => {
      const detailUrlRaw = x.detail_url ?? x.detailUrl;
      const urlRaw = x.url;
      const detailUrl =
        typeof detailUrlRaw === 'string'
          ? String(detailUrlRaw)
          : typeof urlRaw === 'string'
            ? String(urlRaw)
            : '';
      const titleRaw = x.title;
      const title =
        typeof titleRaw === 'string' && titleRaw.trim().length > 0
          ? titleRaw
          : `Item ${Number(x.id)}`;
      const out: KnowledgeNavigationLink = {
        id: Number(x.id),
        title,
        url: detailUrl,
      };
      if (typeof detailUrlRaw === 'string') out.detail_url = detailUrlRaw;
      if (typeof x.status === 'string') out.status = x.status;
      if (x.assignee_name === null || typeof x.assignee_name === 'string') {
        out.assignee_name = x.assignee_name;
      }
      return out;
    });
}

const ZOOM_FEEDBACK_CODES = new Set<string>([
  'auth_expired',
  'pending',
  'not_applicable',
  'unavailable',
  'error',
]);

function normalizeZoomPostMeeting(raw: unknown): ZoomPostMeeting | null {
  if (raw === null || raw === undefined) return null;
  if (typeof raw !== 'object') return null;
  const o = raw as Record<string, unknown>;
  const codeRaw = o.user_feedback_code;
  const user_feedback_code: ZoomPostMeetingUserFeedbackCode | null =
    typeof codeRaw === 'string' && ZOOM_FEEDBACK_CODES.has(codeRaw)
      ? (codeRaw as ZoomPostMeetingUserFeedbackCode)
      : null;

  const participantsIn = o.participants;
  const participants = Array.isArray(participantsIn)
    ? participantsIn.map((row) => {
        const p = row as Record<string, unknown>;
        return {
          name: p.name === null || typeof p.name === 'string' ? (p.name as string | null) : null,
          email:
            p.email === null || typeof p.email === 'string' ? (p.email as string | null) : null,
        };
      })
    : [];

  const filesIn = o.recording_files;
  const recording_files = Array.isArray(filesIn)
    ? filesIn.map((row) => {
        const f = row as Record<string, unknown>;
        return {
          file_type: f.file_type as string | null | undefined,
          recording_type: f.recording_type as string | null | undefined,
          play_url: f.play_url as string | null | undefined,
          download_url: f.download_url as string | null | undefined,
        };
      })
    : [];

  return {
    meeting_status: typeof o.meeting_status === 'string' ? o.meeting_status : '',
    start_time:
      o.start_time === null || typeof o.start_time === 'string' ? (o.start_time as string | null) : null,
    end_time:
      o.end_time === null || typeof o.end_time === 'string' ? (o.end_time as string | null) : null,
    duration_minutes:
      o.duration_minutes === null || typeof o.duration_minutes === 'number'
        ? (o.duration_minutes as number | null)
        : null,
    actual_participants_count:
      o.actual_participants_count === null || typeof o.actual_participants_count === 'number'
        ? (o.actual_participants_count as number | null)
        : null,
    recording_status: typeof o.recording_status === 'string' ? o.recording_status : '',
    summary_status: typeof o.summary_status === 'string' ? o.summary_status : '',
    sync_state: typeof o.sync_state === 'string' ? o.sync_state : '',
    sync_error: typeof o.sync_error === 'string' ? o.sync_error : '',
    last_sync_at:
      o.last_sync_at === null || typeof o.last_sync_at === 'string'
        ? (o.last_sync_at as string | null)
        : null,
    has_participant_breakdown: Boolean(o.has_participant_breakdown),
    participant_breakdown_count:
      typeof o.participant_breakdown_count === 'number' ? o.participant_breakdown_count : 0,
    has_transcript_asset: Boolean(o.has_transcript_asset),
    recording_file_count:
      typeof o.recording_file_count === 'number' ? o.recording_file_count : 0,
    summary_text: typeof o.summary_text === 'string' ? o.summary_text : '',
    participants,
    recording_files,
    user_feedback_code,
  };
}

/** Detail/create/update payloads: stable generated + related navigation arrays. */
function withMeetingKnowledgeFields(meeting: Meeting): Meeting {
  const raw = meeting as unknown as Record<string, unknown>;
  const genDec = raw.generated_decisions ?? raw.generatedDecisions;
  const genTasks = raw.generated_tasks ?? raw.generatedTasks;
  const relDec = raw.related_decisions ?? raw.relatedDecisions;
  const relTasks = raw.related_tasks ?? raw.relatedTasks;
  const zpm = raw.zoom_post_meeting ?? raw.zoomPostMeeting;
  return {
    ...meeting,
    generated_decisions: normalizeKnowledgeLinks(genDec),
    generated_tasks: normalizeKnowledgeLinks(genTasks),
    related_decisions: normalizeKnowledgeLinks(relDec),
    related_tasks: normalizeKnowledgeLinks(relTasks),
    zoom_post_meeting:
      zpm === undefined ? meeting.zoom_post_meeting : normalizeZoomPostMeeting(zpm),
  };
}

function normalizeMeetingActionItemsResponse(data: unknown): MeetingActionItem[] {
  if (Array.isArray(data)) return data as MeetingActionItem[];
  if (data && typeof data === 'object' && Array.isArray((data as Record<string, unknown>).results)) {
    return (data as { results: MeetingActionItem[] }).results;
  }
  return [];
}

function normalizeMeetingTasksResponse(data: unknown): TaskData[] {
  if (Array.isArray(data)) return data as TaskData[];
  if (data && typeof data === 'object' && Array.isArray((data as Record<string, unknown>).results)) {
    return (data as { results: TaskData[] }).results;
  }
  return [];
}

function normalizeMeetingListItem(raw: Record<string, unknown>): MeetingListItem {
  const participants = raw.participants;
  const tags = raw.tags;
  return {
    id: Number(raw.id),
    slug: typeof raw.slug === 'string' ? raw.slug : null,
    title: typeof raw.title === 'string' ? raw.title : '',
    summary: typeof raw.summary === 'string' ? raw.summary : '',
    scheduled_date:
      raw.scheduled_date === null || raw.scheduled_date === undefined
        ? null
        : String(raw.scheduled_date),
    scheduled_time:
      raw.scheduled_time === null || raw.scheduled_time === undefined
        ? null
        : String(raw.scheduled_time),
    meeting_type: typeof raw.meeting_type === 'string' ? raw.meeting_type : '',
    meeting_type_slug:
      typeof raw.meeting_type_slug === 'string' ? raw.meeting_type_slug : '',
    participants: Array.isArray(participants)
      ? (participants as MeetingListParticipant[])
      : [],
    tags: Array.isArray(tags) ? (tags as MeetingListTag[]) : [],
    decision_count:
      typeof raw.decision_count === 'number' ? raw.decision_count : 0,
    task_count: typeof raw.task_count === 'number' ? raw.task_count : 0,
    generated_decisions_count:
      typeof raw.generated_decisions_count === 'number'
        ? raw.generated_decisions_count
        : typeof raw.decision_count === 'number'
          ? raw.decision_count
          : 0,
    generated_tasks_count:
      typeof raw.generated_tasks_count === 'number'
        ? raw.generated_tasks_count
        : typeof raw.task_count === 'number'
          ? raw.task_count
          : 0,
    generated_decisions: normalizeKnowledgeLinks(
      raw.generated_decisions ?? raw.generatedDecisions,
    ),
    generated_tasks: normalizeKnowledgeLinks(raw.generated_tasks ?? raw.generatedTasks),
    related_decisions: normalizeKnowledgeLinks(
      raw.related_decisions ?? raw.relatedDecisions,
    ),
    related_tasks: normalizeKnowledgeLinks(raw.related_tasks ?? raw.relatedTasks),
    is_archived: Boolean(raw.is_archived),
    status: typeof raw.status === 'string' ? raw.status as MeetingStatus : 'draft',
    search_snippet: typeof raw.search_snippet === 'string' ? raw.search_snippet : undefined,
    snippet_source: (raw.snippet_source === 'transcript' || raw.snippet_source === 'summary' || raw.snippet_source === 'title')
      ? raw.snippet_source
      : undefined,
  };
}

export const MeetingsAPI = {
  /**
   * Paginated knowledge-discovery list (strict filters). Prefer for meetings hub list UI.
   */
  async listMeetingsPaginated(
    projectId: number | string,
    params?: MeetingListQueryParams,
  ): Promise<PaginatedMeetingsList> {
    const response = await api.get(`${basePath(projectId)}/`, {
      params: buildMeetingListParams(params),
    });
    const raw = response.data;
    /** Paginated `{ count, results, ... }`, bare array, or `{ data: { ... } }` envelope. */
    let data: Record<string, unknown>;
    let rawResults: unknown[];
    if (Array.isArray(raw)) {
      data = {};
      rawResults = raw;
    } else if (raw && typeof raw === 'object') {
      const top = raw as Record<string, unknown>;
      const inner = top.data;
      if (
        inner &&
        typeof inner === 'object' &&
        !Array.isArray(inner) &&
        Array.isArray((inner as Record<string, unknown>).results)
      ) {
        data = inner as Record<string, unknown>;
      } else {
        data = top;
      }
      rawResults = Array.isArray(data.results) ? data.results : [];
    } else {
      data = {};
      rawResults = [];
    }

    /** Hub counts: tolerate string numbers and camelCase (proxies / older clients). */
    const parseHubCount = (v: unknown): number | undefined => {
      if (typeof v === 'number' && Number.isFinite(v)) return v;
      if (typeof v === 'string' && v.trim() !== '') {
        const n = Number(v);
        if (Number.isFinite(n)) return n;
      }
      return undefined;
    };
    const pick = (snake: string, camel: string) =>
      parseHubCount(data[snake]) ?? parseHubCount(data[camel]);

    if (process.env.NODE_ENV !== 'production' && rawResults.length > 0) {
      const hasHub =
        data.incoming_lane_total != null ||
        data.incoming_result_count != null ||
        data.incomingLaneTotal != null ||
        data.incomingResultCount != null;
      if (!hasHub) {
        console.warn('[meetingsApi] list JSON missing hub lane keys', {
          topKeys:
            raw && typeof raw === 'object' && !Array.isArray(raw)
              ? Object.keys(raw as object).slice(0, 30)
              : [],
          envelopeKeys: Object.keys(data).slice(0, 30),
        });
      }
    }

    return {
      count: Array.isArray(raw)
        ? raw.length
        : typeof data.count === 'number'
          ? data.count
          : 0,
      next: (data.next as string | null | undefined) ?? null,
      previous: (data.previous as string | null | undefined) ?? null,
      results: rawResults.map((row) =>
        normalizeMeetingListItem(row as unknown as Record<string, unknown>),
      ),
      incomingLaneTotal: pick('incoming_lane_total', 'incomingLaneTotal'),
      incomingResultCount:
        pick('incoming_result_count', 'incomingResultCount') ??
        pick('incoming_lane_filtered', 'incomingLaneFiltered'),
      completedLaneTotal: pick('completed_lane_total', 'completedLaneTotal'),
      completedResultCount:
        pick('completed_result_count', 'completedResultCount') ??
        pick('completed_lane_filtered', 'completedLaneFiltered'),
    };
  },

  async createMeeting(projectId: number | string, payload: MeetingCreateRequest): Promise<Meeting> {
    const response = await api.post<Meeting>(`${basePath(projectId)}/`, payload);
    return withMeetingKnowledgeFields(response.data);
  },

  async getMeeting(projectId: number | string, meetingId: number | string): Promise<Meeting> {
    const response = await api.get<Meeting>(`${basePath(projectId)}/${meetingId}/`);
    return withMeetingKnowledgeFields(response.data);
  },

  async updateMeeting(
    projectId: number | string,
    meetingId: number | string,
    payload: MeetingUpdateRequest,
  ): Promise<Meeting> {
    const response = await api.put<Meeting>(`${basePath(projectId)}/${meetingId}/`, payload);
    return withMeetingKnowledgeFields(response.data);
  },

  async patchMeeting(
    projectId: number | string,
    meetingId: number | string,
    payload: MeetingPartialUpdateRequest,
  ): Promise<Meeting> {
    const response = await api.patch<Meeting>(`${basePath(projectId)}/${meetingId}/`, payload);
    return withMeetingKnowledgeFields(response.data);
  },

  async deleteMeeting(projectId: number | string, meetingId: number | string): Promise<void> {
    await api.delete(`${basePath(projectId)}/${meetingId}/`);
  },

  async listAgendaItems(
    projectId: number | string,
    meetingId: number | string,
  ): Promise<AgendaItem[]> {
    const response = await api.get(
      `${basePath(projectId)}/${meetingId}/agenda-items/`,
    );
    const data = response.data as any;
    if (Array.isArray(data)) return data as AgendaItem[];
    if (data && Array.isArray(data.results)) return data.results as AgendaItem[];
    return [];
  },

  async createAgendaItem(
    projectId: number | string,
    meetingId: number | string,
    payload: AgendaItemCreateRequest,
  ): Promise<AgendaItem> {
    const response = await api.post<AgendaItem>(
      `${basePath(projectId)}/${meetingId}/agenda-items/`,
      payload,
    );
    return response.data;
  },

  async updateAgendaItem(
    projectId: number | string,
    meetingId: number | string,
    agendaItemId: number | string,
    payload: AgendaItemUpdateRequest,
  ): Promise<AgendaItem> {
    const response = await api.put<AgendaItem>(
      `${basePath(projectId)}/${meetingId}/agenda-items/${agendaItemId}/`,
      payload,
    );
    return response.data;
  },

  async patchAgendaItem(
    projectId: number | string,
    meetingId: number | string,
    agendaItemId: number | string,
    payload: AgendaItemPartialUpdateRequest,
  ): Promise<AgendaItem> {
    const response = await api.patch<AgendaItem>(
      `${basePath(projectId)}/${meetingId}/agenda-items/${agendaItemId}/`,
      payload,
    );
    return response.data;
  },

  async deleteAgendaItem(
    projectId: number | string,
    meetingId: number | string,
    agendaItemId: number | string,
  ): Promise<void> {
    await api.delete(
      `${basePath(projectId)}/${meetingId}/agenda-items/${agendaItemId}/`,
    );
  },

  async reorderAgendaItems(
    projectId: number | string,
    meetingId: number | string,
    payload: AgendaItemsReorderRequest,
  ): Promise<AgendaItem[]> {
    const response = await api.patch<AgendaItem[]>(
      `${basePath(projectId)}/${meetingId}/agenda-items/reorder/`,
      payload,
    );
    return response.data;
  },

  async listParticipants(
    projectId: number | string,
    meetingId: number | string,
  ): Promise<ParticipantLink[]> {
    const response = await api.get(
      `${basePath(projectId)}/${meetingId}/participants/`,
    );
    const data = response.data as any;
    if (Array.isArray(data)) return data as ParticipantLink[];
    if (data && Array.isArray(data.results)) return data.results as ParticipantLink[];
    return [];
  },

  async addParticipant(
    projectId: number | string,
    meetingId: number | string,
    payload: ParticipantLinkCreateRequest,
  ): Promise<ParticipantLink> {
    const response = await api.post<ParticipantLink>(
      `${basePath(projectId)}/${meetingId}/participants/`,
      payload,
    );
    return response.data;
  },

  async patchParticipant(
    projectId: number | string,
    meetingId: number | string,
    participantLinkId: number | string,
    payload: ParticipantLinkPartialUpdateRequest,
  ): Promise<ParticipantLink> {
    const response = await api.patch<ParticipantLink>(
      `${basePath(projectId)}/${meetingId}/participants/${participantLinkId}/`,
      payload,
    );
    return response.data;
  },

  async removeParticipant(
    projectId: number | string,
    meetingId: number | string,
    participantLinkId: number | string,
  ): Promise<void> {
    await api.delete(
      `${basePath(projectId)}/${meetingId}/participants/${participantLinkId}/`,
    );
  },

  async listArtifacts(
    projectId: number | string,
    meetingId: number | string,
  ): Promise<ArtifactLink[]> {
    const response = await api.get(
      `${basePath(projectId)}/${meetingId}/artifacts/`,
    );
    const data = response.data as any;
    if (Array.isArray(data)) return data as ArtifactLink[];
    if (data && Array.isArray(data.results)) return data.results as ArtifactLink[];
    return [];
  },

  async addArtifact(
    projectId: number | string,
    meetingId: number | string,
    payload: ArtifactLinkCreateRequest,
  ): Promise<ArtifactLink> {
    const response = await api.post<ArtifactLink>(
      `${basePath(projectId)}/${meetingId}/artifacts/`,
      payload,
    );
    return response.data;
  },

  async removeArtifact(
    projectId: number | string,
    meetingId: number | string,
    artifactLinkId: number | string,
  ): Promise<void> {
    await api.delete(
      `${basePath(projectId)}/${meetingId}/artifacts/${artifactLinkId}/`,
    );
  },

  async saveTemplateLayout(templateId: string, layout_config: unknown): Promise<void> {
    await api.patch(`/api/meetings/templates/${templateId}/`, { layout_config });
  },

  async createMeetingTemplate(payload: { name: string; layout_config: unknown }): Promise<MeetingTemplate> {
    const response = await api.post<MeetingTemplate>(`/api/meetings/templates/`, payload);
    return response.data;
  },

  async listMeetingTemplates(): Promise<MeetingTemplate[]> {
    const response = await api.get(`/api/meetings/templates/`);
    const data = response.data as any;
    if (Array.isArray(data)) return data as MeetingTemplate[];
    if (data && Array.isArray(data.results)) return data.results as MeetingTemplate[];
    return [];
  },

  async deleteMeetingTemplate(templateId: string): Promise<void> {
    await api.delete(`/api/meetings/templates/${templateId}/`);
  },

  async saveMeetingLayout(projectId: number | string, meetingId: number | string, layout_config: unknown): Promise<void> {
    await api.patch(`${basePath(projectId)}/${meetingId}/`, { layout_config });
  },

  async getMeetingDocument(projectId: number | string, meetingId: number | string): Promise<MeetingDocument> {
    const response = await api.get<MeetingDocument>(
      `${basePath(projectId)}/${meetingId}/document/`,
    );
    return response.data;
  },

  async saveMeetingDocument(
    projectId: number | string,
    meetingId: number | string,
    payload: { content: string; yjs_state?: string },
  ): Promise<MeetingDocument> {
    const response = await api.patch<MeetingDocument>(
      `${basePath(projectId)}/${meetingId}/document/`,
      payload,
    );
    return response.data;
  },
  async getLifecycle(
  projectId: number | string,
  meetingId: number | string,
): Promise<{ status: string; available_transitions: string[] }> {
  const response = await api.get(
    `${basePath(projectId)}/${meetingId}/lifecycle/`,
  );
  return response.data as { status: string; available_transitions: string[] };
},

async executeTransition(
  projectId: number | string,
  meetingId: number | string,
  toState: string,
): Promise<{ status: string; available_transitions: string[] }> {
  const response = await api.post(
    `${basePath(projectId)}/${meetingId}/lifecycle/transition/`,
    { to_state: toState },
  );
  return response.data as { status: string; available_transitions: string[] };
},

  /** SMP-489: meeting follow-up action items (before task conversion). */
  async listMeetingActionItems(
    projectId: number | string,
    meetingId: number | string,
  ): Promise<MeetingActionItem[]> {
    const response = await api.get(
      `${basePath(projectId)}/${meetingId}/action-items/`,
    );
    return normalizeMeetingActionItemsResponse(response.data);
  },

  async createMeetingActionItem(
    projectId: number | string,
    meetingId: number | string,
    payload: MeetingActionItemCreateRequest,
  ): Promise<MeetingActionItem> {
    const response = await api.post<MeetingActionItem>(
      `${basePath(projectId)}/${meetingId}/action-items/`,
      payload,
    );
    return response.data;
  },

  async patchMeetingActionItem(
    projectId: number | string,
    meetingId: number | string,
    actionItemId: number | string,
    payload: MeetingActionItemPartialUpdateRequest,
  ): Promise<MeetingActionItem> {
    const response = await api.patch<MeetingActionItem>(
      `${basePath(projectId)}/${meetingId}/action-items/${actionItemId}/`,
      payload,
    );
    return response.data;
  },

  async deleteMeetingActionItem(
    projectId: number | string,
    meetingId: number | string,
    actionItemId: number | string,
  ): Promise<void> {
    await api.delete(
      `${basePath(projectId)}/${meetingId}/action-items/${actionItemId}/`,
    );
  },

  async convertMeetingActionItemToTask(
    projectId: number | string,
    meetingId: number | string,
    actionItemId: number | string,
    payload: ConvertActionItemToTaskRequest,
  ): Promise<TaskData> {
    const response = await api.post<TaskData>(
      `${basePath(projectId)}/${meetingId}/action-items/${actionItemId}/convert-to-task/`,
      payload,
    );
    return response.data;
  },

  async bulkConvertMeetingActionItemsToTasks(
    projectId: number | string,
    meetingId: number | string,
    payload: BulkConvertActionItemsRequest,
  ): Promise<TaskData[]> {
    const response = await api.post<{ tasks: TaskData[] }>(
      `${basePath(projectId)}/${meetingId}/action-items/bulk-convert-to-task/`,
      payload,
    );
    return response.data.tasks ?? [];
  },

  /** Tasks with ``MeetingTaskOrigin`` for this meeting (paginated on server). */
  async listMeetingTasks(projectId: number | string, meetingId: number | string): Promise<TaskData[]> {
    const response = await api.get(`${basePath(projectId)}/${meetingId}/tasks/`);
    return normalizeMeetingTasksResponse(response.data);
  },
};
