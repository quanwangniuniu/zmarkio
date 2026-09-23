"use client"

import { useState, useCallback, useRef, useEffect, useMemo } from "react"
import { useRouter } from "next/navigation"
import { useAgentLayout, type AgentView } from "@/components/agent/AgentLayoutContext"
import { WelcomeScreen } from "./WelcomeScreen"
import { MessageList, type ChatMessage } from "./MessageList"
import { ChatInput } from "./ChatInput"
import { ActionBar } from "./ActionBar"
import OnboardingTokenIntro from "./OnboardingTokenIntro"
import type { PendingExternalApproval } from "./ExternalApprovalModal"
import { AgentAPI } from "@/lib/api/agentApi"
import { PatternAPI, PatternAgentError } from "@/lib/api/patternApi"
import { PivotAPI, PivotAgentError } from "@/lib/api/pivotApi"
import { AiConsentAPI, AiConsentRequiredError, type AiConsentTarget } from "@/lib/api/aiConsentApi"
import ConfirmModal from "@/components/ui/ConfirmModal"
import { SpreadsheetAPI } from "@/lib/api/spreadsheetApi"
import { generatePivotSheetName } from "@/lib/spreadsheet/pivot"
import {
  setAgentMessageBoardWaitingForFileAnalysisResponse,
  setAgentMessageBoardRenderEffectsCompletedOnQuit,
  shouldShowAgentMessageBoardThinkingBubbleOnRevisit,
} from "@/lib/agentMessageBoardReadState"
import type {
  SSEEvent,
  AgentAction,
  AgentMessage,
  AgentMessageData,
  AnalysisResult,
  WorkflowStepState,
  ColumnDetectionData,
  GenerationOutputKey,
  RecommendedTask,
  RecommendedDecisionTreeNode,
  SuggestedCalendarEvent,
  StepExecutionStatus,
} from "@/types/agent"
import { useGenerationOutputs } from "@/hooks/useGenerationOutputs"
import { GenerationOutputsSettings } from "./GenerationOutputsSettings"
import { AgentWorkflowBanner } from "./AgentWorkflowBanner"
import { AGENT_MESSAGES } from "@/lib/agentMessages"
import type { StepProgressItem } from "./StepProgress"
import type { TaskGenerationStatus } from "./TaskListCard"
import type { DecisionGenerationStatus } from "./DecisionTreeListCard"
import {
  AGENT_PANEL_OPENED_EVENT,
  consumeCalendarPreload,
  consumeDraftPreload,
  consumeSpreadsheetPreload,
  shouldAutoSendDraftPreload,
  type CalendarPreload,
  type DraftPreload,
  type SpreadsheetPreload,
} from "@/lib/agentLaunchContext"
import { getPendingMiroWorkflowRunIds } from "@/lib/agentMiroBoardStatus"
import { agentMiroBoardHref } from "@/lib/agentMiroBoardHref"
import { useBuildUrl } from "@/lib/buildUrl"

function pickRecommendedDecisionTree(
  data: AnalysisResult | null | undefined,
  wantsDecisions: boolean
) {
  if (!wantsDecisions) return undefined
  const nodes = data?.recommended_decision_tree?.nodes
  if (!Array.isArray(nodes) || nodes.length === 0) return undefined
  return { nodes }
}

function mapCreatedDecisionsByRef(
  created?: Array<{ ref?: string; decision_id?: number }> | null
): Record<string, number> {
  if (!Array.isArray(created)) return {}
  const out: Record<string, number> = {}
  for (const entry of created) {
    const ref = entry?.ref
    const decisionId = Number(entry?.decision_id)
    if (typeof ref === "string" && ref && Number.isFinite(decisionId)) {
      out[ref] = decisionId
    }
  }
  return out
}

function hasPersistedAnalysisPayload(data?: AgentMessageData | null): boolean {
  if (!data) return false
  if (Array.isArray(data.recommended_tasks)) return true
  if (Array.isArray(data.recommended_decision_tree?.nodes)) return true
  if (Array.isArray(data.anomalies) && data.anomalies.length > 0) return true
  if (Array.isArray(data.calendar_events)) return true
  return false
}

function restoreGenerationOutputsFromMessages(
  messages: AgentMessage[]
): GenerationOutputKey[] | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const raw = messages[i].data?.generation_outputs
    if (Array.isArray(raw) && raw.length > 0) {
      return raw as GenerationOutputKey[]
    }
  }
  return null
}

/** Broadcast anomalies from restored messages to RightPanel Alerts. */
function broadcastRestoredAnomalies(messages: AgentMessage[]) {
  // Find the last message with anomalies and broadcast it
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].data?.anomalies) {
      window.dispatchEvent(new CustomEvent("agent:analysis-complete", {
        detail: { anomalies: messages[i].data!.anomalies }
      }))
      break
    }
  }
}

/** Build a spreadsheet detail URL from persisted message metadata. */
function buildSpreadsheetNavigateHref(
  data?: AgentMessage["data"]
): string | undefined {
  if (!data) return undefined
  if (data.url) return data.url
  if (data.spreadsheet_id != null && data.project_id != null) {
    return `/spreadsheets/${data.spreadsheet_id}?project_id=${data.project_id}`
  }
  return undefined
}

function parseUploadedFileName(content: string): string | undefined {
  const match = content.match(/^Uploaded "(.+)"$/)
  return match?.[1]
}

/** Human-readable timestamp for auto-generated session titles, e.g. "Aug 26, 3:45 PM". */
function formatSessionTimestamp(date: Date = new Date()): string {
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  })
}

function spreadsheetUploadNavigateLabel(fileName: string): string {
  return `Uploaded "${fileName}"`
}

/** Sheet identity the workflow banner needs for the spreadsheet-analysis flow. */
type SheetWorkContext = {
  spreadsheetId: number
  sheetId: number
  sheetName: string
  spreadsheetName: string
}

function sheetWorkContextFromPreload(preload: SpreadsheetPreload): SheetWorkContext {
  return {
    spreadsheetId: preload.spreadsheetId,
    sheetId: preload.sheetId,
    sheetName: preload.sheetName,
    spreadsheetName: preload.spreadsheetName,
  }
}

/** Restore the persisted spreadsheet-analysis sheet context (survives refresh). */
function readStoredSheetWorkContext(): SheetWorkContext | null {
  if (typeof window === "undefined") return null
  const raw = sessionStorage.getItem("agent-session-spreadsheet-context")
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as Partial<SheetWorkContext>
    if (
      typeof parsed.spreadsheetId === "number" &&
      typeof parsed.sheetId === "number" &&
      typeof parsed.sheetName === "string" &&
      typeof parsed.spreadsheetName === "string"
    ) {
      return parsed as SheetWorkContext
    }
  } catch {
    /* ignore malformed value */
  }
  return null
}

/**
 * Which sheet the pattern- or pivot-generation board is working on. Persisted
 * (like the spreadsheet-analysis context) so the workflow banner still names
 * the sheet after a page reload — these modes otherwise keep everything in
 * volatile component state and come back blank.
 */
type GenerationModeContext = {
  mode: "pattern_generation" | "pivot_table_generation"
  spreadsheetId: string | number | null
  sheetId: number
  sheetName: string | null
  spreadsheetName: string | null
}

const GENERATION_MODE_CONTEXT_KEY = "agent-generation-mode-context"

function readStoredGenerationModeContext(): GenerationModeContext | null {
  if (typeof window === "undefined") return null
  const raw = sessionStorage.getItem(GENERATION_MODE_CONTEXT_KEY)
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as Partial<GenerationModeContext>
    if (
      (parsed.mode === "pattern_generation" ||
        parsed.mode === "pivot_table_generation") &&
      typeof parsed.sheetId === "number"
    ) {
      return {
        mode: parsed.mode,
        spreadsheetId: parsed.spreadsheetId ?? null,
        sheetId: parsed.sheetId,
        sheetName: typeof parsed.sheetName === "string" ? parsed.sheetName : null,
        spreadsheetName:
          typeof parsed.spreadsheetName === "string" ? parsed.spreadsheetName : null,
      }
    }
  } catch {
    /* ignore malformed value */
  }
  return null
}

/**
 * Generation-mode marker stamped onto the NL pattern/pivot transcript messages
 * (via AgentAPI.appendMessage `data`). Lets a session reopened from sidebar
 * history rehydrate its workflow banner + mode — the volatile component state /
 * per-tab sessionStorage copy is gone by then.
 */
type GenerationModeMarker = {
  generation_mode: "pattern_generation" | "pivot_table_generation"
  spreadsheet_id: string | number | null
  sheet_id: number
  sheet_name: string | null
  spreadsheet_name: string | null
}

/** Newest pattern/pivot generation marker in a restored transcript, if any. */
function findGenerationModeMarker(
  messages: AgentMessage[]
): GenerationModeMarker | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const d = messages[i]?.data as
      | {
          generation_mode?: unknown
          spreadsheet_id?: unknown
          sheet_id?: unknown
          sheet_name?: unknown
          spreadsheet_name?: unknown
        }
      | null
      | undefined
    if (
      (d?.generation_mode === "pattern_generation" ||
        d?.generation_mode === "pivot_table_generation") &&
      typeof d.sheet_id === "number"
    ) {
      return {
        generation_mode: d.generation_mode,
        spreadsheet_id:
          typeof d.spreadsheet_id === "number" || typeof d.spreadsheet_id === "string"
            ? d.spreadsheet_id
            : null,
        sheet_id: d.sheet_id,
        sheet_name: typeof d.sheet_name === "string" ? d.sheet_name : null,
        spreadsheet_name:
          typeof d.spreadsheet_name === "string" ? d.spreadsheet_name : null,
      }
    }
  }
  return null
}

/** Move spreadsheet link from user upload rows onto the next assistant bubble. */
function attachSpreadsheetNavFromUploads(messages: ChatMessage[]): ChatMessage[] {
  const result = messages.map((m) => ({ ...m }))
  for (let i = 0; i < result.length; i++) {
    const upload = result[i]
    if (upload.role !== "user" || upload.type !== "file_uploaded") continue

    const navigateHref = upload.navigateHref
    const fileName = upload.fileName ?? parseUploadedFileName(upload.content)
    if (!navigateHref || !fileName) continue

    for (let j = i + 1; j < result.length; j++) {
      if (result[j].role !== "assistant") continue
      if (!result[j].navigateLabel) {
        result[j] = {
          ...result[j],
          navigateTo: "spreadsheet",
          navigateLabel: spreadsheetUploadNavigateLabel(fileName),
          navigateHref,
        }
      }
      break
    }

    result[i] = { ...upload, navigateHref: undefined }
  }
  return result
}

function prepareRestoredMessages(messages: ChatMessage[]): ChatMessage[] {
  return attachSpreadsheetNavFromUploads(dedupeMiroGenerationStartedMessages(messages))
}

/** Restore a persisted AgentMessage into a ChatMessage with correct type & navigation. */
function restoreMessage(m: AgentMessage): ChatMessage {
  let type: ChatMessage["type"] = "text"
  let navigateTo: string | undefined
  let navigateLabel: string | undefined
  let navigateDisabled = false
  let navigateHref: string | undefined
  let fileName: string | undefined
  let approval: PendingExternalApproval | undefined
  const isFollowUpPrompt = m.message_type === "follow_up_prompt"


  const eventType = m.data?.event_type

  if (
    m.role === "user" &&
    (m.data?.original_filename || /^Uploaded "/.test(m.content))
  ) {
    type = "file_uploaded"
    fileName = m.data?.original_filename ?? parseUploadedFileName(m.content)
    navigateHref = buildSpreadsheetNavigateHref(m.data)
  } else if (m.message_type === "calendar_invite") {
    type = "calendar_invite"
  } else if (eventType === "miro_generation_started") {
    type = "miro_status"
    navigateTo = "miro"
    navigateLabel = "Generating Miro..."
    navigateDisabled = true
  } else if (eventType === "miro_board_created" && m.data?.board_id) {
    type = "miro_status"
    navigateTo = "miro"
    navigateLabel = "Open Miro"
    navigateHref = agentMiroBoardHref(m.data)
  } else if (eventType === "miro_generation_failed") {
    type = "error"
  } else if ((m.data as { kind?: string } | null | undefined)?.kind === "spreadsheet_summary") {
    type = "spreadsheet_summary"
  } else if (
    m.message_type === "analysis" &&
    (m.data as { spreadsheet_id?: number } | null | undefined)?.spreadsheet_id != null &&
    Array.isArray(m.data?.anomalies)
  ) {
    type = "spreadsheet_anomalies"
  } else if (m.message_type === "analysis" || hasPersistedAnalysisPayload(m.data)) {
    type = "analysis"
  } else if (m.message_type === "task_created" || m.data?.task_ids) {
    // Check task_created BEFORE decision_draft — backend may include decision_id on task events
    type = "tasks_created"
    navigateTo = "tasks"
    navigateLabel = "Go to Tasks"
  } else if (
    m.message_type === "decision_draft" ||
    (Array.isArray(m.data?.decision_ids) && m.data.decision_ids.length > 0)
  ) {
    type = "decisions_created"
    navigateTo = "decisions"
    navigateLabel = "Go to Decisions"
  } else if (m.message_type === "approval_request" && m.data?.approval_id) {
    approval = {
      id: String(m.data.approval_id),
      kind: String(m.data.kind ?? ""),
      draft: (m.data.draft as Record<string, unknown>) ?? {},
    }
    const draftTasks = (m.data.draft as { recommended_tasks?: unknown } | undefined)
      ?.recommended_tasks
    const hasDraftTasks = Array.isArray(draftTasks) && draftTasks.length > 0
    type =
      hasPersistedAnalysisPayload(m.data) || hasDraftTasks ? "analysis" : "approval_request"
  } else if (m.message_type === "confirmation_request") {
    type = "confirmation_request"
  }

  const draftRecommendedTasks = (
    m.data?.draft as { recommended_tasks?: RecommendedTask[] } | undefined
  )?.recommended_tasks
  const draftDecisionTree = (
    m.data?.draft as { recommended_decision_tree?: AnalysisResult["recommended_decision_tree"] } | undefined
  )?.recommended_decision_tree

  return {
    id: String(m.id),
    role: m.role,
    content: m.content,
    type,
    isFollowUpPrompt,
    // Prefer the reviewed list so a restored, confirmed card shows the user's
    // include/exclude + edits; fall back to the raw anomalies pre-confirmation.
    anomalies: m.data?.reviewed_anomalies ?? m.data?.anomalies,
    anomaliesConfirmed:
      Boolean(m.data?.anomalies_confirmed) || (m.data?.anomalies?.length ?? 0) === 0,
    recommendedTasks: m.data?.recommended_tasks ?? draftRecommendedTasks,
    recommendedDecisionTree: pickRecommendedDecisionTree(
      {
        recommended_decision_tree:
          m.data?.recommended_decision_tree ?? draftDecisionTree,
      } as AnalysisResult,
      Boolean(m.data?.recommended_decision_tree?.nodes?.length || draftDecisionTree?.nodes?.length)
    ),
    calendarEvents: m.data?.calendar_events,
    navigateTo,
    navigateLabel,
    navigateDisabled,
    navigateHref,
    eventType,
    workflowRunId: m.data?.workflow_run_id,
    approval,
    fileName,
    spreadsheetId: m.data?.spreadsheet_id,
    sheetId: m.data?.sheet_id,
  }
}

/** Matches backend `MIRO_LEGACY_BG_QUEUED_MESSAGE` (queued vs board-ready lines differ). */
const LEGACY_MIRO_QUEUED_FALLBACK =
  "Queued Miro board generation — we'll notify you here when the board is ready."

function dedupeMiroGenerationStartedMessages(messages: ChatMessage[]): ChatMessage[] {
  const seen = new Set<string>()
  const out: ChatMessage[] = []
  for (const m of messages) {
    if (m.eventType === "miro_generation_started" && m.workflowRunId) {
      if (seen.has(m.workflowRunId)) continue
      seen.add(m.workflowRunId)
    }
    out.push(m)
  }
  return out
}

function mergeMiroGenerationStartedIntoMessages(
  prev: ChatMessage[],
  aiMsgId: string,
  patch: Pick<
    ChatMessage,
    | "content"
    | "type"
    | "navigateTo"
    | "navigateLabel"
    | "navigateDisabled"
    | "navigateHref"
    | "eventType"
    | "workflowRunId"
  >
): ChatMessage[] {
  const wrid = patch.workflowRunId
  const updated = prev.map((p) => (p.id === aiMsgId ? { ...p, ...patch } : p))
  const stripped =
    wrid != null && wrid !== ""
      ? updated.filter(
          (p) =>
            !(
              p.id !== aiMsgId &&
              p.workflowRunId === wrid &&
              p.eventType === "miro_generation_started"
            )
        )
      : updated
  return dedupeMiroGenerationStartedMessages(stripped)
}

function appendMiroResultMessage(prev: ChatMessage[], event: SSEEvent): ChatMessage[] {
  if (event.type !== "miro_status") return prev
  const eventType = event.data?.event_type
  if (!eventType || (eventType !== "miro_board_created" && eventType !== "miro_generation_failed")) return prev

  const rawWr = event.data?.workflow_run_id
  const workflowRunId =
    typeof rawWr === "string" ? rawWr : rawWr != null ? String(rawWr) : undefined

  // Prevent duplicates when polling/restoring replays the same event
  const alreadyAdded = prev.some(
    (m) => m.eventType === eventType && (workflowRunId ? m.workflowRunId === workflowRunId : true)
  )
  if (alreadyAdded) return prev

  // Broadcast a right-panel dialog update (success/failure).
  // Keep this ephemeral (not persisted) and only emit once per terminal event.
  if (typeof window !== "undefined") {
    if (eventType === "miro_board_created" && event.data?.board_id) {
      window.dispatchEvent(new CustomEvent("agent:miro-status", {
        detail: {
          status: "success",
          boardId: String(event.data.board_id),
          workflowRunId,
        }
      }))
    } else if (eventType === "miro_generation_failed") {
      window.dispatchEvent(new CustomEvent("agent:miro-status", {
        detail: {
          status: "failed",
          workflowRunId,
        }
      }))
    }
  }

  if (eventType === "miro_board_created" && event.data?.board_id) {
    return [
      ...prev,
      {
        id: `miro-created-${workflowRunId ?? Date.now()}`,
        role: "assistant",
        content: event.content || "",
        type: "miro_status",
        navigateTo: "miro",
        navigateLabel: "Open Miro",
        navigateHref: agentMiroBoardHref(event.data),
        eventType,
        workflowRunId,
      },
    ]
  }

  if (eventType === "miro_generation_failed") {
    return [
      ...prev,
      {
        id: `miro-failed-${workflowRunId ?? Date.now()}`,
        role: "assistant",
        content: event.content || "",
        type: "text",
        eventType,
        workflowRunId,
      },
    ]
  }

  return prev
}

// Reset when new calendar context is consumed so auto-send fires once per launch.
let _calendarAutoSendFired = false
// Same one-shot guard for a staged draft context (Draft → Agent).
let _draftAutoSendFired = false
let _spreadsheetAutoSendFired = false

type AgentChatPageProps = {
  /** Hide title + approval row; used when the floating window title bar shows them. */
  embeddedInFloating?: boolean
}

export function AgentChatPage({ embeddedInFloating = false }: AgentChatPageProps) {
  const router = useRouter()
  const buildUrl = useBuildUrl()
  const { setActiveView, floatingChat, toggleMaximize, setFloatingSessionId } = useAgentLayout()
  // Pattern/pivot generation modes keep their sheet identity in component state,
  // which a page reload wipes. Read the persisted copy once so the mode (and its
  // workflow banner) can be rehydrated below.
  const storedGenerationModeContext = useMemo(() => readStoredGenerationModeContext(), [])
  const storedPatternContext =
    storedGenerationModeContext?.mode === "pattern_generation"
      ? storedGenerationModeContext
      : null
  const storedPivotContext =
    storedGenerationModeContext?.mode === "pivot_table_generation"
      ? storedGenerationModeContext
      : null
  const [sessionId, setSessionIdState] = useState<string | null>(null)
  const [projectId, setProjectId] = useState<string | null>(null)
  // Set when an AI-analysis call needs consent (reactive: a request was refused;
  // proactive: entering pattern/pivot mode for a not-yet-consented sheet). The
  // callback re-runs the original action once consent is granted (a no-op for
  // the proactive case — the user just proceeds).
  const [aiConsentRetry, setAiConsentRetry] = useState<null | (() => void)>(null)
  const [aiConsentSubmitting, setAiConsentSubmitting] = useState(false)
  // What the pending consent prompt is for (one-time per spreadsheet).
  const [consentTarget, setConsentTarget] = useState<AiConsentTarget | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isStreaming, setIsStreaming] = useState(false)
  const [hasStarted, setHasStarted] = useState(storedGenerationModeContext != null)
  const [stepProgress, setStepProgress] = useState<StepProgressItem[]>([])
  const [stepState, setStepState] = useState<WorkflowStepState>({
    analysisComplete: false,
    anomaliesConfirmed: false,
    tasksCreated: false,
    decisionsCreated: false,
  })
  const [followUpAvailable, setFollowUpAvailable] = useState(false)
  const [followUpStarted, setFollowUpStarted] = useState(false)
  const [sessionTitle, setSessionTitle] = useState(
    storedPatternContext
      ? "Pattern Generation"
      : storedPivotContext
        ? "Pivot Table Generation"
        : "Chat"
  )
  const [patternGenerationSheetId, setPatternGenerationSheetId] = useState<number | null>(
    storedPatternContext?.sheetId ?? null
  )
  const [patternGenerationSheetName, setPatternGenerationSheetName] = useState<string | null>(
    storedPatternContext?.sheetName ?? null
  )
  const [patternGenerationSpreadsheetName, setPatternGenerationSpreadsheetName] = useState<string | null>(
    storedPatternContext?.spreadsheetName ?? null
  )
  // Separate ref for the pattern-generation sidebar session — never wired into the main
  // session restore / polling flow so it can't clobber the local chat state.
  const patternGenerationSessionIdRef = useRef<string | null>(null)
  const [pivotGenerationSpreadsheetId, setPivotGenerationSpreadsheetId] = useState<string | number | null>(
    storedPivotContext?.spreadsheetId ?? null
  )
  const [pivotGenerationSheetId, setPivotGenerationSheetId] = useState<number | null>(
    storedPivotContext?.sheetId ?? null
  )
  const [pivotGenerationSheetName, setPivotGenerationSheetName] = useState<string | null>(
    storedPivotContext?.sheetName ?? null
  )
  const [pivotGenerationSpreadsheetName, setPivotGenerationSpreadsheetName] = useState<string | null>(
    storedPivotContext?.spreadsheetName ?? null
  )
  // Separate ref for the pivot-generation sidebar session — mirrors patternGenerationSessionIdRef.
  const pivotGenerationSessionIdRef = useRef<string | null>(null)
  // Pattern/pivot sessions created before the transcript marker existed: the
  // sheet binding is unrecoverable, but the title still identifies the workflow,
  // so the banner shows a label-only variant (see applySessionState + workflowBanner).
  const [historicalGenerationMode, setHistoricalGenerationMode] = useState<
    "pattern_generation" | "pivot_table_generation" | null
  >(null)
  const [approvalRequired, setApprovalRequired] = useState(false)
  const [generatedTaskIndexes, setGeneratedTaskIndexes] = useState<number[]>([])
  const [skippedTaskIndexes, setSkippedTaskIndexes] = useState<number[]>([])
  const [createdTaskIdByIndex, setCreatedTaskIdByIndex] = useState<Record<number, number | string>>({})
  const [pendingTaskApproval, setPendingTaskApproval] = useState<PendingExternalApproval | null>(null)
  const [pendingDecisionApproval, setPendingDecisionApproval] = useState<PendingExternalApproval | null>(null)
  const [selectedTaskIndexes, setSelectedTaskIndexes] = useState<number[]>([])
  const [selectedDecisionRefs, setSelectedDecisionRefs] = useState<string[]>([])
  const [skippedDecisionRefs, setSkippedDecisionRefs] = useState<string[]>([])
  const [generatedDecisionRefs, setGeneratedDecisionRefs] = useState<string[]>([])
  const [tasksApprovalGenerating, setTasksApprovalGenerating] = useState(false)
  const [decisionsApprovalGenerating, setDecisionsApprovalGenerating] = useState(false)
  const [miroGenerateInFlight, setMiroGenerateInFlight] = useState(false)
  const [taskGenerationStatus, setTaskGenerationStatus] = useState<TaskGenerationStatus>("idle")
  const [decisionGenerationStatus, setDecisionGenerationStatus] =
    useState<DecisionGenerationStatus>("idle")
  const [createdDecisionByRef, setCreatedDecisionByRef] = useState<Record<string, number>>({})
  const selectAllRecommendedTasks = useCallback((count: number) => {
    if (count > 0) {
      setSelectedTaskIndexes(Array.from({ length: count }, (_, i) => i))
    }
  }, [])
  const selectAllRecommendedDecisionRefs = useCallback((refs: string[]) => {
    if (refs.length > 0) {
      setSelectedDecisionRefs(refs)
    }
  }, [])
  const syncDecisionTreeNodeCount = useCallback((analysis?: AnalysisResult | null) => {
    latestDecisionTreeNodeCountRef.current =
      analysis?.recommended_decision_tree?.nodes?.length ?? 0
  }, [])

  const applyDecisionDraftResult = useCallback((data?: Record<string, unknown> | null) => {
    setStepState((prev) => ({ ...prev, decisionsCreated: true }))
    setPendingDecisionApproval(null)
    setDecisionsApprovalGenerating(false)
    setDecisionGenerationStatus("completed")
    const created = data?.created_decisions as Array<{ ref?: string; decision_id?: number }> | undefined
    setCreatedDecisionByRef(mapCreatedDecisionsByRef(created))
    if (Array.isArray(created)) {
      const refs = created
        .map((entry) => String(entry?.ref ?? ""))
        .filter((ref) => ref.length > 0)
      if (refs.length > 0) {
        setGeneratedDecisionRefs(Array.from(new Set(refs)))
        setSkippedDecisionRefs((prev) => prev.filter((ref) => !refs.includes(ref)))
      }
    }
  }, [])

  const applyPendingDecisionApproval = useCallback((pending: PendingExternalApproval) => {
    setPendingDecisionApproval(pending)
    setDecisionsApprovalGenerating(false)
    setDecisionGenerationStatus("awaiting_approval")
    setSkippedDecisionRefs([])
    const nodes = (pending.draft as { recommended_decision_tree?: { nodes?: { ref: string }[] } })
      ?.recommended_decision_tree?.nodes
    if (Array.isArray(nodes) && nodes.length > 0) {
      setSelectedDecisionRefs((prev) =>
        prev.length > 0 ? prev : nodes.map((node) => node.ref).filter(Boolean)
      )
    }
  }, [])

  const applyPendingTaskApproval = useCallback((pending: PendingExternalApproval) => {
    setPendingTaskApproval(pending)
    setTasksApprovalGenerating(false)
    setTaskGenerationStatus("awaiting_approval")
    setSkippedTaskIndexes([])
    const tasks = (pending.draft as any)?.recommended_tasks
    const tasksLen =
      Array.isArray(tasks) ? tasks.length : (latestRecommendedTasksRef.current?.length ?? 0)
    if (tasksLen > 0) {
      setSelectedTaskIndexes((prev) =>
        prev.length > 0 ? prev : Array.from({ length: tasksLen }, (_, i) => i)
      )
    }
  }, [])
  const abortRef = useRef<AbortController | null>(null)
  const activeStreamTokenRef = useRef(0)
  const sessionLoadRequestRef = useRef(0)
  const [pendingCalendarPreload, setPendingCalendarPreload] = useState<CalendarPreload | null>(() => {
    const preload = consumeCalendarPreload()
    if (preload) {
      _calendarAutoSendFired = false
    }
    return preload
  })
  const [pendingSpreadsheetPreload, setPendingSpreadsheetPreload] = useState<SpreadsheetPreload | null>(() => {
    const preload = consumeSpreadsheetPreload()
    if (preload) {
      _spreadsheetAutoSendFired = false
    }
    return preload
  })
  // Which sheet the spreadsheet-analysis workflow is working on, kept for the
  // session lifetime (and across refresh) so the chat-board workflow banner stays
  // accurate. Pattern/pivot modes track the sheet in their own state below.
  const [sessionSpreadsheetContext, setSessionSpreadsheetContext] =
    useState<SheetWorkContext | null>(() => {
      if (pendingSpreadsheetPreload) {
        return sheetWorkContextFromPreload(pendingSpreadsheetPreload)
      }
      return readStoredSheetWorkContext()
    })
  useEffect(() => {
    if (typeof window === "undefined") return
    if (sessionSpreadsheetContext) {
      sessionStorage.setItem(
        "agent-session-spreadsheet-context",
        JSON.stringify(sessionSpreadsheetContext)
      )
    } else {
      sessionStorage.removeItem("agent-session-spreadsheet-context")
    }
  }, [sessionSpreadsheetContext])
  // On a hard reload the useState initializer above may run before
  // sessionStorage is readable (SSR/hydration), leaving the workflow banner on
  // a generic session. Re-hydrate once on mount so "continuing a spreadsheet
  // analysis session" survives refresh. Guarded so it never resurrects a
  // context that an incoming preload is about to replace or clear.
  useEffect(() => {
    if (typeof window === "undefined") return
    if (pendingSpreadsheetPreload) return
    setSessionSpreadsheetContext((prev) => prev ?? readStoredSheetWorkContext())
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  // Persist the active pattern/pivot generation sheet so the workflow banner
  // still names it after a page reload (see readStoredGenerationModeContext).
  useEffect(() => {
    if (typeof window === "undefined") return
    let ctx: GenerationModeContext | null = null
    if (patternGenerationSheetId != null) {
      ctx = {
        mode: "pattern_generation",
        spreadsheetId: null,
        sheetId: patternGenerationSheetId,
        sheetName: patternGenerationSheetName,
        spreadsheetName: patternGenerationSpreadsheetName,
      }
    } else if (pivotGenerationSheetId != null) {
      ctx = {
        mode: "pivot_table_generation",
        spreadsheetId: pivotGenerationSpreadsheetId,
        sheetId: pivotGenerationSheetId,
        sheetName: pivotGenerationSheetName,
        spreadsheetName: pivotGenerationSpreadsheetName,
      }
    }
    if (ctx) {
      sessionStorage.setItem(GENERATION_MODE_CONTEXT_KEY, JSON.stringify(ctx))
    } else {
      sessionStorage.removeItem(GENERATION_MODE_CONTEXT_KEY)
    }
  }, [
    patternGenerationSheetId,
    patternGenerationSheetName,
    patternGenerationSpreadsheetName,
    pivotGenerationSpreadsheetId,
    pivotGenerationSheetId,
    pivotGenerationSheetName,
    pivotGenerationSpreadsheetName,
  ])
  // Belt-and-suspenders re-hydrate on mount, in case the initializer above ran
  // before sessionStorage was readable (SSR/hydration). Only fills gaps — never
  // overrides a mode the user is already in.
  useEffect(() => {
    if (typeof window === "undefined") return
    const stored = readStoredGenerationModeContext()
    if (!stored) return
    if (patternGenerationSheetId != null || pivotGenerationSheetId != null) return
    if (sessionStorage.getItem("agent-session-id")) return
    if (stored.mode === "pattern_generation") {
      setPatternGenerationSheetId(stored.sheetId)
      setPatternGenerationSheetName(stored.sheetName)
      setPatternGenerationSpreadsheetName(stored.spreadsheetName)
      setSessionTitle("Pattern Generation")
    } else {
      setPivotGenerationSpreadsheetId(stored.spreadsheetId)
      setPivotGenerationSheetId(stored.sheetId)
      setPivotGenerationSheetName(stored.sheetName)
      setPivotGenerationSpreadsheetName(stored.spreadsheetName)
      setSessionTitle("Pivot Table Generation")
    }
    setHasStarted(true)
    setMessages((prev) =>
      prev.length > 0
        ? prev
        : [
            {
              id: `${stored.mode}-restored-${Date.now()}`,
              role: "assistant",
              type: "text",
              content:
                stored.mode === "pattern_generation"
                  ? "You are back in **Pattern Generation Mode** for this sheet. Describe the change you want and I will generate the pattern steps."
                  : "You are back in **Pivot Table Generation Mode** for this spreadsheet. Describe the pivot table you want and I will generate it as a new sheet.",
            },
          ]
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  // Pattern/pivot generation run as synchronous API calls (no SSE stream), so the
  // banner needs its own "actively processing" flag for those modes.
  const [isWorkflowGenerating, setIsWorkflowGenerating] = useState(false)
  // Persist calendar context for the lifetime of this session so follow-up messages
  // also go through the calendar workflow, not the generic fallback.
  const [sessionCalendarContext, setSessionCalendarContext] = useState<Record<string, unknown> | null>(
    pendingCalendarPreload ? pendingCalendarPreload.context : null
  )
  // Persist calendar context so it survives page refreshes / session restores
  useEffect(() => {
    if (sessionCalendarContext) {
      sessionStorage.setItem("agent-session-calendar-context", JSON.stringify(sessionCalendarContext))
    }
  }, [sessionCalendarContext])

  // Draft → Agent: staged draft context (read-only). Mirrors the calendar flow.
  const [pendingDraftPreload, setPendingDraftPreload] = useState<DraftPreload | null>(() => {
    const preload = consumeDraftPreload()
    if (preload) {
      _draftAutoSendFired = false
    }
    return preload
  })
  // Persist for the session lifetime so follow-up questions stay in draft context.
  const [sessionDraftContext, setSessionDraftContext] = useState<Record<string, unknown> | null>(
    pendingDraftPreload ? pendingDraftPreload.context : null
  )
  useEffect(() => {
    if (sessionDraftContext) {
      sessionStorage.setItem("agent-session-draft-context", JSON.stringify(sessionDraftContext))
    }
  }, [sessionDraftContext])

  const handleSendMessageRef = useRef<typeof handleSendMessage | null>(null)
  // Last spreadsheet-insights preload, so a consent prompt can re-run the analysis.
  const lastSpreadsheetPreloadRef = useRef<SpreadsheetPreload | null>(null)
  const isAwaitingFollowUp = followUpStarted && !isStreaming
  const inputPlaceholder = isAwaitingFollowUp
    ? "Ask one follow-up question about the analysis, or include an exact username/email for forwarding..."
    : "Ask about your data or upload a file..."
  const inputHelperText = isAwaitingFollowUp
    ? "You can send one follow-up message now. Ask for an explanation, a short report, or forwarding to specific project members."
    : undefined
  const latestAnalysisMessageId =
    [...messages]
      .reverse()
      .find(
        (message) =>
          message.type === "analysis" ||
          message.type === "spreadsheet_anomalies" ||
          (Array.isArray(message.recommendedTasks) && message.recommendedTasks.length > 0)
      )?.id ?? null
  const [renderFinishSignal, setRenderFinishSignal] = useState(0)
  const showRevisitThinkingBubble = useMemo(() => {
    // Important: this value is persisted outside React (localStorage). We must re-check it
    // once the message board finishes (re)rendering, otherwise a revisit can miss the
    // transition to `renderFinish=true`.
    void renderFinishSignal
    return Boolean(
      sessionId &&
        !isStreaming &&
        shouldShowAgentMessageBoardThinkingBubbleOnRevisit(sessionId)
    )
  }, [sessionId, isStreaming, renderFinishSignal])

  const sessionIdRef = useRef<string | null>(null)
  const stepProgressMsgIdRef = useRef<string | null>(null)
  // Stores pending auto-confirm mapping when column_mapping event is received
  const pendingAutoConfirmRef = useRef<Record<string, string> | null>(null)
  // Always points to the latest handleConfirmColumns so it can be called from handleFileUpload
  const handleConfirmColumnsRef = useRef<((m: Record<string, string>) => void) | null>(null)
  // Stores recommended tasks from latest analysis for task cards and approvals
  const latestRecommendedTasksRef = useRef<import("@/types/agent").RecommendedTask[] | null>(null)
  const latestDecisionTreeNodeCountRef = useRef(0)
  const autoExternalActionsTriggeredRef = useRef(false)
  const autoActionQueueRef = useRef<string[]>([])
  const approvalRequiredRef = useRef(approvalRequired)
  const isStreamingRef = useRef(isStreaming)
  const handleActionRef = useRef<((action: string) => void) | null>(null)
  approvalRequiredRef.current = approvalRequired
  isStreamingRef.current = isStreaming

  const tryRunNextAutoAction = useCallback(() => {
    if (isStreamingRef.current || !sessionIdRef.current) return
    const next = autoActionQueueRef.current.shift()
    if (next) {
      void handleActionRef.current?.(next)
    }
  }, [])

  const { selected: generationOutputsSelected } = useGenerationOutputs()
  const [requestedGenerationOutputs, setRequestedGenerationOutputs] = useState<
    GenerationOutputKey[]
  >([])
  const requestedGenerationOutputsRef = useRef<GenerationOutputKey[]>([])

  // Low-level: queue and run the downstream external actions (create tasks,
  // generate miro). Runs at most once per analysis cycle.
  const triggerExternalActions = useCallback(
    (options?: { requiresApproval?: boolean; generationOutputs?: GenerationOutputKey[] }) => {
      if (autoExternalActionsTriggeredRef.current) return
      autoExternalActionsTriggeredRef.current = true
      const outputs = options?.generationOutputs ?? requestedGenerationOutputsRef.current
      const requiresApproval = options?.requiresApproval ?? approvalRequiredRef.current
      const queue: string[] = []
      if (outputs.includes("recommended_tasks")) {
        queue.push("create_tasks")
      }
      if (outputs.includes("recommended_decision_tree")) {
        queue.push("create_decisions")
      }
      if (outputs.includes("miro_board") && !requiresApproval) {
        queue.push("generate_miro")
      }
      if (queue.length === 0) {
        autoExternalActionsTriggeredRef.current = false
        return
      }
      autoActionQueueRef.current = queue
      if (queue.includes("create_tasks") && !requiresApproval) {
        setTaskGenerationStatus("generating")
      }
      if (queue.includes("create_decisions") && !requiresApproval) {
        setDecisionGenerationStatus("generating")
      }
      tryRunNextAutoAction()
    },
    [tryRunNextAutoAction]
  )

  // After analysis: only auto-run downstream actions when there are no anomalies
  // to review (clean dataset / already confirmed). When anomalies are present,
  // defer until the user confirms them via confirm_anomalies.
  const queueAutoExternalActionsAfterAnalysis = useCallback(
    (options?: {
      approvalRequired?: boolean
      analysis?: AnalysisResult | null
      generationOutputs?: GenerationOutputKey[]
    }) => {
      if (options && "analysis" in options) {
        const anomalies = options.analysis?.anomalies ?? []
        const alreadyConfirmed =
          Boolean(options.analysis?.anomalies_confirmed) || anomalies.length === 0
        if (anomalies.length > 0 && !alreadyConfirmed) {
          return // wait for the user to confirm anomalies
        }
      }
      triggerExternalActions({
        requiresApproval: options?.approvalRequired,
        generationOutputs: options?.generationOutputs,
      })
    },
    [triggerExternalActions]
  )

  // After the user confirms anomalies: run downstream actions only if at least
  // one anomaly was included. All-excluded => no tasks.
  const runPostConfirmActions = useCallback(
    (hasIncluded: boolean) => {
      if (!hasIncluded) return
      triggerExternalActions()
    },
    [triggerExternalActions]
  )

  const setSessionId = useCallback((id: string | null) => {
    sessionIdRef.current = id
    setSessionIdState(id)
    if (id) {
      sessionStorage.setItem("agent-session-id", id)
    } else {
      sessionStorage.removeItem("agent-session-id")
    }
    // Keep floating title bar state in sync when embedded.
    if (embeddedInFloating) {
      setFloatingSessionId(id)
    }
    // Broadcast session id so container headers (e.g. side panel) can sync.
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent("agent:session-id", { detail: { sessionId: id } }))
    }
  }, [embeddedInFloating, setFloatingSessionId])

  const getApprovalPref = useCallback(() => {
    if (typeof window === "undefined") return false
    return localStorage.getItem("agent-approval-required-default") === "true"
  }, [])

  // Ensure approval state is initialized/refreshed whenever the chatbox opens (especially floating/embedded).
  useEffect(() => {
    if (!embeddedInFloating) return
    const id = floatingChat.sessionId ?? sessionStorage.getItem("agent-session-id")
    if (!id) {
      setApprovalRequired(false)
      return
    }
    AgentAPI.getSession(id)
      .then((s) => setApprovalRequired(Boolean(s.approval_required)))
      .catch(() => {
        // keep current value on failure
      })
  }, [embeddedInFloating, floatingChat.sessionId])

  // Keep approvalRequired in sync with the toggle (floating title bar + inline header).
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail as { sessionId?: string; value?: boolean } | undefined
      if (!detail?.sessionId) return
      const current = sessionIdRef.current
      if (!current || String(current) !== String(detail.sessionId)) return
      if (typeof detail.value === "boolean") setApprovalRequired(detail.value)
    }
    window.addEventListener("agent:approval-changed", handler)
    return () => window.removeEventListener("agent:approval-changed", handler)
  }, [])

  const applySessionState = useCallback((session: Awaited<ReturnType<typeof AgentAPI.getSession>>) => {
    setSessionId(String(session.id))
    setProjectId(session.project_id ? String(session.project_id) : null)

    // Restore messages and back-fill recommendedTasks onto analysis messages when needed.
    const restored = session.messages.map(restoreMessage)
    setHasStarted(restored.length > 0)
    for (let i = 0; i < restored.length; i++) {
      if (restored[i].type === "analysis") {
        latestRecommendedTasksRef.current = restored[i].recommendedTasks || null
      }
    }
    setMessages(prepareRestoredMessages(restored))
    setFollowUpAvailable(Boolean(session.follow_up_available))
    setFollowUpStarted(Boolean(session.follow_up_started))
    setSessionTitle((session.title && session.title.trim()) || "Chat")
    setApprovalRequired(Boolean(session.approval_required))
    if (typeof window !== "undefined") {
      window.dispatchEvent(new CustomEvent("agent:session-state", {
        detail: {
          sessionId: String(session.id),
          title: (session.title && session.title.trim()) || "Chat",
          approvalRequired: Boolean(session.approval_required),
        }
      }))
    }
    const lastTaskCreated = [...session.messages].reverse().find(
      (m) => m.message_type === "task_created" || (m.data?.task_ids && m.data.task_ids.length > 0)
    )
    const created = (lastTaskCreated as any)?.data?.created_tasks
    if (Array.isArray(created)) {
      const idxs = created
        .map((c: any) => Number(c?.index))
        .filter((n: unknown) => typeof n === "number" && Number.isFinite(n))
      setGeneratedTaskIndexes(Array.from(new Set(idxs)))
      // If any previously-skipped tasks later get created (e.g. user runs create_tasks again),
      // clear them from the skipped list so the UI stays consistent.
      setSkippedTaskIndexes((prev) => prev.filter((i) => !idxs.includes(i)))
      const pairs = created
        .map((c: any) => [Number(c?.index), c?.task_slug ?? c?.task_id] as const)
        .filter(([idx, tid]: readonly [number, any]) => Number.isFinite(idx) && tid != null)
      setCreatedTaskIdByIndex(Object.fromEntries(pairs))
    } else {
      setGeneratedTaskIndexes([])
      setCreatedTaskIdByIndex({})
    }
    broadcastRestoredAnomalies(session.messages)
    // Derive step state from restored messages.
    // Each new analysis event starts a fresh cycle — reset downstream flags so that
    // task/decision data from a *previous* upload cycle does not carry over.
    const restoredStepState: WorkflowStepState = {
      analysisComplete: false,
      anomaliesConfirmed: false,
      tasksCreated: false,
      decisionsCreated: false,
    }
    for (const m of session.messages) {
      const treeNodes = m.data?.recommended_decision_tree?.nodes
      if (Array.isArray(treeNodes) && treeNodes.length > 0) {
        latestDecisionTreeNodeCountRef.current = treeNodes.length
      }
      if (
        m.message_type === "analysis" ||
        hasPersistedAnalysisPayload(m.data)
      ) {
        restoredStepState.analysisComplete = true
        restoredStepState.tasksCreated = false
        restoredStepState.decisionsCreated = false
        // A fresh analysis resets confirmation; clean datasets (no anomalies)
        // and already-confirmed analyses are treated as confirmed.
        restoredStepState.anomaliesConfirmed =
          Boolean(m.data?.anomalies_confirmed) || (m.data?.anomalies?.length ?? 0) === 0
      }
      if (m.message_type === "task_created" || m.data?.task_ids) restoredStepState.tasksCreated = true
      if (
        m.message_type === "decision_draft" ||
        (Array.isArray(m.data?.decision_ids) && m.data.decision_ids.length > 0)
      ) {
        restoredStepState.decisionsCreated = true
      }
    }
    setStepState(restoredStepState)
    // Reconcile the spreadsheet-analysis banner context with the loaded session:
    // keep the name-carrying context when it matches, replace it when a different
    // sheet was analyzed, drop it when this session is not a spreadsheet analysis.
    const restoredInsightsMsg = [...session.messages]
      .reverse()
      .find(
        (m) =>
          m.message_type === "analysis" &&
          (m.data as { spreadsheet_id?: number } | null | undefined)?.spreadsheet_id != null
      )
    const restoredInsightsSpreadsheetId =
      (restoredInsightsMsg?.data as { spreadsheet_id?: number } | undefined)?.spreadsheet_id ?? null
    const restoredInsightsSheetId =
      (restoredInsightsMsg?.data as { sheet_id?: number } | undefined)?.sheet_id ?? null
    setSessionSpreadsheetContext((prev) => {
      if (restoredInsightsSpreadsheetId == null) return null
      if (
        prev &&
        prev.spreadsheetId === restoredInsightsSpreadsheetId &&
        prev.sheetId === restoredInsightsSheetId
      ) {
        return prev
      }
      return {
        spreadsheetId: restoredInsightsSpreadsheetId,
        sheetId: restoredInsightsSheetId ?? 0,
        sheetName: "",
        spreadsheetName: "",
      }
    })
    // Rehydrate pattern/pivot generation mode when the session is reopened from
    // sidebar history (that path restores these as a generic chat otherwise):
    //  1. transcript marker  -> full restore (banner names + functional
    //     continuation), see findGenerationModeMarker.
    //  2. no marker but the title identifies the workflow -> pre-marker session;
    //     show the banner label only (the sheet binding is unrecoverable, so
    //     continuing starts a fresh mode from the spreadsheet).
    //  3. neither -> clear every mode so a stale banner can't linger.
    const generationMarker = findGenerationModeMarker(session.messages)
    const restoredTitle = (session.title ?? "").trim()
    if (generationMarker?.generation_mode === "pattern_generation") {
      setHistoricalGenerationMode(null)
      patternGenerationSessionIdRef.current = String(session.id)
      setPatternGenerationSheetId(generationMarker.sheet_id)
      setPatternGenerationSheetName(generationMarker.sheet_name)
      setPatternGenerationSpreadsheetName(generationMarker.spreadsheet_name)
      pivotGenerationSessionIdRef.current = null
      setPivotGenerationSpreadsheetId(null)
      setPivotGenerationSheetId(null)
      setPivotGenerationSheetName(null)
      setPivotGenerationSpreadsheetName(null)
    } else if (generationMarker?.generation_mode === "pivot_table_generation") {
      setHistoricalGenerationMode(null)
      pivotGenerationSessionIdRef.current = String(session.id)
      setPivotGenerationSpreadsheetId(generationMarker.spreadsheet_id)
      setPivotGenerationSheetId(generationMarker.sheet_id)
      setPivotGenerationSheetName(generationMarker.sheet_name)
      setPivotGenerationSpreadsheetName(generationMarker.spreadsheet_name)
      patternGenerationSessionIdRef.current = null
      setPatternGenerationSheetId(null)
      setPatternGenerationSheetName(null)
      setPatternGenerationSpreadsheetName(null)
    } else {
      patternGenerationSessionIdRef.current = null
      setPatternGenerationSheetId(null)
      setPatternGenerationSheetName(null)
      setPatternGenerationSpreadsheetName(null)
      pivotGenerationSessionIdRef.current = null
      setPivotGenerationSpreadsheetId(null)
      setPivotGenerationSheetId(null)
      setPivotGenerationSheetName(null)
      setPivotGenerationSpreadsheetName(null)
      if (restoredTitle.startsWith("Pattern Generation")) {
        setHistoricalGenerationMode("pattern_generation")
      } else if (restoredTitle.startsWith("Pivot Table Generation")) {
        setHistoricalGenerationMode("pivot_table_generation")
      } else {
        setHistoricalGenerationMode(null)
      }
    }
    const restoredOutputs = restoreGenerationOutputsFromMessages(session.messages)
    if (restoredOutputs) {
      requestedGenerationOutputsRef.current = restoredOutputs
      setRequestedGenerationOutputs(restoredOutputs)
    }
    if (restoredStepState.analysisComplete) {
      setAgentMessageBoardWaitingForFileAnalysisResponse(String(session.id), false)
    }
    const pendingTaskApprovalFromMessages = !restoredStepState.tasksCreated
      ? [...restored].reverse().find((message) => message.approval?.kind === "task")?.approval ?? null
      : null
    const pendingDecisionApprovalFromMessages = !restoredStepState.decisionsCreated
      ? [...restored].reverse().find((message) => message.approval?.kind === "decision_tree")?.approval ?? null
      : null
    if (pendingTaskApprovalFromMessages) {
      setPendingTaskApproval(pendingTaskApprovalFromMessages)
      setTaskGenerationStatus("awaiting_approval")
      const tasks =
        (pendingTaskApprovalFromMessages.draft as any)?.recommended_tasks ??
        latestRecommendedTasksRef.current ??
        []
      if (Array.isArray(tasks) && tasks.length > 0) {
        setSelectedTaskIndexes(Array.from({ length: tasks.length }, (_, i) => i))
      }
    } else {
      setPendingTaskApproval(null)
      const recommendedTaskCount = latestRecommendedTasksRef.current?.length ?? 0
      if (
        Boolean(session.approval_required) &&
        restoredStepState.analysisComplete &&
        !restoredStepState.tasksCreated &&
        recommendedTaskCount > 0
      ) {
        setSelectedTaskIndexes(Array.from({ length: recommendedTaskCount }, (_, i) => i))
      }
      setTaskGenerationStatus(restoredStepState.tasksCreated ? "completed" : "idle")
    }
    const lastDecisionDraft = [...session.messages]
      .reverse()
      .find(
        (m) =>
          m.message_type === "decision_draft" ||
          (Array.isArray(m.data?.created_decisions) && m.data.created_decisions.length > 0)
      )
    const createdDecisions = lastDecisionDraft?.data?.created_decisions
    setCreatedDecisionByRef(mapCreatedDecisionsByRef(createdDecisions))
    if (Array.isArray(createdDecisions)) {
      const refs = createdDecisions
        .map((entry: { ref?: string }) => String(entry?.ref ?? ""))
        .filter((ref: string) => ref.length > 0)
      setGeneratedDecisionRefs(Array.from(new Set(refs)))
    } else {
      setGeneratedDecisionRefs([])
    }
    if (pendingDecisionApprovalFromMessages) {
      applyPendingDecisionApproval(pendingDecisionApprovalFromMessages)
    } else {
      setPendingDecisionApproval(null)
      const restoredDecisionNodes = [...restored]
        .reverse()
        .find((message) => message.recommendedDecisionTree?.nodes?.length)
        ?.recommendedDecisionTree?.nodes
      if (
        Boolean(session.approval_required) &&
        restoredStepState.analysisComplete &&
        !restoredStepState.decisionsCreated &&
        Array.isArray(restoredDecisionNodes) &&
        restoredDecisionNodes.length > 0
      ) {
        setSelectedDecisionRefs(restoredDecisionNodes.map((node) => node.ref))
      }
      setDecisionGenerationStatus(
        restoredStepState.decisionsCreated ? "completed" : "idle"
      )
    }
    const recommendedTaskCount = latestRecommendedTasksRef.current?.length ?? 0
    const wantsDecisions = (restoredOutputs ?? requestedGenerationOutputsRef.current).includes(
      "recommended_decision_tree"
    )
    const wantsTasks = (restoredOutputs ?? requestedGenerationOutputsRef.current).includes(
      "recommended_tasks"
    )
    if (
      restoredStepState.analysisComplete &&
      restoredStepState.anomaliesConfirmed &&
      ((wantsTasks && !restoredStepState.tasksCreated && recommendedTaskCount > 0) ||
        (wantsDecisions && !restoredStepState.decisionsCreated && latestDecisionTreeNodeCountRef.current > 0)) &&
      !pendingTaskApprovalFromMessages &&
      !pendingDecisionApprovalFromMessages
    ) {
      queueAutoExternalActionsAfterAnalysis({ approvalRequired: Boolean(session.approval_required) })
    }
    if (embeddedInFloating) {
      void AgentAPI.updateSession(String(session.id), { last_read_at: new Date().toISOString() })
        .then(() => {
          window.dispatchEvent(new CustomEvent("agent:sessions-changed"))
        })
        .catch(() => {
          /* ignore */
        })
    }
  }, [
    setSessionId,
    embeddedInFloating,
    queueAutoExternalActionsAfterAnalysis,
    applyPendingDecisionApproval,
  ])

  const refreshFollowUpState = useCallback(async (id: string) => {
    try {
      const session = await AgentAPI.getSession(id)
      setFollowUpAvailable(Boolean(session.follow_up_available))
      setFollowUpStarted(Boolean(session.follow_up_started))
      setApprovalRequired(Boolean(session.approval_required))
      if (typeof window !== "undefined") {
        window.dispatchEvent(new CustomEvent("agent:session-state", {
          detail: {
            sessionId: String(session.id),
            title: (session.title && session.title.trim()) || "Chat",
            approvalRequired: Boolean(session.approval_required),
          }
        }))
      }
    } catch {
      // ignore refresh failures; next restore/poll can retry
    }
  }, [])

  const invalidateActiveStreams = useCallback(() => {
    const sid = sessionIdRef.current
    const streaming = isStreamingRef.current
    if (sid && streaming) {
      setAgentMessageBoardWaitingForFileAnalysisResponse(String(sid), true)
    }
    activeStreamTokenRef.current += 1
    abortRef.current?.abort()
    abortRef.current = null
    setIsStreaming(false)
  }, [])

  const resetTransientChatUiState = useCallback(() => {
    setStepProgress([])
    setPendingTaskApproval(null)
    setPendingDecisionApproval(null)
    setTasksApprovalGenerating(false)
    setDecisionsApprovalGenerating(false)
    setMiroGenerateInFlight(false)
    stepProgressMsgIdRef.current = null
    pendingAutoConfirmRef.current = null
    autoExternalActionsTriggeredRef.current = false
    autoActionQueueRef.current = []
  }, [])

  const refreshSession = useCallback(async (id: string) => {
    try {
      const session = await AgentAPI.getSession(id)
      if (String(sessionIdRef.current) !== String(id)) return
      // Re-apply the same backfill logic as applySessionState so that
      const restored = session.messages.map(restoreMessage)
      for (let i = 0; i < restored.length; i++) {
        if (restored[i].type === "analysis") {
          latestRecommendedTasksRef.current = restored[i].recommendedTasks || null
        }
      }
      setMessages(prepareRestoredMessages(restored))
      const hasAnalysis = session.messages.some(
        (message) =>
          message.message_type === "analysis" || hasPersistedAnalysisPayload(message.data)
      )
      if (hasAnalysis) {
        setAgentMessageBoardWaitingForFileAnalysisResponse(String(id), false)
      }
      setApprovalRequired(Boolean(session.approval_required))
      setFollowUpAvailable(Boolean(session.follow_up_available))
      setFollowUpStarted(Boolean(session.follow_up_started))
      if (typeof window !== "undefined") {
        window.dispatchEvent(new CustomEvent("agent:session-state", {
          detail: {
            sessionId: String(session.id),
            title: (session.title && session.title.trim()) || "Chat",
            approvalRequired: Boolean(session.approval_required),
          }
        }))
      }
      const lastTaskCreated = [...session.messages].reverse().find(
        (m) => m.message_type === "task_created" || (m.data?.task_ids && m.data.task_ids.length > 0)
      )
      const created = (lastTaskCreated as any)?.data?.created_tasks
      if (Array.isArray(created)) {
        const idxs = created
          .map((c: any) => Number(c?.index))
          .filter((n: unknown) => typeof n === "number" && Number.isFinite(n))
        setGeneratedTaskIndexes(Array.from(new Set(idxs)))
        setSkippedTaskIndexes((prev) => prev.filter((i) => !idxs.includes(i)))
        const pairs = created
          .map((c: any) => [Number(c?.index), c?.task_slug ?? c?.task_id] as const)
          .filter(([idx, tid]: readonly [number, any]) => Number.isFinite(idx) && tid != null)
        setCreatedTaskIdByIndex(Object.fromEntries(pairs))
      }
      if (lastTaskCreated) {
        setStepState((prev) => ({ ...prev, tasksCreated: true }))
        setTaskGenerationStatus("completed")
      }
      const lastDecisionCreated = [...session.messages].reverse().find(
        (m) =>
          m.message_type === "decision_draft" ||
          (Array.isArray(m.data?.decision_ids) && m.data.decision_ids.length > 0)
      )
      if (lastDecisionCreated) {
        setStepState((prev) => ({ ...prev, decisionsCreated: true }))
        setCreatedDecisionByRef(
          mapCreatedDecisionsByRef(lastDecisionCreated.data?.created_decisions)
        )
        setDecisionGenerationStatus("completed")
      }
    } catch {
      // ignore refresh failures; next restore/poll can retry
    }
  }, [])

  const loadSessionById = useCallback(async (id: string) => {
    const requestId = ++sessionLoadRequestRef.current
    invalidateActiveStreams()
    resetTransientChatUiState()
    try {
      const session = await AgentAPI.getSession(id)
      if (requestId !== sessionLoadRequestRef.current) return
      applySessionState(session)
    } catch {
      if (requestId !== sessionLoadRequestRef.current) return
      if (String(sessionIdRef.current) === String(id)) {
        sessionStorage.removeItem("agent-session-id")
        setSessionId(null)
      }
    }
  }, [applySessionState, invalidateActiveStreams, resetTransientChatUiState, setSessionId])

  // Abort SSE on unmount
  useEffect(() => {
    return () => {
      const sid = sessionIdRef.current
      const streaming = isStreamingRef.current
      // Persist "waiting" so revisit can show the thinking bubble once render finishes.
      if (sid && streaming) {
        setAgentMessageBoardWaitingForFileAnalysisResponse(String(sid), true)
      }
      abortRef.current?.abort()
    }
  }, [])

  useEffect(() => {
    if (!sessionId) return
    const pendingWorkflowRunIds = getPendingMiroWorkflowRunIds(messages)
    if (pendingWorkflowRunIds.length === 0) return

    const intervalId = window.setInterval(async () => {
      try {
        const session = await AgentAPI.getSession(sessionId)
        if (String(sessionIdRef.current) !== String(sessionId)) return
        applySessionState(session)
      } catch {
        // ignore polling failures; next cycle can retry
      }
    }, 5000)

    return () => window.clearInterval(intervalId)
  }, [sessionId, messages, applySessionState])

  // Restore session on mount
  useEffect(() => {
    const storedId = sessionStorage.getItem("agent-session-id")
    if (storedId) {
      void loadSessionById(storedId)
    } else {
      // If there is no active session, initialize the toggle from the persisted preference.
      setApprovalRequired(getApprovalPref())
    }
  }, [getApprovalPref, loadSessionById])

  // Calendar context staged while the panel was closed (Calendar → Ask Agent).
  useEffect(() => {
    const resetForPreload = () => {
      sessionLoadRequestRef.current += 1
      invalidateActiveStreams()
      resetTransientChatUiState()
      setSessionId(null)
      sessionStorage.removeItem("agent-session-calendar-context")
      sessionStorage.removeItem("agent-session-spreadsheet-context")
      setSessionSpreadsheetContext(null)
      // Leaving any pattern/pivot generation mode when a preload takes over.
      setPatternGenerationSheetId(null)
      setPatternGenerationSheetName(null)
      setPatternGenerationSpreadsheetName(null)
      patternGenerationSessionIdRef.current = null
      setPivotGenerationSpreadsheetId(null)
      setPivotGenerationSheetId(null)
      setPivotGenerationSheetName(null)
      setPivotGenerationSpreadsheetName(null)
      pivotGenerationSessionIdRef.current = null
      setHistoricalGenerationMode(null)
      setMessages([])
      setHasStarted(false)
      setFollowUpAvailable(false)
      setFollowUpStarted(false)
      setSessionTitle("Chat")
      setApprovalRequired(getApprovalPref())
      setStepState({
        analysisComplete: false,
        anomaliesConfirmed: false,
        tasksCreated: false,
        decisionsCreated: false,
      })
      setGeneratedTaskIndexes([])
      setSkippedTaskIndexes([])
      setCreatedTaskIdByIndex({})
      setTaskGenerationStatus("idle")
    }

    const onPanelOpened = () => {
      const calendarPreload = consumeCalendarPreload()
      if (calendarPreload) {
        resetForPreload()
        setSessionCalendarContext(calendarPreload.context)
        setPendingCalendarPreload(calendarPreload)
        setPendingSpreadsheetPreload(null)
        setSessionSpreadsheetContext(null)
        _calendarAutoSendFired = false
        return
      }

      const spreadsheetPreload = consumeSpreadsheetPreload()
      if (!spreadsheetPreload) {
        return
      }
      resetForPreload()
      setSessionCalendarContext(null)
      setPendingCalendarPreload(null)
      setPendingSpreadsheetPreload(spreadsheetPreload)
      setSessionSpreadsheetContext(sheetWorkContextFromPreload(spreadsheetPreload))
      _spreadsheetAutoSendFired = false
    }

    // Draft → Agent (Open in Agent / Ask Agent from a draft).
    const onPanelOpenedDraft = () => {
      const preload = consumeDraftPreload()
      if (!preload) {
        return
      }
      sessionLoadRequestRef.current += 1
      invalidateActiveStreams()
      resetTransientChatUiState()
      setSessionId(null)
      sessionStorage.removeItem("agent-session-draft-context")
      setMessages([])
      setHasStarted(false)
      setFollowUpAvailable(false)
      setFollowUpStarted(false)
      setSessionTitle("Chat")
      setApprovalRequired(getApprovalPref())
      setSessionDraftContext(preload.context)
      setPendingDraftPreload(preload)
      _draftAutoSendFired = false
    }

    const onPanelOpenedHandler = () => {
      onPanelOpened()
      onPanelOpenedDraft()
    }

    window.addEventListener(AGENT_PANEL_OPENED_EVENT, onPanelOpenedHandler)
    return () => window.removeEventListener(AGENT_PANEL_OPENED_EVENT, onPanelOpenedHandler)
  }, [
    getApprovalPref,
    invalidateActiveStreams,
    resetTransientChatUiState,
    setSessionId,
  ])

  // Listen for sidebar events
  useEffect(() => {
    // Proactively prompt for AI consent when entering a generation mode for a
    // sheet the user hasn't consented to yet, instead of waiting for the first
    // request to be refused. The AI_CONSENT_REQUIRED catch blocks stay as a
    // backstop (restored sessions, revoked consent, races).
    const promptSpreadsheetConsentIfNeeded = (sheetId: number | null | undefined) => {
      if (sheetId == null) return
      AiConsentAPI.get({ sheetId })
        .then((status) => {
          if (status.consented) return
          setConsentTarget((cur) => cur ?? { sheetId })
          setAiConsentRetry((cur) => cur ?? (() => {}))
        })
        .catch(() => {
          /* network / access error — the reactive 403 path will handle it */
        })
    }

    const handleNewChat = () => {
      sessionLoadRequestRef.current += 1
      invalidateActiveStreams()
      resetTransientChatUiState()
      setSessionId(null)
      setProjectId(null)
      sessionStorage.removeItem("agent-session-calendar-context")
      sessionStorage.removeItem("agent-session-draft-context")
      sessionStorage.removeItem("agent-session-spreadsheet-context")
      setSessionSpreadsheetContext(null)
      setIsWorkflowGenerating(false)
      setPatternGenerationSheetId(null)
      setPatternGenerationSheetName(null)
      setPatternGenerationSpreadsheetName(null)
      patternGenerationSessionIdRef.current = null
      setPivotGenerationSpreadsheetId(null)
      setPivotGenerationSheetId(null)
      setPivotGenerationSheetName(null)
      setPivotGenerationSpreadsheetName(null)
      pivotGenerationSessionIdRef.current = null
      setHistoricalGenerationMode(null)
      setMessages([])
      setSessionCalendarContext(null)
      setSessionDraftContext(null)
      setPendingDraftPreload(null)
      setHasStarted(false)
      setFollowUpAvailable(false)
      setFollowUpStarted(false)
      setSessionTitle("Chat")
      setApprovalRequired(false)
setStepState({
      analysisComplete: false,
      anomaliesConfirmed: false,
      tasksCreated: false,
      decisionsCreated: false,
    })
    }

    const handleLoadSession = (e: Event) => {
      const detail = (e as CustomEvent).detail
      if (!detail?.sessionId) return
      void loadSessionById(String(detail.sessionId))
    }

    const handlePatternGenerationMode = (e: Event) => {
      const detail = (e as CustomEvent<{ sheetId?: number; sheetName?: string; spreadsheetName?: string }>).detail
      sessionLoadRequestRef.current += 1
      invalidateActiveStreams()
      resetTransientChatUiState()
      setSessionId(null)
      sessionStorage.removeItem("agent-session-calendar-context")
      sessionStorage.removeItem("agent-session-spreadsheet-context")
      setSessionSpreadsheetContext(null)
      setIsWorkflowGenerating(false)
      pivotGenerationSessionIdRef.current = null
      setPivotGenerationSpreadsheetId(null)
      setPivotGenerationSheetId(null)
      setPivotGenerationSheetName(null)
      setPivotGenerationSpreadsheetName(null)
      patternGenerationSessionIdRef.current = null
      setHistoricalGenerationMode(null)
      setPatternGenerationSheetId(detail?.sheetId ?? null)
      setPatternGenerationSheetName(detail?.sheetName ?? null)
      setPatternGenerationSpreadsheetName(detail?.spreadsheetName ?? null)
      setMessages([
        {
          id: `pattern-mode-${Date.now()}`,
          role: "assistant",
          type: "text",
          content:
            "You are now in **Pattern Generation Mode**. Please describe the action you would like to implement for the current spreadsheet — for example, \"add a column K that sums I and J, then highlight rows where K > 1000\". I will generate the pattern steps automatically.",
        },
      ])
      setSessionCalendarContext(null)
      setHasStarted(true)
      setFollowUpAvailable(false)
      setFollowUpStarted(false)
      setSessionTitle("Pattern Generation")
      setApprovalRequired(false)
      setStepState({
        analysisComplete: false,
        anomaliesConfirmed: false,
        tasksCreated: false,
        decisionsCreated: false,
      })
      promptSpreadsheetConsentIfNeeded(detail?.sheetId)
    }

    const handlePivotGenerationMode = (e: Event) => {
      const detail = (e as CustomEvent<{
        spreadsheetId?: string | number
        sheetId?: number
        sheetName?: string
        spreadsheetName?: string
      }>).detail
      sessionLoadRequestRef.current += 1
      invalidateActiveStreams()
      resetTransientChatUiState()
      setSessionId(null)
      sessionStorage.removeItem("agent-session-calendar-context")
      sessionStorage.removeItem("agent-session-spreadsheet-context")
      setSessionSpreadsheetContext(null)
      setIsWorkflowGenerating(false)
      patternGenerationSessionIdRef.current = null
      setPatternGenerationSheetId(null)
      setPatternGenerationSheetName(null)
      setPatternGenerationSpreadsheetName(null)
      pivotGenerationSessionIdRef.current = null
      setHistoricalGenerationMode(null)
      setPivotGenerationSpreadsheetId(detail?.spreadsheetId ?? null)
      setPivotGenerationSheetId(detail?.sheetId ?? null)
      setPivotGenerationSheetName(detail?.sheetName ?? null)
      setPivotGenerationSpreadsheetName(detail?.spreadsheetName ?? null)
      setMessages([
        {
          id: `pivot-mode-${Date.now()}`,
          role: "assistant",
          type: "text",
          content:
            "You are now in **Pivot Table Generation Mode**. Describe the pivot table you want — for example, \"summarize total revenue by region and month\". I will generate it as a new sheet in this spreadsheet.",
        },
      ])
      setSessionCalendarContext(null)
      setHasStarted(true)
      setFollowUpAvailable(false)
      setFollowUpStarted(false)
      setSessionTitle("Pivot Table Generation")
      setApprovalRequired(false)
      setStepState({
        analysisComplete: false,
        anomaliesConfirmed: false,
        tasksCreated: false,
        decisionsCreated: false,
      })
      promptSpreadsheetConsentIfNeeded(detail?.sheetId)
    }

    window.addEventListener("agent:new-chat", handleNewChat)
    window.addEventListener("agent:load-session", handleLoadSession)
    window.addEventListener("agent:pattern-generation-mode", handlePatternGenerationMode)
    window.addEventListener("agent:pivot-generation-mode", handlePivotGenerationMode)
    return () => {
      window.removeEventListener("agent:new-chat", handleNewChat)
      window.removeEventListener("agent:load-session", handleLoadSession)
      window.removeEventListener("agent:pattern-generation-mode", handlePatternGenerationMode)
      window.removeEventListener("agent:pivot-generation-mode", handlePivotGenerationMode)
    }
  }, [invalidateActiveStreams, loadSessionById, resetTransientChatUiState, setSessionId])

  /** Append a new message and return its id */
  const addMessage = useCallback((msg: ChatMessage) => {
    setMessages((prev) => [...prev, msg])
    return msg.id
  }, [])

  /** Update an existing message by id */
  const updateMessage = useCallback((id: string, updates: Partial<ChatMessage>) => {
    setMessages((prev) =>
      prev.map((m) => (m.id === id ? { ...m, ...updates } : m))
    )
  }, [])

  /** Handle file upload — calls upload-analyze SSE endpoint */
  const handleFileUpload = useCallback(async (file: File, userContext?: string) => {
    if (generationOutputsSelected.length === 0) return
    const outputsForUpload = [...generationOutputsSelected]
    requestedGenerationOutputsRef.current = outputsForUpload
    setRequestedGenerationOutputs(outputsForUpload)

    setHasStarted(true)
    // Reset workflow state so a new upload always starts from analysis
setStepState({
      analysisComplete: false,
      anomaliesConfirmed: false,
      tasksCreated: false,
      decisionsCreated: false,
    })
    setGeneratedTaskIndexes([])
    setSkippedTaskIndexes([])
    setCreatedTaskIdByIndex({})
    autoExternalActionsTriggeredRef.current = false
    autoActionQueueRef.current = []

    // Show user message
    const userMsgId = `user-${Date.now()}`
    addMessage({
      id: userMsgId,
      role: "user",
      content: spreadsheetUploadNavigateLabel(file.name),
      type: "file_uploaded",
      fileName: file.name,
    })

    // Show thinking placeholder
    const aiMsgId = `ai-${Date.now()}`
    addMessage({
      id: aiMsgId,
      role: "assistant",
      content: AGENT_MESSAGES.CHAT_THINKING,
      type: "text",
    })

    setIsStreaming(true)

    let contentParts: string[] = []
    let analysisData: AnalysisResult | null = null
    let columnMappingReceived = false

    let sid = sessionId
    if (!sid) {
      try {
        const session = await AgentAPI.createSession({ approval_required: getApprovalPref() })
        sid = String(session.id)
        setSessionId(sid)
        setSessionTitle("New Chat")
        setApprovalRequired(Boolean(session.approval_required))
        window.dispatchEvent(new CustomEvent("agent:sessions-changed"))
      } catch {
        updateMessage(aiMsgId, { content: AGENT_MESSAGES.SESSION_CREATE_FAILED, type: "error" })
        setIsStreaming(false)
        return
      }
    }

    const streamToken = activeStreamTokenRef.current
    const requestSessionId = String(sid)
    setAgentMessageBoardRenderEffectsCompletedOnQuit(requestSessionId, false)
    setAgentMessageBoardWaitingForFileAnalysisResponse(requestSessionId, true)

    abortRef.current = AgentAPI.uploadAndAnalyze(
      file,
      sid,
      outputsForUpload,
      userContext || null,
      (event: SSEEvent) => {
        if (activeStreamTokenRef.current !== streamToken) return
        if (String(sessionIdRef.current) !== requestSessionId) return

        if (event.type === "file_uploaded") {
          const fromServer = event.data?.generation_outputs
          if (Array.isArray(fromServer) && fromServer.length > 0) {
            requestedGenerationOutputsRef.current = fromServer as GenerationOutputKey[]
            setRequestedGenerationOutputs(fromServer as GenerationOutputKey[])
          }
          const spreadsheetId = event.data?.spreadsheet_id
          const navigateHref =
            event.data?.url ??
            (spreadsheetId != null && event.data?.project_id != null
              ? `/spreadsheets/${spreadsheetId}?project_id=${event.data.project_id}`
              : undefined)
          updateMessage(userMsgId, {
            spreadsheetId,
            sheetId: event.data?.sheet_id,
          })
          // File confirmed uploaded — update thinking message
          updateMessage(aiMsgId, {
            content: event.content || "File uploaded. Analyzing...",
            navigateTo: navigateHref ? "spreadsheet" : undefined,
            navigateLabel: navigateHref
              ? spreadsheetUploadNavigateLabel(file.name)
              : undefined,
            navigateHref,
          })
        } else if (event.type === "step_progress" && event.data) {
          const { step_order, step_name, total_steps, status } = event.data
          if (step_order != null && step_name && total_steps) {
            setStepProgress((prev) => {
              const updated = [...prev]
              for (const s of updated) {
                if (s.order < step_order && s.status === "running") {
                  s.status = "completed"
                }
              }
              const existing = updated.find((s) => s.order === step_order)
              if (existing) {
                existing.status = (status as StepExecutionStatus) || "running"
                existing.name = step_name
              } else {
                while (updated.length < total_steps) {
                  const order = updated.length + 1
                  updated.push({
                    order,
                    name: order === step_order ? step_name : `Step ${order}`,
                    status: order < step_order ? "completed" : order === step_order ? ((status as StepExecutionStatus) || "running") : "pending",
                  })
                }
              }
              return updated
            })
          }
        } else if (event.type === "column_mapping" && event.data) {
          columnMappingReceived = true
          const detectionData = event.data as unknown as ColumnDetectionData
          // Auto-confirm silently — store mapping to trigger after stream ends
          pendingAutoConfirmRef.current = detectionData.mappings
          updateMessage(aiMsgId, {
            content: event.content || "Column detection complete. Generating success criteria...",
            type: "text",
          })
        } else if (event.type === "text") {
          if (!columnMappingReceived) {
            contentParts.push(event.content || "")
            updateMessage(aiMsgId, { content: contentParts.join("\n") })
          }
        } else if (event.type === "analysis") {
          setAgentMessageBoardWaitingForFileAnalysisResponse(requestSessionId, false)
          contentParts.push(event.content || "")
          analysisData = (event.data as unknown as AnalysisResult) || null
          const wantsTasks = requestedGenerationOutputsRef.current.includes("recommended_tasks")
          const wantsDecisions =
            requestedGenerationOutputsRef.current.includes("recommended_decision_tree")
          const tasks = wantsTasks ? analysisData?.recommended_tasks : undefined
          latestRecommendedTasksRef.current = tasks || null
          syncDecisionTreeNodeCount(analysisData)
          setFollowUpAvailable(true)
          setFollowUpStarted(false)
          const anomConfirmed =
            Boolean(analysisData?.anomalies_confirmed) ||
            (analysisData?.anomalies?.length ?? 0) === 0
          setStepState((prev) => ({
            ...prev,
            analysisComplete: true,
            anomaliesConfirmed: anomConfirmed,
          }))
          setGeneratedTaskIndexes([])
          setSkippedTaskIndexes([])
          setCreatedTaskIdByIndex({})
          setCreatedDecisionByRef({})
          setDecisionGenerationStatus("idle")
          updateMessage(aiMsgId, {
            content: contentParts.join("\n"),
            type: "analysis",
            anomalies: wantsTasks ? analysisData?.anomalies : undefined,
            anomaliesConfirmed: anomConfirmed,
            recommendedTasks: tasks,
            recommendedDecisionTree: pickRecommendedDecisionTree(analysisData, wantsDecisions),
          })
          if (wantsTasks) {
            selectAllRecommendedTasks(tasks?.length ?? 0)
          }
          if (wantsDecisions) {
            const refs =
              analysisData?.recommended_decision_tree?.nodes?.map((node) => node.ref) ?? []
            selectAllRecommendedDecisionRefs(refs)
          }
          queueAutoExternalActionsAfterAnalysis({
            analysis: analysisData,
            generationOutputs: requestedGenerationOutputsRef.current,
          })
        } else if (event.type === "calendar_events" && event.data) {
          const events =
            (event.data.calendar_events as SuggestedCalendarEvent[] | undefined) ?? []
          updateMessage(aiMsgId, { calendarEvents: events })
        } else if (event.type === "confirmation_request") {
          if (!columnMappingReceived) {
            updateMessage(aiMsgId, {
              content: event.content || "Please confirm to continue.",
              type: "confirmation_request",
            })
          }
        } else if (event.type === "approval_request" && event.data) {
          const d = event.data as Record<string, unknown>
          const approvalId = d.approval_id
          const kind = String(d.kind ?? "")
          if (typeof approvalId !== "string") return

          const pending: PendingExternalApproval = {
            id: approvalId,
            kind,
            draft: (d.draft as Record<string, unknown>) ?? {},
          }

          if (kind === "task") {
            applyPendingTaskApproval(pending)
          } else if (kind === "decision_tree") {
            applyPendingDecisionApproval(pending)
          }
        } else if (event.type === "decision_draft" && event.data) {
          applyDecisionDraftResult(event.data as Record<string, unknown>)
          updateMessage(aiMsgId, {
            content: contentParts.join("\n"),
            type: "decisions_created",
            navigateTo: "decisions",
            navigateLabel: "Go to Decisions",
          })
        } else if (event.type === "follow_up_prompt") {
          contentParts.push(event.content || "")
          setFollowUpAvailable(false)
          setFollowUpStarted(true)
          updateMessage(aiMsgId, {
            content: contentParts.join("\n"),
            isFollowUpPrompt: true,
          })
        } else if (event.type === "error") {
          setAgentMessageBoardWaitingForFileAnalysisResponse(requestSessionId, false)
          updateMessage(aiMsgId, { content: event.content || "An error occurred.", type: "error" })
          const errData = event.data as
            | { code?: string; spreadsheet_id?: number }
            | undefined
          if (errData?.code === "AI_CONSENT_REQUIRED") {
            const preload = lastSpreadsheetPreloadRef.current
            const ssId = errData.spreadsheet_id ?? preload?.spreadsheetId
            setConsentTarget(
              typeof ssId === "number" && Number.isFinite(ssId)
                ? { spreadsheetId: ssId }
                : preload?.sheetId != null
                  ? { sheetId: preload.sheetId }
                  : null
            )
            setAiConsentRetry(() => () => {
              if (!preload) return
              _spreadsheetAutoSendFired = false
              handleSendMessageRef.current?.(
                preload.message, undefined, undefined, undefined, undefined, undefined, preload,
              )
            })
          }
        } else if (event.type === "done") {
          // Stream ended: stop the revisit thinking state.
          setAgentMessageBoardWaitingForFileAnalysisResponse(requestSessionId, false)
          // Capture session_id from done event
          const sid = event.data?.session_id
          if (sid) {
            setSessionId(sid)
            window.dispatchEvent(new CustomEvent("agent:sessions-changed"))
            void refreshFollowUpState(sid)
          }
          // Attach final step progress to the message
          setStepProgress((prev) => {
            if (prev.length > 0) {
              const final = prev.map((s) => ({
                ...s,
                status: s.status === "running" ? "completed" as const : s.status,
              }))
              updateMessage(aiMsgId, { stepProgress: final })
              return final
            }
            return prev
          })
          // Auto-confirm column mapping if pending (workflow paused at await_confirmation)
          const pendingMapping = pendingAutoConfirmRef.current
          if (pendingMapping) {
            pendingAutoConfirmRef.current = null
            setIsStreaming(false)
            setTimeout(() => {
              handleConfirmColumnsRef.current?.(pendingMapping)
            }, 100)
          }
        }
      },
      (error) => {
        if (activeStreamTokenRef.current !== streamToken) return
        if (String(sessionIdRef.current) !== requestSessionId) return
        setAgentMessageBoardWaitingForFileAnalysisResponse(requestSessionId, false)
        updateMessage(aiMsgId, { content: `Error: ${error.message}`, type: "error" })
        setIsStreaming(false)
      },
      () => {
        if (activeStreamTokenRef.current !== streamToken) return
        if (String(sessionIdRef.current) !== requestSessionId) return
        setAgentMessageBoardWaitingForFileAnalysisResponse(requestSessionId, false)
        void refreshSession(requestSessionId)
        void refreshFollowUpState(requestSessionId)
        setIsStreaming(false)
        if (embeddedInFloating) {
          window.dispatchEvent(new CustomEvent("agent:sessions-changed"))
          void AgentAPI.updateSession(requestSessionId, { last_read_at: new Date().toISOString() }).catch(() => {
            /* ignore */
          })
        }
      }
    )
  }, [
    sessionId,
    addMessage,
    updateMessage,
    setSessionId,
    refreshFollowUpState,
    refreshSession,
    getApprovalPref,
    embeddedInFloating,
    queueAutoExternalActionsAfterAnalysis,
    generationOutputsSelected,
  ])

  /** Confirm detected column mapping and resume paused workflow */
  const handleConfirmColumns = useCallback(async (mapping: Record<string, string>) => {
    const sid = sessionIdRef.current
    if (!sid) return

    const aiMsgId = `ai-${Date.now()}`
    addMessage({ id: aiMsgId, role: "assistant", content: AGENT_MESSAGES.CHAT_THINKING, type: "text" })
    setIsStreaming(true)
    setGeneratedTaskIndexes([])
    setSkippedTaskIndexes([])
    setCreatedTaskIdByIndex({})
    // Preserve step names from the upload phase; reset steps 3+ to pending
    setStepProgress((prev) =>
      prev.map((s) => ({
        ...s,
        status: s.order <= 2 ? ("completed" as const) : ("pending" as const),
      }))
    )
    let contentParts: string[] = []
    const streamToken = activeStreamTokenRef.current
    const requestSessionId = String(sid)

    abortRef.current = AgentAPI.sendMessage(
      sid,
      { message: "confirm_columns", action: "confirm_columns", column_mapping: mapping },
      (event: SSEEvent) => {
        if (activeStreamTokenRef.current !== streamToken) return
        if (String(sessionIdRef.current) !== requestSessionId) return
        if (event.type === "done") return

        if (event.type === "step_progress" && event.data) {
          const { step_order, step_name, total_steps, status } = event.data
          if (step_order != null && step_name && total_steps) {
            setStepProgress((prev) => {
              const updated = [...prev]
              for (const s of updated) {
                if (s.order < step_order && s.status === "running") {
                  s.status = "completed"
                }
              }
              const existing = updated.find((s) => s.order === step_order)
              if (existing) {
                existing.status = (status as StepExecutionStatus) || "running"
                existing.name = step_name
              } else {
                while (updated.length < total_steps) {
                  const order = updated.length + 1
                  updated.push({
                    order,
                    name: order === step_order ? step_name : `Step ${order}`,
                    status: order < step_order ? "completed" : order === step_order ? ((status as StepExecutionStatus) || "running") : "pending",
                  })
                }
              }
              return updated
            })
          }
          return
        }

        if (event.content && event.type !== "miro_status" && event.type !== "follow_up_prompt") {
          contentParts.push(event.content)
          updateMessage(aiMsgId, { content: contentParts.join("\n") })
        }

        if (event.type === "analysis" && event.data) {
          const data = event.data as unknown as AnalysisResult
          const wantsTasks = requestedGenerationOutputsRef.current.includes("recommended_tasks")
          const wantsDecisions =
            requestedGenerationOutputsRef.current.includes("recommended_decision_tree")
          const tasks = wantsTasks ? data.recommended_tasks : undefined
          latestRecommendedTasksRef.current = tasks || null
          syncDecisionTreeNodeCount(data)
          setFollowUpAvailable(true)
          setFollowUpStarted(false)
          const anomConfirmed =
            Boolean(data.anomalies_confirmed) || (data.anomalies?.length ?? 0) === 0
          setStepState((prev) => ({
            ...prev,
            analysisComplete: true,
            anomaliesConfirmed: anomConfirmed,
          }))
          setGeneratedTaskIndexes([])
          setSkippedTaskIndexes([])
          setCreatedTaskIdByIndex({})
          setCreatedDecisionByRef({})
          setDecisionGenerationStatus("idle")
          updateMessage(aiMsgId, {
            type: "analysis",
            anomalies: wantsTasks ? data.anomalies : undefined,
            anomaliesConfirmed: anomConfirmed,
            recommendedTasks: tasks,
            recommendedDecisionTree: pickRecommendedDecisionTree(data, wantsDecisions),
          })
          if (wantsTasks) {
            selectAllRecommendedTasks(tasks?.length ?? 0)
          }
          if (wantsDecisions) {
            const refs = data.recommended_decision_tree?.nodes?.map((node) => node.ref) ?? []
            selectAllRecommendedDecisionRefs(refs)
          }
          queueAutoExternalActionsAfterAnalysis({
            analysis: data,
            generationOutputs: requestedGenerationOutputsRef.current,
          })
        }
        if (event.type === "approval_request" && event.data) {
          const d = event.data as Record<string, unknown>
          const approvalId = d.approval_id
          const kind = String(d.kind ?? "")
          if (typeof approvalId !== "string") return

          const pending: PendingExternalApproval = {
            id: approvalId,
            kind,
            draft: (d.draft as Record<string, unknown>) ?? {},
          }

          if (kind === "task") {
            applyPendingTaskApproval(pending)
          } else if (kind === "decision_tree") {
            applyPendingDecisionApproval(pending)
          }
        }
        if (event.type === "task_created" && event.data) {
          setStepState((prev) => ({ ...prev, tasksCreated: true }))
          setPendingTaskApproval(null)
          setTasksApprovalGenerating(false)
          setTaskGenerationStatus("completed")
          const created = (event.data as any)?.created_tasks
          if (Array.isArray(created)) {
            const idxs = created
              .map((c: any) => Number(c?.index))
              .filter((n: unknown) => typeof n === "number" && Number.isFinite(n))
            setGeneratedTaskIndexes(Array.from(new Set(idxs)))
            setSkippedTaskIndexes((prev) => prev.filter((i) => !idxs.includes(i)))
            const pairs = created
              .map((c: any) => [Number(c?.index), c?.task_slug ?? c?.task_id] as const)
              .filter(([idx, tid]: readonly [number, any]) => Number.isFinite(idx) && tid != null)
            setCreatedTaskIdByIndex(Object.fromEntries(pairs))
          } else {
            const tasksLen = latestRecommendedTasksRef.current?.length ?? 0
            if (tasksLen > 0) setGeneratedTaskIndexes(Array.from({ length: tasksLen }, (_, i) => i))
          }
          updateMessage(aiMsgId, {
            content: contentParts.join("\n"),
            type: "tasks_created",
            navigateTo: "tasks",
            navigateLabel: "Go to Tasks",
          })
        }
        if (event.type === "decision_draft" && event.data) {
          applyDecisionDraftResult(event.data as Record<string, unknown>)
          updateMessage(aiMsgId, {
            content: contentParts.join("\n"),
            type: "decisions_created",
            navigateTo: "decisions",
            navigateLabel: "Go to Decisions",
          })
        }
      },
      (error) => {
        if (activeStreamTokenRef.current !== streamToken) return
        if (String(sessionIdRef.current) !== requestSessionId) return
        updateMessage(aiMsgId, { content: `Error: ${error.message}`, type: "error" })
        setIsStreaming(false)
      },
      () => {
        if (activeStreamTokenRef.current !== streamToken) return
        if (String(sessionIdRef.current) !== requestSessionId) return
        void refreshSession(requestSessionId)
        void refreshFollowUpState(requestSessionId)
        setStepProgress((prev) => {
          if (prev.length > 0) {
            const final = prev.map((s) => ({
              ...s,
              status: s.status === "running" ? "completed" as const : s.status,
            }))
            updateMessage(aiMsgId, { stepProgress: final })
            stepProgressMsgIdRef.current = aiMsgId
            return final
          }
          return prev
        })
        setIsStreaming(false)
        if (embeddedInFloating) {
          window.dispatchEvent(new CustomEvent("agent:sessions-changed"))
          void AgentAPI.updateSession(requestSessionId, { last_read_at: new Date().toISOString() }).catch(() => {
            /* ignore */
          })
        }
      }
    )
  }, [addMessage, updateMessage, refreshFollowUpState, refreshSession, embeddedInFloating, queueAutoExternalActionsAfterAnalysis])

  // Keep ref in sync so handleFileUpload's done handler can call the latest version
  handleConfirmColumnsRef.current = handleConfirmColumns

  /**
   * Confirm the user's anomaly review. Persists the reviewed list, locks the
   * card read-only, and (only if anomalies were included) runs the downstream
   * task/miro actions. All-excluded confirms but creates nothing.
   */
  const handleConfirmAnomalies = useCallback(
    (messageId: string, reviewed: import("@/types/agent").ReviewedAnomaly[]) => {
      const sid = sessionIdRef.current
      if (!sid) return
      const requestSessionId = String(sid)

      AgentAPI.sendMessage(
        sid,
        { message: "confirm_anomalies", action: "confirm_anomalies", reviewed_anomalies: reviewed },
        (event: SSEEvent) => {
          if (String(sessionIdRef.current) !== requestSessionId) return
          if (event.type === "anomalies_confirmed") {
            const data = (event.data as unknown as AnalysisResult) || null
            const reviewedList = data?.reviewed_anomalies ?? []
            const includedCount = reviewedList.filter((a) => a.included !== false).length
            setStepState((prev) => ({ ...prev, anomaliesConfirmed: true }))
            updateMessage(messageId, {
              anomaliesConfirmed: true,
              ...(reviewedList.length > 0 ? { anomalies: reviewedList } : {}),
            })
            runPostConfirmActions(includedCount > 0)
          } else if (event.type === "error") {
            // Leave the card editable for retry; surface the error inline.
            addMessage({
              id: `ai-anom-err-${Date.now()}`,
              role: "assistant",
              content: event.content || "Failed to confirm anomalies.",
              type: "error",
            })
          }
        }
      )
    },
    [addMessage, updateMessage, runPostConfirmActions]
  )

  /** Re-upload: reset to welcome screen so the user can upload a different file */
  const handleReupload = useCallback(() => {
    sessionIdRef.current = null
    stepProgressMsgIdRef.current = null
    setSessionId(null)
    setMessages([])
    setHasStarted(false)
    setIsStreaming(false)
    setFollowUpAvailable(false)
    setFollowUpStarted(false)
    setStepProgress([])
setStepState({
      analysisComplete: false,
      anomaliesConfirmed: false,
      tasksCreated: false,
      decisionsCreated: false,
    })
    setGeneratedTaskIndexes([])
    setSkippedTaskIndexes([])
    setCreatedDecisionByRef({})
    setDecisionGenerationStatus("idle")
    latestRecommendedTasksRef.current = null
    latestDecisionTreeNodeCountRef.current = 0
    autoExternalActionsTriggeredRef.current = false
    autoActionQueueRef.current = []
    requestedGenerationOutputsRef.current = []
    setRequestedGenerationOutputs([])
    abortRef.current?.abort()
  }, [setSessionId])

  /** Resume a workflow paused at await_confirmation (Continue button). */
  const handleResumeWorkflow = useCallback((confirmMessageId: string) => {
    void handleSendMessageRef.current?.(
      "",
      undefined,
      undefined,
      undefined,
      "resume_workflow",
      confirmMessageId,
    )
  }, [])

  /** Handle text message send. Pass workflowId when user confirms an AI-matched workflow. */
  const handleSendMessage = useCallback(async (
    text: string,
    calendarContext?: Record<string, unknown>,
    userContext?: string,
    workflowId?: string,
    action?: AgentAction,
    reuseAiMsgId?: string,
    spreadsheetPreload?: SpreadsheetPreload,
  ) => {
    // Pivot generation mode: call the NL→PivotConfig API, then create the pivot
    // sheet using the same SpreadsheetAPI calls the manual "Create pivot sheet"
    // flow uses (page.tsx handleCreatePivotSheet), instead of the regular agent.
    if (pivotGenerationSpreadsheetId != null && pivotGenerationSheetId != null) {
      const userMsgId = `user-${Date.now()}`
      addMessage({ id: userMsgId, role: "user", content: text, type: "text" })
      const thinkingId = `ai-${Date.now()}`
      addMessage({ id: thinkingId, role: "assistant", content: "Generating pivot table…", type: "text" })

      if (!pivotGenerationSessionIdRef.current) {
        try {
          const session = await AgentAPI.createSession({ approval_required: getApprovalPref() })
          const sid = String(session.id)
          pivotGenerationSessionIdRef.current = sid
          const title = `Pivot Table Generation - ${formatSessionTimestamp()}`
          await AgentAPI.updateSession(sid, { title })
          setSessionTitle(title)
          window.dispatchEvent(new CustomEvent("agent:sessions-changed"))
        } catch {
          // Non-fatal — sidebar tracking failed but generation can still proceed
        }
      }

      // Persist the transcript on the backend session (best-effort) so the
      // sidebar history survives navigating away and reopening this board.
      const pivotSid = pivotGenerationSessionIdRef.current
      if (pivotSid) {
        AgentAPI.appendMessage(pivotSid, {
          role: "user",
          content: text,
          // Marker so reopening this session from history can rehydrate the
          // pivot workflow banner + mode (see findGenerationModeMarker).
          data: {
            generation_mode: "pivot_table_generation",
            spreadsheet_id: pivotGenerationSpreadsheetId,
            sheet_id: pivotGenerationSheetId,
            sheet_name: pivotGenerationSheetName,
            spreadsheet_name: pivotGenerationSpreadsheetName,
          },
        }).catch(() => {})
      }

      setIsWorkflowGenerating(true)
      try {
        const config = await PivotAPI.generatePivotConfig(pivotGenerationSheetId, text)
        const sheetsResp = await SpreadsheetAPI.listSheets(pivotGenerationSpreadsheetId)
        const existingNames = (sheetsResp.results || []).map((s) => s.name)
        const sheetName = generatePivotSheetName(existingNames)

        const newSheet = await SpreadsheetAPI.createSheet(pivotGenerationSpreadsheetId, { name: sheetName })
        await SpreadsheetAPI.resizeSheet(pivotGenerationSpreadsheetId, newSheet.id, 100, 26)
        await SpreadsheetAPI.upsertPivotConfig(pivotGenerationSpreadsheetId, newSheet.id, {
          sourceSheetId: pivotGenerationSheetId,
          rows: config.rows_config,
          columns: config.columns_config,
          values: config.values_config,
          showGrandTotalRow: config.show_grand_total_row,
        })
        await SpreadsheetAPI.recomputePivot(pivotGenerationSpreadsheetId, newSheet.id)

        // Tell an open spreadsheet detail view to pull in the freshly created
        // pivot sheet without a full reload.
        if (typeof window !== "undefined") {
          window.dispatchEvent(
            new CustomEvent("agent:pivot-sheet-created", {
              detail: { spreadsheetId: pivotGenerationSpreadsheetId, sheetId: newSheet.id },
            })
          )
        }

        const rowsSummary = config.rows_config.join(", ")
        const valuesSummary = config.values_config
          .map((v) => `${v.aggregation}(${v.field})`)
          .join(", ")
        const columnsSummary = config.columns_config.length
          ? ` split by ${config.columns_config
              .map((c) => (typeof c === "string" ? c : c.field))
              .join(", ")}`
          : ""

        const doneContent = `Done! I've created a new pivot sheet **"${sheetName}"** grouped by ${rowsSummary}${columnsSummary}, aggregating ${valuesSummary}.`
        updateMessage(thinkingId, {
          content: doneContent,
          navigateTo: "spreadsheet",
          navigateLabel: "Open spreadsheet",
          navigateHref: `/spreadsheets/${pivotGenerationSpreadsheetId}`,
        })
        if (pivotSid) {
          AgentAPI.appendMessage(pivotSid, { role: "assistant", content: doneContent }).catch(() => {})
        }
      } catch (err) {
        if (err instanceof AiConsentRequiredError) {
          updateMessage(thinkingId, {
            content: "AI analysis needs to be enabled for this spreadsheet first.",
            type: "error",
          })
          setConsentTarget(
            err.spreadsheetId != null
              ? { spreadsheetId: err.spreadsheetId }
              : pivotGenerationSheetId != null
                ? { sheetId: pivotGenerationSheetId }
                : null
          )
          setAiConsentRetry(() => () => handleSendMessageRef.current?.(text))
          setIsWorkflowGenerating(false)
          return
        }
        let msg = "Generation failed: Unable to generate a pivot table. Please try rephrasing your request."
        if (err instanceof PivotAgentError) {
          msg = `Invalid request: ${err.message}`
        } else if (err instanceof Error) {
          const axiosData = (err as any)?.response?.data
          if (axiosData?.error) {
            msg = `Generation failed: ${axiosData.error}`
          } else if (err.message) {
            msg = `Generation failed: ${err.message}`
          }
        }
        updateMessage(thinkingId, { content: msg, type: "error" })
        if (pivotSid) {
          AgentAPI.appendMessage(pivotSid, { role: "assistant", content: msg, message_type: "error" }).catch(() => {})
        }
      } finally {
        setIsWorkflowGenerating(false)
      }
      return
    }

    // Pattern generation mode: call the NL→steps API instead of the regular agent.
    if (patternGenerationSheetId != null) {
      const userMsgId = `user-${Date.now()}`
      addMessage({ id: userMsgId, role: "user", content: text, type: "text" })
      const thinkingId = `ai-${Date.now()}`
      addMessage({ id: thinkingId, role: "assistant", content: "Generating pattern steps…", type: "text" })

      // Create a sidebar session on first message. We use a separate ref (not setSessionId)
      // so the main session restore / polling flow is never triggered and cannot clobber
      // the local pattern-generation chat state.
      if (!patternGenerationSessionIdRef.current) {
        try {
          const session = await AgentAPI.createSession({ approval_required: getApprovalPref() })
          const sid = String(session.id)
          patternGenerationSessionIdRef.current = sid
          const title = `Pattern Generation - ${formatSessionTimestamp()}`
          await AgentAPI.updateSession(sid, { title })
          setSessionTitle(title)
          window.dispatchEvent(new CustomEvent("agent:sessions-changed"))
        } catch {
          // Non-fatal — sidebar tracking failed but generation can still proceed
        }
      }

      // Persist the transcript on the backend session (best-effort) so the
      // sidebar history survives navigating away and reopening this board.
      const patternSid = patternGenerationSessionIdRef.current
      if (patternSid) {
        AgentAPI.appendMessage(patternSid, {
          role: "user",
          content: text,
          // Marker so reopening this session from history can rehydrate the
          // pattern workflow banner + mode (see findGenerationModeMarker).
          data: {
            generation_mode: "pattern_generation",
            spreadsheet_id: null,
            sheet_id: patternGenerationSheetId,
            sheet_name: patternGenerationSheetName,
            spreadsheet_name: patternGenerationSpreadsheetName,
          },
        }).catch(() => {})
      }

      setIsWorkflowGenerating(true)
      try {
        const steps = await PatternAPI.generatePatternSteps(patternGenerationSheetId, text)
        const doneContent = `Done! I've generated **${steps.length} pattern step${steps.length !== 1 ? 's' : ''}** and added them to the Pattern Agent panel. You can review, reorder, or apply them from there.`
        updateMessage(thinkingId, {
          content: doneContent,
        })
        if (patternSid) {
          AgentAPI.appendMessage(patternSid, { role: "assistant", content: doneContent }).catch(() => {})
        }
        window.dispatchEvent(
          new CustomEvent('agent:pattern-steps-generated', {
            detail: { sheetId: patternGenerationSheetId, steps },
          })
        )
      } catch (err) {
        if (err instanceof AiConsentRequiredError) {
          updateMessage(thinkingId, {
            content: "AI analysis needs to be enabled for this spreadsheet first.",
            type: "error",
          })
          setConsentTarget(
            err.spreadsheetId != null
              ? { spreadsheetId: err.spreadsheetId }
              : patternGenerationSheetId != null
                ? { sheetId: patternGenerationSheetId }
                : null
          )
          setAiConsentRetry(() => () => handleSendMessageRef.current?.(text))
          setIsWorkflowGenerating(false)
          return
        }
        let msg = "Generation failed: Unable to generate pattern steps. Please try rephrasing your instruction."
        if (err instanceof PatternAgentError) {
          msg = `Invalid request: ${err.message}`
        } else if (err instanceof Error) {
          const axiosData = (err as any)?.response?.data
          if (axiosData?.error) {
            msg = `Generation failed: ${axiosData.error}`
          } else if (err.message) {
            msg = `Generation failed: ${err.message}`
          }
        }
        updateMessage(thinkingId, { content: msg, type: "error" })
        if (patternSid) {
          AgentAPI.appendMessage(patternSid, { role: "assistant", content: msg, message_type: "error" }).catch(() => {})
        }
      } finally {
        setIsWorkflowGenerating(false)
      }
      return
    }

    if (spreadsheetPreload) {
      if (generationOutputsSelected.length === 0) return
      const outputsForSpreadsheet = [...generationOutputsSelected]
      requestedGenerationOutputsRef.current = outputsForSpreadsheet
      setRequestedGenerationOutputs(outputsForSpreadsheet)
      setStepState({
        analysisComplete: false,
        anomaliesConfirmed: false,
        tasksCreated: false,
        decisionsCreated: false,
      })
      setGeneratedTaskIndexes([])
      setSkippedTaskIndexes([])
      setCreatedTaskIdByIndex({})
      autoExternalActionsTriggeredRef.current = false
      autoActionQueueRef.current = []
    }

    setHasStarted(true)
    // Use provided context or fall back to the session-level calendar context
    const effectiveCalendarContext = calendarContext ?? sessionCalendarContext ?? undefined
    if (calendarContext && !sessionCalendarContext) {
      setSessionCalendarContext(calendarContext)
    }

    const isResumeOnly = action === "resume_workflow"
    if (!isResumeOnly && text.trim()) {
      const userMsgId = `user-${Date.now()}`
      addMessage({ id: userMsgId, role: "user", content: text, type: "text" })
    }

    // Create session if needed
    let sid = sessionId
    if (!sid) {
      try {
        const session = await AgentAPI.createSession({ approval_required: getApprovalPref() })
        sid = String(session.id)
        setSessionId(sid)
        setSessionTitle("New Chat")
        setApprovalRequired(Boolean(session.approval_required))
        window.dispatchEvent(new CustomEvent("agent:sessions-changed"))
      } catch {
        addMessage({
          id: `err-${Date.now()}`,
          role: "assistant",
          content: AGENT_MESSAGES.SESSION_CREATE_FAILED,
          type: "error",
        })
        return
      }
    }

    const aiMsgId = reuseAiMsgId ?? `ai-${Date.now()}`
    if (reuseAiMsgId) {
      updateMessage(reuseAiMsgId, {
        content: "Continuing workflow…",
        type: "text",
      })
    } else {
      addMessage({ id: aiMsgId, role: "assistant", content: AGENT_MESSAGES.CHAT_THINKING, type: "text" })
    }

    setIsStreaming(true)
    setStepProgress([])
    let contentParts: string[] = []
    const streamToken = activeStreamTokenRef.current
    const requestSessionId = String(sid)

    abortRef.current = AgentAPI.sendMessage(
      sid!,
      {
        message: isResumeOnly ? "Continue" : text,
        ...(workflowId ? { workflow_id: workflowId } : {}),
        ...(action ? { action } : {}),
        ...(effectiveCalendarContext ? { calendar_context: effectiveCalendarContext as any } : {}),
        ...(sessionDraftContext ? { draft_context: sessionDraftContext as any } : {}),
        ...(spreadsheetPreload
          ? {
              action: "analyze_spreadsheet_insights" as const,
              spreadsheet_id: spreadsheetPreload.spreadsheetId,
              sheet_id: spreadsheetPreload.sheetId,
            }
          : {}),
        user_context: userContext || undefined,
      },
      (event: SSEEvent) => {
        if (activeStreamTokenRef.current !== streamToken) return
        if (String(sessionIdRef.current) !== requestSessionId) return
        if (event.type === "done") return

        // Notify the calendar page to refresh when events are created.
        // Dispatch a custom event for same-window (floating chat) communication,
        // and also write to localStorage for cross-tab communication.
        if (event.type === "calendar_updated") {
          window.dispatchEvent(new CustomEvent("agent:calendar-updated"))
          localStorage.setItem("calendar-events-updated", String(Date.now()))
          return
        }

        // Add a separate invite message so the calendar answer is preserved.
        // Switch to calendar mode so the user's reply goes through the calendar workflow.
        if (event.type === "calendar_invite") {
          addMessage({
            id: `ai-invite-${Date.now()}`,
            role: "assistant",
            content: event.content || "",
            type: "calendar_invite",
          })
          setSessionCalendarContext({
            type: "calendar",
            userTimezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
            currentView: "week",
            currentDate: new Date().toISOString().split("T")[0],
          })
          return
        }

        if (event.type === "spreadsheet_summary") {
          updateMessage(aiMsgId, { content: "", type: "text" })
          addMessage({
            id: `ai-sheet-summary-${Date.now()}`,
            role: "assistant",
            content: event.content || "",
            type: "spreadsheet_summary",
          })
          return
        }

        if (event.type === "spreadsheet_anomalies" && event.data) {
          const data = event.data as AnalysisResult & {
            spreadsheet_id?: number
            sheet_id?: number
          }
          const wantsTasks = requestedGenerationOutputsRef.current.includes("recommended_tasks")
          const tasks = wantsTasks ? data.recommended_tasks : undefined
          latestRecommendedTasksRef.current = tasks || null
          setFollowUpAvailable(true)
          setFollowUpStarted(false)
          setStepState((prev) => ({
            ...prev,
            analysisComplete: true,
            anomaliesConfirmed: true,
          }))
          setGeneratedTaskIndexes([])
          setSkippedTaskIndexes([])
          setCreatedTaskIdByIndex({})
          addMessage({
            id: `ai-sheet-anomalies-${Date.now()}`,
            role: "assistant",
            content: event.content || "",
            type: "spreadsheet_anomalies",
            anomalies: data.anomalies,
            anomaliesConfirmed: true,
            recommendedTasks: tasks,
            spreadsheetId: data.spreadsheet_id,
            sheetId: data.sheet_id,
          })
          if (wantsTasks) {
            selectAllRecommendedTasks(tasks?.length ?? 0)
          }
          queueAutoExternalActionsAfterAnalysis({
            analysis: data,
            generationOutputs: requestedGenerationOutputsRef.current,
          })
          return
        }

        if (event.type === "approval_request" && event.data) {
          const d = event.data as Record<string, unknown>
          const approvalId = d.approval_id
          const kind = String(d.kind ?? "")
          if (typeof approvalId !== "string") return

          const pending: PendingExternalApproval = {
            id: approvalId,
            kind,
            draft: (d.draft as Record<string, unknown>) ?? {},
          }

          if (kind === "task") {
            applyPendingTaskApproval(pending)
            addMessage({
              id: `approval-${approvalId}`,
              role: "assistant",
              content: event.content || "Approval required.",
              type: "approval_request",
              approval: pending,
            })
          }
          return
        }

        if (event.type === "confirmation_request") {
          updateMessage(aiMsgId, {
            content: event.content || "Please confirm to continue.",
            type: "confirmation_request",
          })
          return
        }

        if (event.type === "step_progress" && event.data) {
          const { step_order, step_name, total_steps, status } = event.data
          if (step_order != null && step_name && total_steps) {
            setStepProgress((prev) => {
              const updated = [...prev]
              // Mark previous steps as completed
              for (const s of updated) {
                if (s.order < step_order && s.status === "running") {
                  s.status = "completed"
                }
              }
              // Add or update current step
              const existing = updated.find((s) => s.order === step_order)
              if (existing) {
                existing.status = (status as StepExecutionStatus) || "running"
                existing.name = step_name
              } else {
                // Fill pending steps up to total_steps
                while (updated.length < total_steps) {
                  const order = updated.length + 1
                  updated.push({
                    order,
                    name: order === step_order ? step_name : `Step ${order}`,
                    status: order < step_order ? "completed" : order === step_order ? ((status as StepExecutionStatus) || "running") : "pending",
                  })
                }
              }
              return updated
            })
            updateMessage(aiMsgId, {
              content: event.content || contentParts.join("\n") || `Running: ${step_name}...`,
              stepProgress: undefined, // will be set on done
            })
          }
          return
        }

        if (event.content && event.type !== "miro_status" && event.type !== "follow_up_prompt") {
          contentParts.push(event.content)
          updateMessage(aiMsgId, { content: contentParts.join("\n") })
        }

        if (event.type === "analysis" && event.data) {
          const data = event.data as unknown as AnalysisResult
          const wantsTasks = requestedGenerationOutputsRef.current.includes("recommended_tasks")
          const wantsDecisions =
            requestedGenerationOutputsRef.current.includes("recommended_decision_tree")
          const tasks = wantsTasks ? data.recommended_tasks : undefined
          latestRecommendedTasksRef.current = tasks || null
          syncDecisionTreeNodeCount(data)
          setFollowUpAvailable(true)
          setFollowUpStarted(false)
          const anomConfirmed =
            Boolean(data.anomalies_confirmed) || (data.anomalies?.length ?? 0) === 0
          setStepState((prev) => ({
            ...prev,
            analysisComplete: true,
            anomaliesConfirmed: anomConfirmed,
          }))
          setGeneratedTaskIndexes([])
          setSkippedTaskIndexes([])
          setCreatedTaskIdByIndex({})
          setCreatedDecisionByRef({})
          setDecisionGenerationStatus("idle")
          updateMessage(aiMsgId, {
            type: "analysis",
            anomalies: wantsTasks ? data.anomalies : undefined,
            anomaliesConfirmed: anomConfirmed,
            recommendedTasks: tasks,
            recommendedDecisionTree: pickRecommendedDecisionTree(data, wantsDecisions),
          })
          if (wantsTasks) {
            selectAllRecommendedTasks(tasks?.length ?? 0)
          }
          if (wantsDecisions) {
            const refs = data.recommended_decision_tree?.nodes?.map((node) => node.ref) ?? []
            selectAllRecommendedDecisionRefs(refs)
          }
          queueAutoExternalActionsAfterAnalysis({
            analysis: data,
            generationOutputs: requestedGenerationOutputsRef.current,
          })
        }
        if (event.type === "task_created" && event.data) {
          setStepState((prev) => ({ ...prev, tasksCreated: true }))
          setTaskGenerationStatus("completed")
          const created = (event.data as any)?.created_tasks
          if (Array.isArray(created)) {
            const idxs = created
              .map((c: any) => Number(c?.index))
              .filter((n: unknown) => typeof n === "number" && Number.isFinite(n))
            setGeneratedTaskIndexes(Array.from(new Set(idxs)))
            const pairs = created
              .map((c: any) => [Number(c?.index), c?.task_slug ?? c?.task_id] as const)
              .filter(([idx, tid]: readonly [number, any]) => Number.isFinite(idx) && tid != null)
            setCreatedTaskIdByIndex(Object.fromEntries(pairs))
          } else {
            const tasksLen = latestRecommendedTasksRef.current?.length ?? 0
            if (tasksLen > 0) setGeneratedTaskIndexes(Array.from({ length: tasksLen }, (_, i) => i))
          }
          updateMessage(aiMsgId, {
            content: contentParts.join("\n"),
            type: "tasks_created",
            navigateTo: "tasks",
            navigateLabel: "Go to Tasks",
          })
        }
        if (event.type === "decision_draft" && event.data) {
          applyDecisionDraftResult(event.data as Record<string, unknown>)
          updateMessage(aiMsgId, {
            content: contentParts.join("\n"),
            type: "decisions_created",
            navigateTo: "decisions",
            navigateLabel: "Go to Decisions",
          })
        }
        if (event.type === "miro_status") {
          setMiroGenerateInFlight(false)
          const rawWr = event.data?.workflow_run_id
          const workflowRunId =
            typeof rawWr === "string" ? rawWr : rawWr != null ? String(rawWr) : undefined
          const startedEventType =
            typeof event.data?.event_type === "string"
              ? event.data.event_type
              : "miro_generation_started"

          setMessages((prev) => {
            const next = mergeMiroGenerationStartedIntoMessages(prev, aiMsgId, {
              content: event.content || LEGACY_MIRO_QUEUED_FALLBACK,
              type: "miro_status",
              navigateTo: "miro",
              navigateLabel: "Generating Miro...",
              navigateDisabled: true,
              navigateHref: undefined,
              eventType: startedEventType,
              workflowRunId,
            })
            return appendMiroResultMessage(next, event)
          })
        }
        if (event.type === "follow_up_prompt") {
          contentParts.push(event.content || "")
          setFollowUpAvailable(false)
          setFollowUpStarted(true)
          updateMessage(aiMsgId, {
            content: contentParts.join("\n"),
            isFollowUpPrompt: true,
          })
        }
      },
      (error) => {
        if (activeStreamTokenRef.current !== streamToken) return
        if (String(sessionIdRef.current) !== requestSessionId) return
        if (error.message === "quota_error") {
          // The global UpgradeModal already explains the block; drop the optimistic
          // thinking placeholder instead of leaving an "Error: quota_error" bubble.
          setMessages((prev) => prev.filter((m) => m.id !== aiMsgId))
        } else {
          updateMessage(aiMsgId, { content: `Error: ${error.message}`, type: "error" })
        }
        setIsStreaming(false)
      },
      () => {
        if (activeStreamTokenRef.current !== streamToken) return
        if (String(sessionIdRef.current) !== requestSessionId) return
        // Attach final step progress before refresh (refresh reloads messages from DB)
        setStepProgress((prev) => {
          if (prev.length > 0) {
            const final = prev.map((s) => ({
              ...s,
              status: s.status === "running" ? "completed" as const : s.status,
            }))
            updateMessage(aiMsgId, { stepProgress: final })
            return final
          }
          return prev
        })
        setIsStreaming(false)
        void refreshSession(requestSessionId)
        void refreshFollowUpState(requestSessionId)
        if (embeddedInFloating) {
          window.dispatchEvent(new CustomEvent("agent:sessions-changed"))
          void AgentAPI.updateSession(requestSessionId, { last_read_at: new Date().toISOString() }).catch(() => {
            /* ignore */
          })
        }
      }
    )
  }, [patternGenerationSheetId, patternGenerationSheetName, patternGenerationSpreadsheetName, pivotGenerationSpreadsheetId, pivotGenerationSheetId, pivotGenerationSheetName, pivotGenerationSpreadsheetName, sessionId, sessionCalendarContext, sessionDraftContext, generationOutputsSelected, addMessage, updateMessage, setMessages, setSessionId, refreshFollowUpState, refreshSession, getApprovalPref, embeddedInFloating, queueAutoExternalActionsAfterAnalysis])

  // Keep ref always pointing to the latest handleSendMessage
  handleSendMessageRef.current = handleSendMessage

  /** Handle pipeline actions (Create Tasks, Miro, follow-up, etc.) */
  const handleAction = useCallback(async (action: string) => {
    const sid = sessionIdRef.current
    if (!sid) return

    const actionMap: Record<string, AgentAction> = {
      create_decisions: "create_decisions",
      create_tasks: "create_tasks",
      generate_miro: "generate_miro",
      start_follow_up: "start_follow_up",
      cancel_follow_up: "cancel_follow_up",
    }
    const agentAction = actionMap[action]
    if (!agentAction) return

    if (action === "create_tasks") {
      setTaskGenerationStatus("generating")
    }
    if (action === "create_decisions") {
      setDecisionGenerationStatus("generating")
    }
    if (action === "generate_miro") {
      setMiroGenerateInFlight(true)
    }

    const aiMsgId = `ai-${Date.now()}`
    addMessage({ id: aiMsgId, role: "assistant", content: AGENT_MESSAGES.CHAT_THINKING, type: "text" })

    setIsStreaming(true)
    if (action === "create_tasks") setGeneratedTaskIndexes([])
    let contentParts: string[] = []
    const streamToken = activeStreamTokenRef.current
    const requestSessionId = String(sid)
    let createTasksHadError = false
    let createTasksSucceeded = false

    abortRef.current = AgentAPI.sendMessage(
      sid,
      { message: action, action: agentAction },
      (event: SSEEvent) => {
        if (activeStreamTokenRef.current !== streamToken) return
        if (String(sessionIdRef.current) !== requestSessionId) return
        if (event.type === "done") return

        if (event.type === "step_progress" && event.data) {
          const { step_order, step_name, total_steps, status } = event.data
          if (step_order != null && step_name && total_steps) {
            setStepProgress((prev) => {
              const updated = [...prev]
              for (const s of updated) {
                if (s.order < step_order && s.status === "running") s.status = "completed"
              }
              const existing = updated.find((s) => s.order === step_order)
              if (existing) {
                existing.status =  (status as StepExecutionStatus)|| "running"
                existing.name = step_name
              } else {
                while (updated.length < total_steps) {
                  const order = updated.length + 1
                  updated.push({
                    order,
                    name: order === step_order ? step_name : `Step ${order}`,
                    status: order < step_order ? "completed" : order === step_order ? ((status as StepExecutionStatus) || "running") : "pending",
                  })
                }
              }
              // Update the existing step progress message live
              const spMsgId = stepProgressMsgIdRef.current
              if (spMsgId) updateMessage(spMsgId, { stepProgress: [...updated] })
              return updated
            })
          }
          return
        }

        if (event.content && event.type !== "miro_status" && event.type !== "follow_up_prompt") {
          contentParts.push(event.content)
          updateMessage(aiMsgId, { content: contentParts.join("\n") })
        }

        if (event.type === "task_created") {
          createTasksSucceeded = true
          setStepState((prev) => ({ ...prev, tasksCreated: true }))
          setPendingTaskApproval(null)
          setTasksApprovalGenerating(false)
          setTaskGenerationStatus("completed")
          const created = (event.data as any)?.created_tasks
          if (Array.isArray(created)) {
            const idxs = created
              .map((c: any) => Number(c?.index))
              .filter((n: unknown) => typeof n === "number" && Number.isFinite(n))
            setGeneratedTaskIndexes(Array.from(new Set(idxs)))
            setSkippedTaskIndexes((prev) => prev.filter((i) => !idxs.includes(i)))
            const pairs = created
              .map((c: any) => [Number(c?.index), c?.task_slug ?? c?.task_id] as const)
              .filter(([idx, tid]: readonly [number, any]) => Number.isFinite(idx) && tid != null)
            setCreatedTaskIdByIndex(Object.fromEntries(pairs))
          } else {
            const tasksLen = latestRecommendedTasksRef.current?.length ?? 0
            if (tasksLen > 0) setGeneratedTaskIndexes(Array.from({ length: tasksLen }, (_, i) => i))
          }
          updateMessage(aiMsgId, {
            content: contentParts.join("\n"),
            type: "tasks_created",
            navigateTo: "tasks",
            navigateLabel: "Go to Tasks",
          })
        }
        if (event.type === "decision_draft" && event.data) {
          applyDecisionDraftResult(event.data as Record<string, unknown>)
          updateMessage(aiMsgId, {
            content: contentParts.join("\n"),
            type: "decisions_created",
            navigateTo: "decisions",
            navigateLabel: "Go to Decisions",
          })
        }
        if (event.type === "miro_status") {
          setMiroGenerateInFlight(false)
          const rawWr = event.data?.workflow_run_id
          const workflowRunId =
            typeof rawWr === "string" ? rawWr : rawWr != null ? String(rawWr) : undefined

          const startedEventType =
            typeof event.data?.event_type === "string"
              ? event.data.event_type
              : "miro_generation_started"

          setMessages((prev) => {
            const next = mergeMiroGenerationStartedIntoMessages(prev, aiMsgId, {
              content: event.content || LEGACY_MIRO_QUEUED_FALLBACK,
              type: "miro_status",
              navigateTo: "miro",
              navigateLabel: "Generating Miro...",
              navigateDisabled: true,
              navigateHref: undefined,
              eventType: startedEventType,
              workflowRunId,
            })
            return appendMiroResultMessage(next, event)
          })
        }
        if (event.type === "follow_up_prompt") {
          contentParts.push(event.content || "")
          setFollowUpAvailable(false)
          setFollowUpStarted(true)
          updateMessage(aiMsgId, {
            content: contentParts.join("\n"),
            isFollowUpPrompt: true,
          })
        }
        if (event.type === "approval_request" && event.data) {
          const d = event.data as Record<string, unknown>
          const approvalId = d.approval_id
          const kind = String(d.kind ?? "")
          if (typeof approvalId !== "string") return

          const pending: PendingExternalApproval = {
            id: approvalId,
            kind,
            draft: (d.draft as Record<string, unknown>) ?? {},
          }

          if (kind === "task") {
            applyPendingTaskApproval(pending)
            const draftTasks = (pending.draft as { recommended_tasks?: RecommendedTask[] })
              ?.recommended_tasks
            if (Array.isArray(draftTasks) && draftTasks.length > 0) {
              updateMessage(aiMsgId, { recommendedTasks: draftTasks })
            }
          } else if (kind === "decision_tree") {
            applyPendingDecisionApproval(pending)
          }
        }
        if (event.type === "error" && action === "create_tasks") {
          createTasksHadError = true
          setTaskGenerationStatus("error")
        }
        if (event.type === "error" && action === "create_decisions") {
          setDecisionGenerationStatus("error")
        }
      },
      (error) => {
        if (activeStreamTokenRef.current !== streamToken) return
        if (String(sessionIdRef.current) !== requestSessionId) return
        if (action === "create_tasks") {
          createTasksHadError = true
          setTaskGenerationStatus("error")
        }
        if (action === "create_decisions") {
          setDecisionGenerationStatus("error")
        }
        if (action === "generate_miro") {
          setMiroGenerateInFlight(false)
        }
        updateMessage(aiMsgId, { content: `Error: ${error.message}`, type: "error" })
        setIsStreaming(false)
      },
      () => {
        if (activeStreamTokenRef.current !== streamToken) return
        if (String(sessionIdRef.current) !== requestSessionId) return
        if (action === "create_tasks") {
          setTaskGenerationStatus((status) => {
            if (status !== "generating") return status
            if (createTasksHadError) return "error"
            return "completed"
          })
        }
        if (action === "create_decisions") {
          setDecisionGenerationStatus((status) => (status === "generating" ? "error" : status))
        }
        void refreshSession(requestSessionId)
        void refreshFollowUpState(requestSessionId)
        setStepProgress((prev) => {
          if (prev.length > 0) {
            const final = prev.map((s) => ({
              ...s,
              status: s.status === "running" ? "completed" as const : s.status,
            }))
            const spMsgId = stepProgressMsgIdRef.current
            if (spMsgId) updateMessage(spMsgId, { stepProgress: final })
            return final
          }
          return prev
        })
        setIsStreaming(false)
        if (embeddedInFloating) {
          window.dispatchEvent(new CustomEvent("agent:sessions-changed"))
          void AgentAPI.updateSession(requestSessionId, { last_read_at: new Date().toISOString() }).catch(() => {
            /* ignore */
          })
        }
      }
    )
  }, [
    addMessage,
    updateMessage,
    setMessages,
    refreshFollowUpState,
    refreshSession,
    embeddedInFloating,
    applyPendingTaskApproval,
    applyDecisionDraftResult,
  ])

  useEffect(() => {
    handleActionRef.current = handleAction
  }, [handleAction])

  useEffect(() => {
    if (isStreamingRef.current || !sessionIdRef.current) return
    if (approvalRequiredRef.current) return
    if (!stepState.analysisComplete) return
    // Do not auto-run downstream actions until anomalies are confirmed.
    if (!stepState.anomaliesConfirmed) return
    if (pendingTaskApproval || pendingDecisionApproval) return
    const outputs = requestedGenerationOutputsRef.current
    const wantsTasks = outputs.includes("recommended_tasks")
    const wantsDecisions = outputs.includes("recommended_decision_tree")
    const needsTasks =
      wantsTasks &&
      !stepState.tasksCreated &&
      (latestRecommendedTasksRef.current?.length ?? 0) > 0
    const needsDecisions =
      wantsDecisions &&
      !stepState.decisionsCreated &&
      latestDecisionTreeNodeCountRef.current > 0
    if (!needsTasks && !needsDecisions) return
    if (autoExternalActionsTriggeredRef.current) return
    queueAutoExternalActionsAfterAnalysis()
  }, [
    sessionId,
    isStreaming,
    approvalRequired,
    stepState.analysisComplete,
    stepState.anomaliesConfirmed,
    stepState.tasksCreated,
    stepState.decisionsCreated,
    pendingTaskApproval,
    pendingDecisionApproval,
    queueAutoExternalActionsAfterAnalysis,
  ])

  const resolveExternalApproval = useCallback(
    (
      pending: PendingExternalApproval,
      decision: "approve" | "reject",
      draft?: Record<string, unknown>
    ) => {
      if (!sessionId) return
      const streamToken = activeStreamTokenRef.current
      const requestSessionId = String(sessionId)
      AgentAPI.sendMessage(
        sessionId,
        {
          message: ".",
          action: "resolve_external_approval",
          approval_id: pending.id,
          approval_decision: decision,
          approval_draft: decision === "approve" ? draft : undefined,
        },
        (_ev: SSEEvent) => {
          if (activeStreamTokenRef.current !== streamToken) return
          if (String(sessionIdRef.current) !== requestSessionId) return
          /* streamed chunks ignored; subsequent events update UI */
        },
        () => {
          if (activeStreamTokenRef.current !== streamToken) return
          if (String(sessionIdRef.current) !== requestSessionId) return
          if (pending.kind === "task") setTasksApprovalGenerating(false)
          if (pending.kind === "decision_tree") setDecisionsApprovalGenerating(false)
        },
        () => {
          if (activeStreamTokenRef.current !== streamToken) return
          if (String(sessionIdRef.current) !== requestSessionId) return
          void refreshSession(requestSessionId)
          if (embeddedInFloating) {
            window.dispatchEvent(new CustomEvent("agent:sessions-changed"))
            void AgentAPI.updateSession(requestSessionId, { last_read_at: new Date().toISOString() }).catch(() => {
              /* ignore */
            })
          }
        }
      )
    },
    [sessionId, refreshSession, embeddedInFloating]
  )

  const handleApproveSelectedTasks = useCallback(
    (selected: number[]) => {
      if (!pendingTaskApproval) return
      const tasks =
        (pendingTaskApproval.draft as any)?.recommended_tasks ??
        (latestRecommendedTasksRef.current ?? [])
      if (!Array.isArray(tasks) || tasks.length === 0) return

      // Mark unselected recommendations as explicitly skipped so the UI shows a red X
      // instead of falling back to "awaiting approval".
      const selectedSet = new Set(selected)
      const skipped = Array.from({ length: tasks.length }, (_, i) => i).filter((i) => !selectedSet.has(i))
      setSkippedTaskIndexes(skipped)

      const filtered = tasks
        .map((t: any, idx: number) => ({ ...t, index: idx }))
        .filter((_t: any, idx: number) => selectedSet.has(idx))

      setTasksApprovalGenerating(true)
      // Optimistic UI: immediately exit checkbox/approval mode so the right panel
      // doesn't appear "stuck" after clicking Create Tasks.
      setPendingTaskApproval(null)
      setSelectedTaskIndexes([])
      setTaskGenerationStatus("generating")
      resolveExternalApproval(pendingTaskApproval, "approve", { recommended_tasks: filtered })
    },
    [pendingTaskApproval, resolveExternalApproval]
  )

  const handleRejectTasksApproval = useCallback(() => {
    if (!pendingTaskApproval) return
    setTasksApprovalGenerating(false)
    resolveExternalApproval(pendingTaskApproval, "reject")
    setPendingTaskApproval(null)
  }, [pendingTaskApproval, resolveExternalApproval])

  const handleApproveSelectedDecisions = useCallback(
    (selectedRefs: string[]) => {
      if (!pendingDecisionApproval) return
      const nodes =
        (pendingDecisionApproval.draft as { recommended_decision_tree?: { nodes?: RecommendedDecisionTreeNode[] } })
          ?.recommended_decision_tree?.nodes ?? []
      if (!Array.isArray(nodes) || nodes.length === 0) return

      const selectedSet = new Set(selectedRefs)
      const skipped = nodes.map((node) => node.ref).filter((ref) => !selectedSet.has(ref))
      setSkippedDecisionRefs(skipped)

      const filteredNodes = nodes
        .filter((node) => selectedSet.has(node.ref))
        .map((node) => ({
          ...node,
          parent_refs: (node.parent_refs || []).filter((parentRef) => selectedSet.has(parentRef)),
        }))

      setDecisionsApprovalGenerating(true)
      setPendingDecisionApproval(null)
      setSelectedDecisionRefs([])
      setDecisionGenerationStatus("generating")
      resolveExternalApproval(pendingDecisionApproval, "approve", {
        recommended_decision_tree: { nodes: filteredNodes },
      })
    },
    [pendingDecisionApproval, resolveExternalApproval]
  )

  // Auto-run queued external actions when streaming is idle.
  useEffect(() => {
    if (isStreaming) return
    tryRunNextAutoAction()
  }, [isStreaming, tryRunNextAutoAction])

  // Auto-send calendar context message when arriving from calendar/event page (once only).
  // Uses a module-level flag + ref to handleSendMessage so this effect only fires on mount
  // and when hasStarted changes — NOT when sessionId changes and recreates handleSendMessage.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!pendingCalendarPreload || hasStarted) return
    if (_calendarAutoSendFired) return
    _calendarAutoSendFired = true
    handleSendMessageRef.current?.(pendingCalendarPreload.message, pendingCalendarPreload.context)
  }, [pendingCalendarPreload, hasStarted])

  // Auto-send the initial draft question once — ONLY for "Ask Agent"
  // (autoSend). "Open in Agent" (autoSend=false) just attaches the draft as
  // context and waits for the user to type; sessionDraftContext is already set,
  // so whichever message the user sends first still carries draft_context.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!pendingDraftPreload || hasStarted) return
    if (_draftAutoSendFired) return
    if (!shouldAutoSendDraftPreload(pendingDraftPreload)) return
    _draftAutoSendFired = true
    handleSendMessageRef.current?.(pendingDraftPreload.message)
  }, [pendingDraftPreload, hasStarted])

  // Auto-send spreadsheet insights when arriving from spreadsheet Analyze with AI.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!pendingSpreadsheetPreload || hasStarted) return
    if (_spreadsheetAutoSendFired) return
    _spreadsheetAutoSendFired = true
    lastSpreadsheetPreloadRef.current = pendingSpreadsheetPreload
    handleSendMessageRef.current?.(
      pendingSpreadsheetPreload.message,
      undefined,
      undefined,
      undefined,
      undefined,
      undefined,
      pendingSpreadsheetPreload,
    )
  }, [pendingSpreadsheetPreload, hasStarted])

  // Which spreadsheet workflow the chat board is currently working on (if any),
  // used to render the sheet banner above the message list.
  const workflowBanner = useMemo<
    | {
        workflow: "spreadsheet_analysis" | "pattern_generation" | "pivot_table_generation"
        spreadsheetName?: string | null
        sheetName?: string | null
        busy: boolean
      }
    | null
  >(() => {
    if (patternGenerationSheetId != null) {
      return {
        workflow: "pattern_generation",
        spreadsheetName: patternGenerationSpreadsheetName,
        sheetName: patternGenerationSheetName,
        busy: isWorkflowGenerating,
      }
    }
    if (pivotGenerationSheetId != null) {
      return {
        workflow: "pivot_table_generation",
        spreadsheetName: pivotGenerationSpreadsheetName,
        sheetName: pivotGenerationSheetName,
        busy: isWorkflowGenerating,
      }
    }
    if (sessionSpreadsheetContext) {
      return {
        workflow: "spreadsheet_analysis",
        spreadsheetName: sessionSpreadsheetContext.spreadsheetName,
        sheetName: sessionSpreadsheetContext.sheetName,
        busy: isStreaming,
      }
    }
    // Pre-marker pattern/pivot session reopened from history: label-only banner
    // (no recoverable sheet target).
    if (historicalGenerationMode) {
      return {
        workflow: historicalGenerationMode,
        spreadsheetName: null,
        sheetName: null,
        busy: false,
      }
    }
    return null
  }, [
    patternGenerationSheetId,
    patternGenerationSpreadsheetName,
    patternGenerationSheetName,
    pivotGenerationSheetId,
    pivotGenerationSpreadsheetName,
    pivotGenerationSheetName,
    sessionSpreadsheetContext,
    historicalGenerationMode,
    isStreaming,
    isWorkflowGenerating,
  ])

  return (
    <div className="flex h-full flex-col">
      <OnboardingTokenIntro />
      {!embeddedInFloating && (
        <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2 shrink-0 bg-background">
          <div className="flex items-center gap-2 min-w-0">
            <h2 className="text-sm font-semibold truncate text-foreground">{sessionTitle}</h2>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <GenerationOutputsSettings
              disabled={isStreaming}
              sessionId={sessionId}
              approvalRequired={approvalRequired}
              onApprovalChange={(next) => {
                setApprovalRequired(next)
                if (typeof window !== "undefined") {
                  window.dispatchEvent(
                    new CustomEvent("agent:approval-changed", {
                      detail: { sessionId, value: next },
                    })
                  )
                  if (sessionId) {
                    window.dispatchEvent(
                      new CustomEvent("agent:session-state", {
                        detail: { sessionId, title: sessionTitle, approvalRequired: next },
                      })
                    )
                  }
                }
              }}
            />
          </div>
        </div>
      )}
      {workflowBanner && (
        <AgentWorkflowBanner
          workflow={workflowBanner.workflow}
          spreadsheetName={workflowBanner.spreadsheetName}
          sheetName={workflowBanner.sheetName}
          busy={workflowBanner.busy}
        />
      )}
      {!hasStarted ? (
        <WelcomeScreen
          onSend={handleSendMessage}
          onFileUpload={handleFileUpload}
          disabled={isStreaming}
          showComposer={false}
        />
      ) : (
      <MessageList
        {...({
          messages,
          sessionId,
          projectId,
          requestedGenerationOutputs,
          isStreaming,
          showRevisitThinkingBubble,
          onRenderFinishChange: () => setRenderFinishSignal((prev) => prev + 1),
          approvalDisabled: isStreaming,
          approvalRequired,
          generatedTaskIndexes,
          skippedTaskIndexes,
          createdTaskIdByIndex,
          generatingTasks: !approvalRequired && stepState.analysisComplete && !stepState.tasksCreated,
          generatingDecisions:
            !approvalRequired && stepState.analysisComplete && !stepState.decisionsCreated,
          taskGenerationStatus,
          decisionGenerationStatus,
          createdDecisionByRef,
          pendingTaskApproval,
          pendingDecisionApproval,
          selectedTaskIndexes,
          onSelectedTaskIndexesChange: setSelectedTaskIndexes,
          tasksApprovalGenerating,
          onApproveSelectedTasks: handleApproveSelectedTasks,
          onRejectTasksApproval: handleRejectTasksApproval,
          selectedDecisionRefs,
          onSelectedDecisionRefsChange: setSelectedDecisionRefs,
          skippedDecisionRefs,
          generatedDecisionRefs,
          decisionsApprovalGenerating,
          onApproveSelectedDecisions: handleApproveSelectedDecisions,
          miroGenerateInFlight,
          onAction: handleAction,
          onConfirmColumns: handleConfirmColumns,
          onReupload: handleReupload,
          onResumeWorkflow: handleResumeWorkflow,
          latestAnalysisMessageId,
          tasksCardMessageId: latestAnalysisMessageId,
          showFollowUpToggle: followUpAvailable || followUpStarted,
          followUpActive: followUpStarted,
          stepState,
          bypassRevealQueue: patternGenerationSheetId != null || pivotGenerationSheetId != null,
          onNavigate: (view: string, msg?: ChatMessage) => {
        if (msg?.navigateHref && typeof window !== "undefined") {
          window.location.href = buildUrl(msg.navigateHref)
          return
        }
        if (view === "tasks") {
          router.push(buildUrl("/tasks"))
          return
        }
        if (view === "decisions") {
          router.push(buildUrl("/decisions"))
          return
        }
        setActiveView(view as AgentView)
        if (floatingChat.mode === "maximized") toggleMaximize()
          },
        } as any)}
      />
      )}
      <ActionBar stepState={stepState} onReupload={handleReupload} disabled={isStreaming} />
      <ChatInput
        onSend={(msg, ctx) => handleSendMessage(msg, undefined, ctx)}
        onFileUpload={handleFileUpload}
        disabled={isStreaming}
        placeholder={inputPlaceholder}
        helperText={inputHelperText}
      />
      <ConfirmModal
        isOpen={aiConsentRetry != null}
        type="info"
        title="Enable AI analysis for this spreadsheet?"
        message="Analyzing with AI sends the contents of this spreadsheet to AI Agent for processing. Your data is not used to train models. This choice is remembered for this spreadsheet."
        confirmText="Enable AI analysis"
        cancelText="Not now"
        loading={aiConsentSubmitting}
        onClose={() => {
          if (!aiConsentSubmitting) {
            setAiConsentRetry(null)
            setConsentTarget(null)
          }
        }}
        onConfirm={async () => {
          if (consentTarget == null) {
            setAiConsentRetry(null)
            return
          }
          setAiConsentSubmitting(true)
          try {
            await AiConsentAPI.grant(consentTarget)
            const retry = aiConsentRetry
            setAiConsentRetry(null)
            setConsentTarget(null)
            retry?.()
          } finally {
            setAiConsentSubmitting(false)
          }
        }}
      />
    </div>
  )
}
