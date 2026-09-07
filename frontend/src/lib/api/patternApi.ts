import api from '../api';
import { AiConsentRequiredError, aiConsentSpreadsheetId, isAiConsentRequired } from './aiConsentApi';
import { Id } from '@/types/common';
import {
  CreatePatternPayload,
  ListPatternsResponse,
  ApplyPatternResponse,
  PatternJobResponse,
  WorkflowPatternDetail,
  WorkflowPatternSummary,
  TimelineItem,
} from '@/types/patterns';

export class PatternAgentError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'PatternAgentError';
  }
}

/**
 * Normalize a raw step dict returned by the backend into a TimelineItem.
 * APPLY_FORMULA steps store target/a1/formula at the top level in the frontend
 * type, whereas the backend wraps them in params.
 */
function normalizeStep(raw: Record<string, any>): TimelineItem {
  if (raw.type === 'APPLY_FORMULA') {
    const p = raw.params ?? {};
    return {
      id: raw.id,
      type: 'APPLY_FORMULA',
      target: p.target ?? { row: 1, col: 0 },
      a1: p.a1 ?? '',
      formula: p.formula ?? '',
      disabled: raw.disabled ?? false,
      createdAt: raw.createdAt ?? new Date().toISOString(),
    };
  }
  return raw as TimelineItem;
}

export const PatternAPI = {
  createPattern: async (payload: CreatePatternPayload): Promise<WorkflowPatternSummary> => {
    const response = await api.post<WorkflowPatternSummary>('/api/spreadsheet/patterns/', payload);
    return response.data;
  },

  listPatterns: async (): Promise<ListPatternsResponse> => {
    const response = await api.get<ListPatternsResponse>('/api/spreadsheet/patterns/');
    return response.data;
  },

  getPattern: async (patternId: string): Promise<WorkflowPatternDetail> => {
    const response = await api.get<WorkflowPatternDetail>(`/api/spreadsheet/patterns/${patternId}/`);
    return response.data;
  },

  deletePattern: async (patternId: string): Promise<void> => {
    await api.delete(`/api/spreadsheet/patterns/${patternId}/`);
  },

  applyPattern: async (
    patternId: string,
    payload: { spreadsheet_id: Id; sheet_id: Id }
  ): Promise<ApplyPatternResponse> => {
    const response = await api.post<ApplyPatternResponse>(`/api/spreadsheet/patterns/${patternId}/apply/`, payload);
    return response.data;
  },

  getPatternJob: async (jobId: string): Promise<PatternJobResponse> => {
    const response = await api.get<PatternJobResponse>(`/api/spreadsheet/pattern-jobs/${jobId}/`);
    return response.data;
  },

  /**
   * Send a natural-language instruction to Gemini and get back TimelineItems.
   * Throws PatternAgentError when Gemini returns an ERROR_REQUEST step.
   * Throws a generic Error on network / server failure.
   */
  generatePatternSteps: async (sheetId: number, instruction: string): Promise<TimelineItem[]> => {
    let response;
    try {
      response = await api.post<{ steps: Array<Record<string, any>>; error?: string }>(
        `/api/spreadsheet/sheets/${sheetId}/generate-pattern-steps/`,
        { instruction },
        { timeout: 60000 }  // Gemini can take 15-30s; override the global 10s default
      );
    } catch (err) {
      if (isAiConsentRequired(err)) throw new AiConsentRequiredError(aiConsentSpreadsheetId(err));
      throw err;
    }
    const { steps, error } = response.data;
    if (error) throw new Error(error);

    // Surface ERROR_REQUEST from Gemini as a typed error
    if (steps.length === 1 && steps[0].type === 'ERROR_REQUEST') {
      throw new PatternAgentError(steps[0].params?.message ?? 'Unable to generate steps for this instruction.');
    }

    return steps.map(normalizeStep);
  },
};

