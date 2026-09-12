import api from '../api';
import type {
  DecisionCommitResponse,
  DecisionCommittedResponse,
  DecisionDraftResponse,
  DecisionOptionDraft,
  DecisionGraphResponse,
  DecisionListResponse,
  DecisionConnectionsResponse,
  DecisionSignal,
  DecisionSignalListResponse,
  DecisionReviewPayload,
} from '@/types/decision';

export interface DecisionDraftPayload {
  title?: string | null;
  contextSummary?: string | null;
  reasoning?: string | null;
  riskLevel?: string | null;
  confidenceScore?: number | null;
  options?: DecisionOptionDraft[];
  topic?: string | null;
  parentDecisionIds?: number[];
}

export interface DecisionSignalPayload {
  metric: DecisionSignal['metric'];
  movement: DecisionSignal['movement'];
  period: DecisionSignal['period'];
  comparison?: DecisionSignal['comparison'];
  scopeType?: DecisionSignal['scopeType'] | null;
  scopeValue?: DecisionSignal['scopeValue'] | null;
  deltaValue?: DecisionSignal['deltaValue'] | null;
  deltaUnit?: DecisionSignal['deltaUnit'] | null;
  displayTextOverride?: DecisionSignal['displayTextOverride'] | null;
}

export interface DecisionTopicLabelResponse {
  topic: string;
  title: string;
  defaultTitle: string;
}

const withProject = (projectId?: number | string | null) => {
  if (!projectId) return {};
  return {
    headers: { 'x-project-id': projectId },
    params: { project_id: projectId },
  };
};

export const DecisionAPI = {
  createDraft: async (
    projectId: number | string,
    body?: { origin_meeting_id?: number },
  ) => {
    const response = await api.post<DecisionDraftResponse>(
      '/api/decisions/drafts/',
      body ?? {},
      withProject(projectId),
    );
    return response.data;
  },
  getDraft: async (decisionId: number | string, projectId?: number | string | null) => {
    const response = await api.get<DecisionDraftResponse>(
      `/api/decisions/drafts/${decisionId}/`,
      withProject(projectId)
    );
    return response.data;
  },
  patchDraft: async (
    decisionId: number | string,
    payload: DecisionDraftPayload,
    projectId?: number | string | null
  ) => {
    const response = await api.patch<DecisionDraftResponse>(
      `/api/decisions/drafts/${decisionId}/`,
      payload,
      withProject(projectId)
    );
    return response.data;
  },
  getDecision: async (decisionId: number | string, projectId?: number | string | null) => {
    const response = await api.get<DecisionCommittedResponse>(
      `/api/decisions/${decisionId}/`,
      withProject(projectId)
    );
    return response.data;
  },
  /** Update content fields on committed/reviewed decisions (tree panel amend). */
  patchDecision: async (
    decisionId: number | string,
    payload: DecisionDraftPayload,
    projectId?: number | string | null,
  ) => {
    const response = await api.patch<DecisionCommittedResponse>(
      `/api/decisions/${decisionId}/`,
      payload,
      withProject(projectId),
    );
    return response.data;
  },
  commit: async (decisionId: number | string, projectId?: number | string | null) => {
    const response = await api.post<DecisionCommitResponse>(
      `/api/decisions/${decisionId}/commit/`,
      {},
      withProject(projectId)
    );
    return response.data;
  },
  createReview: async (
    decisionId: number | string,
    payload: DecisionReviewPayload,
    projectId?: number | string | null
  ) => {
    const response = await api.post<DecisionCommitResponse>(
      `/api/decisions/${decisionId}/reviews/`,
      payload,
      withProject(projectId)
    );
    return response.data;
  },
  approve: async (decisionId: number | string, projectId?: number | string | null) => {
    const response = await api.post<DecisionCommitResponse>(
      `/api/decisions/${decisionId}/approve/`,
      {},
      withProject(projectId)
    );
    return response.data;
  },
  archive: async (decisionId: number | string, projectId?: number | string | null) => {
    const response = await api.post<DecisionCommitResponse>(
      `/api/decisions/${decisionId}/archive/`,
      {},
      withProject(projectId)
    );
    return response.data;
  },
  listReviews: async (decisionId: number | string, projectId?: number | string | null) => {
    const response = await api.get(
      `/api/decisions/${decisionId}/reviews/`,
      withProject(projectId)
    );
    return response.data as unknown as { items?: any[] } | any[];
  },
  listDecisions: async (
    projectId: number | string,
    params?: { status?: string; riskLevel?: string }
  ): Promise<DecisionListResponse> => {
    const base = withProject(projectId);
    const accumulated: DecisionListResponse['items'] = [];
    let pageToken: string | null | undefined = '0';

    while (pageToken !== null && pageToken !== undefined) {
      const response = await api.get<DecisionListResponse>('/api/decisions/', {
        ...base,
        params: {
          ...(base as any).params,
          ...params,
          pageSize: 100,
          pageToken,
        },
      }) as { data: DecisionListResponse };
      const data = response.data;
      if (data?.items?.length) {
        accumulated.push(...data.items);
      }
      pageToken = data.nextPageToken;
    }

    return { items: accumulated };
  },
  listSignals: async (decisionId: number | string, projectId?: number | string | null) => {
    const response = await api.get<DecisionSignalListResponse>(
      `/api/decisions/${decisionId}/signals/`,
      withProject(projectId)
    );
    return response.data;
  },
  getDecisionGraph: async (
    projectId: number | string,
    options?: { scope?: 'project' | 'all_projects' },
  ): Promise<DecisionGraphResponse> => {
    const response = await api.get<DecisionGraphResponse>(
      '/api/decisions/graph',
      {
        headers: { 'x-project-id': projectId },
        params: {
          project_id: projectId,
          ...(options?.scope && options.scope !== 'project' ? { scope: options.scope } : {}),
        },
      },
    );
    return response.data;
  },
  getConnections: async (decisionId: number | string, projectId?: number | string | null) => {
    const response = await api.get<DecisionConnectionsResponse>(
      `/api/decisions/${decisionId}/connections/`,
      withProject(projectId)
    );
    return response.data;
  },
  updateConnections: async (
    decisionId: number | string,
    connectedDecisionSeqs: number[],
    projectId?: number | string | null
  ) => {
    const response = await api.put<DecisionConnectionsResponse>(
      `/api/decisions/${decisionId}/connections/`,
      { connectedDecisionSeqs },
      withProject(projectId)
    );
    return response.data;
  },
  updateConnectionsById: async (
    decisionId: number | string,
    connectedDecisionIds: number[],
    projectId?: number | string | null
  ) => {
    const response = await api.put<DecisionConnectionsResponse>(
      `/api/decisions/${decisionId}/connections/`,
      { connectedDecisionIds },
      withProject(projectId)
    );
    return response.data;
  },
  moveDecisionToProject: async (
    decisionId: number | string,
    fromProjectId: number | string,
    targetProjectId: number | string,
  ) => {
    const response = await api.post<DecisionDraftResponse>(
      `/api/decisions/${decisionId}/move-project/`,
      { projectId: targetProjectId },
      withProject(fromProjectId),
    );
    return response.data;
  },
  moveDecisionToTopic: async (
    decisionId: number | string,
    projectId: number | string,
    topic: string,
  ) => {
    const response = await api.post<DecisionDraftResponse>(
      `/api/decisions/${decisionId}/move-topic/`,
      { topic },
      withProject(projectId),
    );
    return response.data;
  },
  renameDecisionTopic: async (
    projectId: number | string,
    topic: string,
    title: string,
  ) => {
    const response = await api.patch<DecisionTopicLabelResponse>(
      `/api/decisions/topic-labels/${encodeURIComponent(topic)}`,
      { title },
      withProject(projectId),
    );
    return response.data;
  },
  createDecisionTopic: async (
    projectId: number | string,
    title: string,
  ) => {
    const response = await api.post<DecisionTopicLabelResponse>(
      `/api/decisions/topic-labels/${encodeURIComponent(title)}`,
      { title },
      withProject(projectId),
    );
    return response.data;
  },
  deleteDecisionTopic: async (
    projectId: number | string,
    topic: string,
  ) => {
    await api.delete(
      `/api/decisions/topic-labels/${encodeURIComponent(topic)}`,
      withProject(projectId),
    );
  },
  createSignal: async (
    decisionId: number | string,
    payload: DecisionSignalPayload,
    projectId?: number | string | null
  ) => {
    const response = await api.post<DecisionSignal>(
      `/api/decisions/${decisionId}/signals/`,
      payload,
      withProject(projectId)
    );
    return response.data;
  },
  updateSignal: async (
    decisionId: number | string,
    signalId: number | string,
    payload: Partial<DecisionSignalPayload>,
    projectId?: number | string | null
  ) => {
    const response = await api.patch<DecisionSignal>(
      `/api/decisions/${decisionId}/signals/${signalId}/`,
      payload,
      withProject(projectId)
    );
    return response.data;
  },
  deleteSignal: async (
    decisionId: number | string,
    signalId: number | string,
    projectId?: number | string | null
  ) => {
    const response = await api.delete(
      `/api/decisions/${decisionId}/signals/${signalId}/`,
      withProject(projectId)
    );
    return response.data;
  },
  deleteDecision: async (decisionId: number | string, projectId?: number | string | null) => {
    await api.delete(`/api/decisions/${decisionId}/`, withProject(projectId));
  },
};
