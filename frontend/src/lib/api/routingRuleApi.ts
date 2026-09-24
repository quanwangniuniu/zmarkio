import api from '../api';
import type {
  RoutingRule,
  RoutingRulePayload,
  RoutingSandboxRequest,
  RoutingSandboxResult,
  RoutingVocabulary,
} from '@/types/routingRule';

const BASE = '/api/csm';

export const RoutingRuleAPI = {
  list(projectId: number, experienceGroupId: number) {
    return api
      .get<RoutingRule[]>(`${BASE}/routing-rules/`, {
        params: { project: projectId, experience_group: experienceGroupId },
      })
      .then((res) => res.data);
  },

  create(projectId: number, data: RoutingRulePayload) {
    return api
      .post<RoutingRule>(`${BASE}/routing-rules/`, data, { params: { project: projectId } })
      .then((res) => res.data);
  },

  update(ruleId: number, data: Partial<RoutingRulePayload>) {
    return api.patch<RoutingRule>(`${BASE}/routing-rules/${ruleId}/`, data).then((res) => res.data);
  },

  remove(ruleId: number) {
    return api.delete(`${BASE}/routing-rules/${ruleId}/`);
  },

  reorder(projectId: number, experienceGroupId: number, ids: number[]) {
    return api
      .put<RoutingRule[]>(
        `${BASE}/routing-rules/reorder/`,
        { experience_group: experienceGroupId, ids },
        { params: { project: projectId } },
      )
      .then((res) => res.data);
  },

  vocabulary(projectId: number) {
    return api
      .get<RoutingVocabulary>(`${BASE}/routing-rules/vocabulary/`, { params: { project: projectId } })
      .then((res) => res.data);
  },
};

export const RoutingSandboxAPI = {
  evaluate(projectId: number, data: RoutingSandboxRequest) {
    return api
      .post<RoutingSandboxResult>(`${BASE}/routing-sandbox/evaluate/`, data, {
        params: { project: projectId },
      })
      .then((res) => res.data);
  },
};
