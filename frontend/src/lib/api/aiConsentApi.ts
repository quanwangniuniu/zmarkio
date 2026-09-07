import api from '../api';

/**
 * Thrown when an AI-analysis call is refused because the current user has not
 * yet consented to sending *this spreadsheet's* data to an external AI service.
 * Callers catch this, show the consent prompt, then retry. `spreadsheetId` is
 * the spreadsheet the one-time consent is for (from the 403 body / SSE error).
 */
export class AiConsentRequiredError extends Error {
  readonly spreadsheetId?: number;

  constructor(spreadsheetId?: number, message = 'Enable AI analysis for this spreadsheet to continue.') {
    super(message);
    this.name = 'AiConsentRequiredError';
    this.spreadsheetId = spreadsheetId;
  }
}

/** True when an axios error is a 403 carrying the AI_CONSENT_REQUIRED code. */
export function isAiConsentRequired(err: unknown): boolean {
  const resp = (err as { response?: { status?: number; data?: { code?: string } } })?.response;
  return resp?.status === 403 && resp?.data?.code === 'AI_CONSENT_REQUIRED';
}

/**
 * The `spreadsheet_id` an AI_CONSENT_REQUIRED payload points at, if present.
 * Accepts an axios error, an SSE `error` event, or a bare `{ spreadsheet_id }`.
 */
export function aiConsentSpreadsheetId(source: unknown): number | undefined {
  const s = source as {
    response?: { data?: { spreadsheet_id?: unknown } };
    data?: { spreadsheet_id?: unknown };
    spreadsheet_id?: unknown;
  };
  const raw = s?.response?.data?.spreadsheet_id ?? s?.data?.spreadsheet_id ?? s?.spreadsheet_id;
  return typeof raw === 'number' ? raw : undefined;
}

export interface AiConsentStatus {
  consented: boolean;
  consented_at: string | null;
}

/**
 * What the consent is for. Callers that hold a spreadsheet id (reactive path —
 * it comes back in the 403 / SSE error) use `spreadsheetId`; callers that only
 * hold a sheet id (proactive check when entering pattern/pivot mode) use
 * `sheetId`, which the backend resolves to its spreadsheet.
 */
export type AiConsentTarget = { spreadsheetId: string | number } | { sheetId: number };

function consentParams(target: AiConsentTarget): Record<string, string | number> {
  return 'sheetId' in target
    ? { sheet_id: target.sheetId }
    : { spreadsheet_id: target.spreadsheetId };
}

export const AiConsentAPI = {
  get: async (target: AiConsentTarget): Promise<AiConsentStatus> => {
    const response = await api.get<AiConsentStatus>('/api/agent/ai-consent/', {
      params: consentParams(target),
    });
    return response.data;
  },

  grant: async (target: AiConsentTarget): Promise<AiConsentStatus> => {
    const response = await api.post<AiConsentStatus>('/api/agent/ai-consent/', consentParams(target));
    return response.data;
  },
};
