/** SessionStorage keys and launch helpers for Dashboard Agent side panel. */

export const AGENT_CALENDAR_CONTEXT_KEY = 'agent-calendar-context';
export const AGENT_DRAFT_CONTEXT_KEY = 'agent-draft-context';
export const AGENT_SPREADSHEET_CONTEXT_KEY = 'agent-spreadsheet-context';
export const AGENT_SESSION_ID_KEY = 'agent-session-id';
export const AGENT_PANEL_OPENED_EVENT = 'agent:panel-opened';

export const SPREADSHEET_HIGHLIGHT_LOCATIONS_EVENT = 'spreadsheet:highlight-locations';

export type CalendarPreload = {
  message: string;
  context: Record<string, unknown>;
};

export type SpreadsheetPreload = {
  message: string;
  // Projects on flat routes are identified by slug (string) or numeric id.
  projectId: number | string;
  spreadsheetId: number;
  sheetId: number;
  sheetName: string;
  spreadsheetName: string;
};

export type SpreadsheetHighlightDetail = {
  spreadsheetId: number;
  sheetId: number;
  locations: Array<{ row: number; col: number; a1?: string; sheet_id?: number }>;
};

/**
 * Read and remove calendar/event context staged by Calendar → Ask Agent.
 * Returns null when no context is pending.
 */
export function consumeCalendarPreload(): CalendarPreload | null {
  if (typeof window === 'undefined') {
    return null;
  }
  const raw = sessionStorage.getItem(AGENT_CALENDAR_CONTEXT_KEY);
  if (!raw) {
    return null;
  }
  sessionStorage.removeItem(AGENT_CALENDAR_CONTEXT_KEY);
  try {
    const ctx = JSON.parse(raw) as Record<string, unknown>;
    let message: string;
    if (ctx.type === 'event') {
      const start = new Date(String(ctx.startDatetime));
      const end = new Date(String(ctx.endDatetime));
      const dateStr = start.toLocaleDateString('en-US', {
        weekday: 'long',
        month: 'long',
        day: 'numeric',
      });
      const startTime = start.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      const endTime = end.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      const description =
        typeof ctx.description === 'string' && ctx.description.trim()
          ? ` Description: ${ctx.description.trim()}.`
          : '';
      message = `I'm looking at a calendar event: "${ctx.eventTitle}" on ${dateStr} from ${startTime} to ${endTime}.${description} Can you help me understand this event and suggest what I should prepare or do?`;
    } else {
      message = `I'm viewing my calendar (${(ctx.currentView as string) ?? 'week'} view). Can you help me understand my calendar events, check my availability, or assist with scheduling?`;
    }
    return { message, context: ctx };
  } catch {
    return null;
  }
}

/**
 * Stage spreadsheet context for in-sheet AI analysis (consumed by AgentChatPage).
 */
export function stageSpreadsheetInsightsPreload(payload: {
  projectId: number | string;
  spreadsheetId: number;
  sheetId: number;
  sheetName: string;
  spreadsheetName: string;
}): SpreadsheetPreload {
  const message = `Analyze the current sheet "${payload.sheetName}" in "${payload.spreadsheetName}" for a summary, anomalies, and recommendations.`;
  const preload: SpreadsheetPreload = {
    message,
    projectId: payload.projectId,
    spreadsheetId: payload.spreadsheetId,
    sheetId: payload.sheetId,
    sheetName: payload.sheetName,
    spreadsheetName: payload.spreadsheetName,
  };
  if (typeof window !== 'undefined') {
    sessionStorage.setItem(AGENT_SPREADSHEET_CONTEXT_KEY, JSON.stringify(preload));
    sessionStorage.removeItem(AGENT_SESSION_ID_KEY);
  }
  return preload;
}

/**
 * Read and remove spreadsheet context staged by Spreadsheet → Analyze with AI.
 */
export function consumeSpreadsheetPreload(): SpreadsheetPreload | null {
  if (typeof window === 'undefined') {
    return null;
  }
  const raw = sessionStorage.getItem(AGENT_SPREADSHEET_CONTEXT_KEY);
  if (!raw) {
    return null;
  }
  sessionStorage.removeItem(AGENT_SPREADSHEET_CONTEXT_KEY);
  try {
    const parsed = JSON.parse(raw) as SpreadsheetPreload;
    if (
      (typeof parsed.projectId !== 'number' && typeof parsed.projectId !== 'string') ||
      typeof parsed.spreadsheetId !== 'number' ||
      typeof parsed.sheetId !== 'number' ||
      typeof parsed.message !== 'string'
    ) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

export function readStoredAgentSessionId(): string | null {
  if (typeof window === 'undefined') {
    return null;
  }
  const stored = sessionStorage.getItem(AGENT_SESSION_ID_KEY);
  return stored?.trim() || null;
}

/**
 * Context staged by a draft's entry point.
 * - "Ask Agent" (autoSend=true): the Agent auto-sends `message` on open.
 * - "Open in Agent" (autoSend=false): the draft is attached as context and the
 *   cursor lands in the input; nothing is sent until the user types a message.
 */
export type DraftPreload = {
  message: string;
  autoSend: boolean;
  context: Record<string, unknown>;
};

/**
 * Stage a draft as Agent context (Draft → Agent). Stores a `{message, autoSend,
 * context}` payload that the Agent side panel consumes on open. `context.draftId`
 * is the draft's slug or pk; the Agent reads the draft via the existing Notion
 * API. The draft context is threaded onto every subsequent send for the session,
 * so follow-up messages keep the draft attached regardless of `autoSend`.
 */
export function stageDraftContext(args: {
  draftId: string | number;
  title?: string;
  /** true = "Ask Agent" (auto-send); false = "Open in Agent" (wait for user). */
  autoSend?: boolean;
}): void {
  if (typeof window === 'undefined') {
    return;
  }
  const autoSend = args.autoSend !== false; // default: Ask Agent
  const title = (args.title || '').trim();
  const context: Record<string, unknown> = {
    type: 'draft',
    draftId: String(args.draftId),
    title,
  };
  // Only "Ask Agent" carries an auto-message; "Open in Agent" stays silent.
  const message = autoSend ? 'Summarize this draft.' : '';
  sessionStorage.setItem(
    AGENT_DRAFT_CONTEXT_KEY,
    JSON.stringify({ message, autoSend, context })
  );
  // A fresh draft context starts a new conversation thread.
  sessionStorage.removeItem(AGENT_SESSION_ID_KEY);
}

/**
 * Whether a staged draft preload should auto-send its initial message.
 * "Ask Agent" → true (sends immediately); "Open in Agent" → false (the Agent
 * waits for the user, so no LLM call happens until they type a message).
 */
export function shouldAutoSendDraftPreload(preload: DraftPreload | null): boolean {
  return Boolean(preload?.autoSend);
}

/**
 * Read and remove draft context staged by a draft entry point.
 * Returns null when no draft context is pending.
 */
export function consumeDraftPreload(): DraftPreload | null {
  if (typeof window === 'undefined') {
    return null;
  }
  const raw = sessionStorage.getItem(AGENT_DRAFT_CONTEXT_KEY);
  if (!raw) {
    return null;
  }
  sessionStorage.removeItem(AGENT_DRAFT_CONTEXT_KEY);
  try {
    const parsed = JSON.parse(raw) as DraftPreload;
    if (!parsed || typeof parsed.message !== 'string' || typeof parsed.context !== 'object') {
      return null;
    }
    return { ...parsed, autoSend: parsed.autoSend !== false };
  } catch {
    return null;
  }
}
