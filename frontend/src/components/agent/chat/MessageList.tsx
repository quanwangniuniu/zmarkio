"use client"

import { Fragment, useEffect, useLayoutEffect, useMemo, useRef } from "react"
import { FileSpreadsheet, ArrowRight, CalendarPlus, UploadCloud } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { AGENT_MESSAGES } from "@/lib/agentMessages"
import { SpreadsheetInsightAnomalyCard } from "./SpreadsheetInsightAnomalyCard"
import { ColumnMappingCard } from "./ColumnMappingCard"
import { FollowUpCard } from "./FollowUpCard"
import { MiroGenerateCard } from "./MiroGenerateCard"
import { TaskListCard } from "./TaskListCard"
import { DecisionTreeListCard } from "./DecisionTreeListCard"
import type {
  AnomalyItem,
  RecommendedTask,
  RecommendedDecisionTreeNode,
  WorkflowStepState,
  ColumnDetectionData,
  GenerationOutputKey,
  SuggestedCalendarEvent,
} from "@/types/agent"
import { CalendarEventsCard } from "./CalendarEventsCard"
import { DEFAULT_GENERATION_OUTPUTS } from "@/lib/generationOutputs"
import { deriveMiroBoardCardState } from "@/lib/agentMiroBoardStatus"
import { StepProgress, type StepProgressItem } from "./StepProgress"
import type { PendingExternalApproval } from "./ExternalApprovalModal"
import type { TaskGenerationStatus } from "./TaskListCard"
import type { DecisionGenerationStatus } from "./DecisionTreeListCard"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { AgentMessageBoardBlock } from "./AgentMessageBoardBlock"
import { AgentMessageBoardMarkdown } from "./AgentMessageBoardMarkdown"
import { AgentMessageBoardText } from "./AgentMessageBoardText"
import { AgentMessageBoardTextProvider } from "./AgentMessageBoardTextContext"
import { AgentMessageBoardAvatar } from "./AgentMessageBoardAvatar"
import {
  getAssistantMessageBlockIds,
  getMessageBoardBlockIds,
  getMiroCardsAnchorMessageId,
  getMiroGenerateBlockId,
} from "./agentMessageBoardBlockIds"
import { WorkflowStepConfirmCard } from "./WorkflowStepConfirmCard"

export type ChatMessageType =
  | "text"
  | "analysis"
  | "spreadsheet_summary"
  | "spreadsheet_anomalies"
  | "file_uploaded"
  | "tasks_created"
  | "decisions_created"
  | "miro_status"
  | "step_progress"
  | "error"
  | "calendar_invite"
  | "column_mapping"
  | "approval_request"
  | "confirmation_request"

export interface ChatMessage {
  id: string
  role: "user" | "assistant"
  content: string
  type?: ChatMessageType
  isFollowUpPrompt?: boolean
  anomalies?: AnomalyItem[]
  anomaliesConfirmed?: boolean
  recommendedTasks?: RecommendedTask[]
  recommendedDecisionTree?: { nodes: RecommendedDecisionTreeNode[] }
  columnMappingData?: ColumnDetectionData
  fileName?: string
  navigateTo?: string
  navigateLabel?: string
  navigateDisabled?: boolean
  navigateHref?: string
  eventType?: string
  workflowRunId?: string
  stepProgress?: StepProgressItem[]
  approval?: PendingExternalApproval
  calendarEvents?: SuggestedCalendarEvent[]
  spreadsheetId?: number
  sheetId?: number
}

export interface MessageListProps {
  messages: ChatMessage[]
  onAction?: (action: string) => void
  onNavigate?: (view: string, message?: ChatMessage) => void
  onConfirmColumns?: (mapping: Record<string, string>) => void
  onReupload?: () => void
  sessionId?: string | null
  projectId?: string
  approvalDisabled?: boolean
  approvalRequired?: boolean
  generatedTaskIndexes?: number[]
  skippedTaskIndexes?: number[]
  createdTaskIdByIndex?: Record<number, number | string>
  generatingTasks?: boolean
  pendingTaskApproval?: PendingExternalApproval | null
  pendingDecisionApproval?: PendingExternalApproval | null
  selectedTaskIndexes?: number[]
  onSelectedTaskIndexesChange?: (next: number[]) => void
  tasksApprovalGenerating?: boolean
  onApproveSelectedTasks?: (selectedIndexes: number[], destination?: Record<string, unknown>) => void
  onRejectTasksApproval?: () => void
  selectedDecisionRefs?: string[]
  onSelectedDecisionRefsChange?: (next: string[]) => void
  skippedDecisionRefs?: string[]
  generatedDecisionRefs?: string[]
  decisionsApprovalGenerating?: boolean
  onApproveSelectedDecisions?: (selectedRefs: string[]) => void
  miroGenerateInFlight?: boolean
  latestAnalysisMessageId?: string | null
  showFollowUpToggle?: boolean
  followUpActive?: boolean
  stepState?: WorkflowStepState
  taskGenerationStatus?: TaskGenerationStatus
  /** When set, only this message renders the recommended-tasks card (approval flow). */
  tasksCardMessageId?: string | null
  decisionGenerationStatus?: DecisionGenerationStatus
  generatingDecisions?: boolean
  createdDecisionByRef?: Record<string, number>
  isStreaming?: boolean
  onResumeWorkflow?: (confirmMessageId: string) => void
  showRevisitThinkingBubble?: boolean
  onRenderFinishChange?: (finished: boolean) => void
  requestedGenerationOutputs?: GenerationOutputKey[]
  /**
   * Skip the sequential reveal queue for assistant bubbles / nav buttons.
   * Used by pattern- and pivot-generation modes, which run as plain request/
   * response calls with no SSE stream to drive the queue — routing their
   * bubbles through the queue leaves them stuck unrevealed.
   */
  bypassRevealQueue?: boolean
}

export function MessageList({
  messages,
  onAction,
  onNavigate,
  onConfirmColumns,
  onReupload,
  sessionId,
  projectId,
  approvalDisabled,
  approvalRequired,
  generatedTaskIndexes,
  skippedTaskIndexes,
  createdTaskIdByIndex,
  generatingTasks,
  pendingTaskApproval,
  pendingDecisionApproval,
  selectedTaskIndexes,
  onSelectedTaskIndexesChange,
  tasksApprovalGenerating,
  onApproveSelectedTasks,
  onRejectTasksApproval,
  selectedDecisionRefs,
  onSelectedDecisionRefsChange,
  skippedDecisionRefs,
  generatedDecisionRefs,
  decisionsApprovalGenerating,
  onApproveSelectedDecisions,
  miroGenerateInFlight = false,
  latestAnalysisMessageId,
  showFollowUpToggle,
  followUpActive,
  stepState,
  taskGenerationStatus,
  tasksCardMessageId: tasksCardMessageIdOverride,
  decisionGenerationStatus,
  generatingDecisions,
  createdDecisionByRef,
  isStreaming = false,
  onResumeWorkflow,
  showRevisitThinkingBubble = false,
  onRenderFinishChange,
  requestedGenerationOutputs = DEFAULT_GENERATION_OUTPUTS,
  bypassRevealQueue = false,
}: MessageListProps) {
  const effectiveGenerationOutputs =
    requestedGenerationOutputs.length > 0
      ? requestedGenerationOutputs
      : DEFAULT_GENERATION_OUTPUTS
  const wantsTasks = effectiveGenerationOutputs.includes("recommended_tasks")
  const wantsDecisions = effectiveGenerationOutputs.includes("recommended_decision_tree")
  const wantsMiro = effectiveGenerationOutputs.includes("miro_board")
  const downstreamOutputsComplete = Boolean(
    stepState &&
      (!wantsTasks || stepState.tasksCreated) &&
      (!wantsDecisions || stepState.decisionsCreated)
  )
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const prevScrollTopRef = useRef(0)
  const prevScrollHeightRef = useRef(0)
  const wasAtBottomRef = useRef(true)
  const isAnalysisLikeMessage = (m: ChatMessage) =>
    m.type === "analysis" ||
    m.type === "spreadsheet_anomalies" ||
    m.type === "tasks_created" ||
    m.type === "decisions_created" ||
    (Array.isArray(m.recommendedTasks) && m.recommendedTasks.length > 0) ||
    (Array.isArray(m.recommendedDecisionTree?.nodes) && m.recommendedDecisionTree.nodes.length > 0)

  const latestAnalysisWithTasks = [...messages]
    .reverse()
    .find(
      (m) =>
        isAnalysisLikeMessage(m) &&
        Array.isArray(m.recommendedTasks) &&
        m.recommendedTasks.length > 0
    )
  const latestMessageWithTasks = [...messages]
    .reverse()
    .find(
      (m) =>
        m.role === "assistant" &&
        Array.isArray(m.recommendedTasks) &&
        m.recommendedTasks.length > 0
    )
  const latestAnalysisLikeMessage = [...messages].reverse().find(isAnalysisLikeMessage)
  const useSingleTasksCard =
    Boolean(approvalRequired) ||
    Boolean(pendingTaskApproval) ||
    taskGenerationStatus === "awaiting_approval"
  const tasksCardMessageId =
    tasksCardMessageIdOverride ??
    (useSingleTasksCard
      ? (latestMessageWithTasks?.id ?? latestAnalysisWithTasks?.id ?? null)
      : (latestAnalysisWithTasks?.id ?? null))
  const bottomCardsMessageId =
    tasksCardMessageId ??
    latestAnalysisMessageId ??
    latestAnalysisLikeMessage?.id ??
    "board-bottom"
  const canSelectRecommendedTasks =
    (taskGenerationStatus === "idle" || taskGenerationStatus === "awaiting_approval") &&
    !tasksApprovalGenerating &&
    !generatingTasks &&
    !stepState?.tasksCreated
  const taskSelectionMode =
    canSelectRecommendedTasks && Boolean(pendingTaskApproval)
  const canSelectRecommendedDecisions =
    (decisionGenerationStatus === "idle" || decisionGenerationStatus === "awaiting_approval") &&
    !decisionsApprovalGenerating &&
    !generatingDecisions &&
    !stepState?.decisionsCreated
  const decisionSelectionMode =
    canSelectRecommendedDecisions &&
    (Boolean(pendingDecisionApproval) ||
      (Boolean(approvalRequired) &&
        Boolean(stepState?.analysisComplete) &&
        !stepState?.decisionsCreated))

  const showBottomActionCards = Boolean(
    wantsMiro &&
      stepState?.analysisComplete &&
      (!wantsTasks ||
        Boolean(stepState?.tasksCreated) ||
        Boolean(pendingTaskApproval) ||
        taskGenerationStatus === "awaiting_approval")
  )
  const hasThinkingMessage = messages.some(
    (message) =>
      message.role === "assistant" &&
      message.content === AGENT_MESSAGES.CHAT_THINKING
  )
  const miroCardsAnchorMode =
    approvalRequired && wantsTasks && stepState?.tasksCreated
      ? ("after_tasks_created" as const)
      : ("after_miro_started" as const)
  const miroCardsAnchorMessageId = useMemo(
    () => getMiroCardsAnchorMessageId(messages, { mode: miroCardsAnchorMode }),
    [messages, miroCardsAnchorMode]
  )
  const showMiroCardAfterApprovalTasks =
    !approvalRequired || !wantsTasks || Boolean(stepState?.tasksCreated)
  const miroBoardCardState = useMemo(
    () =>
      deriveMiroBoardCardState(messages, {
        miroGenerateInFlight,
        approvalRequired,
        tasksCreated: stepState?.tasksCreated,
        wantsTasks,
      }),
    [
      messages,
      miroGenerateInFlight,
      approvalRequired,
      stepState?.tasksCreated,
      wantsTasks,
    ]
  )
  const showMiroCard = Boolean(
    wantsMiro &&
      stepState?.analysisComplete &&
      showMiroCardAfterApprovalTasks &&
      (showBottomActionCards ||
        miroGenerateInFlight ||
        miroCardsAnchorMessageId != null ||
        miroBoardCardState.status !== "idle")
  )
  const suppressMiroMessageNav = Boolean(showBottomActionCards && wantsMiro)

  const miroCardBlockId = useMemo(
    () =>
      getMiroGenerateBlockId({
        bottomCardsMessageId,
        miroCardsAnchorMessageId,
        showMiroCard,
      }),
    [bottomCardsMessageId, miroCardsAnchorMessageId, showMiroCard]
  )

  const boardBlockIds = useMemo(
    () =>
      getMessageBoardBlockIds(messages, {
        latestAnalysisMessageId,
        showFollowUpToggle,
        stepState,
        tasksCardMessageId,
        bottomCardsMessageId,
        showMiroCard,
        showReupload: Boolean(stepState?.analysisComplete),
        suppressMiroMessageNav,
        miroCardsAnchorMode,
        wantsTasks,
        wantsDecisions,
        bypassRevealQueue,
      }),
    [
      bypassRevealQueue,
      messages,
      latestAnalysisMessageId,
      showFollowUpToggle,
      stepState,
      tasksCardMessageId,
      bottomCardsMessageId,
      showMiroCard,
      suppressMiroMessageNav,
      miroCardsAnchorMode,
      wantsTasks,
      wantsDecisions,
    ]
  )
  const extraPartIdsOnQuit = useMemo(
    () => (stepState?.analysisComplete ? ["reupload-button"] : []),
    [stepState?.analysisComplete]
  )

  // Track whether the user is currently at (or near) the bottom.
  useEffect(() => {
    const el = scrollContainerRef.current
    if (!el) return

    const thresholdPx = 24
    const update = () => {
      const distanceFromBottom = el.scrollHeight - (el.scrollTop + el.clientHeight)
      wasAtBottomRef.current = distanceFromBottom <= thresholdPx
      prevScrollTopRef.current = el.scrollTop
      prevScrollHeightRef.current = el.scrollHeight
    }

    update()
    el.addEventListener("scroll", update, { passive: true })
    return () => el.removeEventListener("scroll", update)
  }, [])

  // On new messages:
  // - if user is at bottom, keep them at bottom
  // - otherwise, preserve their viewport position (so new messages don't jump the scroll)
  useLayoutEffect(() => {
    const el = scrollContainerRef.current
    if (!el) return

    const prevScrollTop = prevScrollTopRef.current
    const prevScrollHeight = prevScrollHeightRef.current
    const nextScrollHeight = el.scrollHeight

    if (wasAtBottomRef.current) {
      el.scrollTop = nextScrollHeight
    } else {
      const delta = nextScrollHeight - prevScrollHeight
      if (Number.isFinite(delta) && delta !== 0) {
        el.scrollTop = prevScrollTop + delta
      }
    }

    prevScrollTopRef.current = el.scrollTop
    prevScrollHeightRef.current = el.scrollHeight
  }, [messages])

  const renderMiroActionCards = (blockId: string) => (
    <AgentMessageBoardBlock blockId={blockId}>
      <MiroGenerateCard
        status={miroBoardCardState.status}
        boardHref={miroBoardCardState.boardHref}
        errorMessage={miroBoardCardState.errorMessage}
        messageId={bottomCardsMessageId}
        blockId={blockId}
        onGenerate={() => onAction?.("generate_miro")}
        onOpenBoard={
          miroBoardCardState.boardHref
            ? () =>
                onNavigate?.("miro", {
                  id: bottomCardsMessageId,
                  role: "assistant",
                  content: "",
                  navigateTo: "miro",
                  navigateHref: miroBoardCardState.boardHref,
                })
            : undefined
        }
        disabled={
          miroBoardCardState.status !== "idle" &&
          miroBoardCardState.status !== "failed"
        }
        disabledHint={
          miroBoardCardState.status === "waiting_tasks_generation"
            ? "Approve task creation to enable Miro generation."
            : undefined
        }
      />
    </AgentMessageBoardBlock>
  )

  return (
    <AgentMessageBoardTextProvider
      isStreaming={isStreaming}
      sessionId={sessionId}
      boardBlockIds={boardBlockIds}
      extraPartIdsOnQuit={extraPartIdsOnQuit}
      onRenderFinishChange={onRenderFinishChange}
    >
      <div ref={scrollContainerRef} className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
        {messages.map((message) => (
          message.type === "approval_request" ? null : (
          <Fragment key={message.id}>
          <div>
          <div
            className={cn(
              "flex gap-3",
              message.role === "user" && "flex-row-reverse"
            )}
          >
            {/* Avatar */}
            <AgentMessageBoardAvatar
              role={message.role}
              forceVisible={bypassRevealQueue && message.role === "assistant"}
              blockIds={
                message.role === "assistant"
                  ? getAssistantMessageBlockIds(message, {
                      latestAnalysisMessageId,
                      showFollowUpToggle,
                      stepState,
                      tasksCardMessageId,
                      suppressMiroMessageNav,
                      wantsTasks,
                      wantsDecisions,
                    })
                  : undefined
              }
            />

            {/* Content */}
            <div className={cn("max-w-[80%] space-y-2", message.role === "user" && "items-end")}>
              {/* File uploaded indicator */}
              {message.type === "file_uploaded" && message.fileName && (
                <div className="flex items-center gap-2 rounded-lg bg-muted/50 border border-border px-3 py-2">
                  <FileSpreadsheet className="h-4 w-4 text-primary" />
                  <span className="text-sm text-foreground">{message.fileName}</span>
                </div>
              )}

              {/* Text bubble — hidden for calendar_invite which renders its own card */}
              {message.content &&
                message.type !== "calendar_invite" &&
                message.type !== "file_uploaded" && (
                message.role === "assistant" ? (
                  bypassRevealQueue ? (
                    <div
                      className={cn(
                        "rounded-lg px-4 py-2.5 text-sm bg-muted text-foreground",
                        message.content === AGENT_MESSAGES.CHAT_THINKING && "animate-pulse"
                      )}
                    >
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {message.content}
                      </ReactMarkdown>
                    </div>
                  ) : (
                    <AgentMessageBoardBlock blockId={`${message.id}-bubble`}>
                      <div
                        className={cn(
                          "rounded-lg px-4 py-2.5 text-sm bg-muted text-foreground",
                          message.content === AGENT_MESSAGES.CHAT_THINKING && "animate-pulse"
                        )}
                      >
                        <AgentMessageBoardMarkdown
                          target={message.content}
                          partId={`${message.id}-content`}
                          blockId={`${message.id}-bubble`}
                        />
                      </div>
                    </AgentMessageBoardBlock>
                  )
                ) : (
                  <div className="rounded-lg px-4 py-2.5 text-sm whitespace-pre-wrap bg-primary text-primary-foreground">
                    {message.content}
                  </div>
                )
              )}

              {/* Step progress */}
              {message.role === "assistant" &&
                message.stepProgress &&
                message.stepProgress.length > 0 && (
                <AgentMessageBoardBlock blockId={`${message.id}-steps`}>
                  <StepProgress
                    steps={message.stepProgress}
                    messageId={message.id}
                    blockId={`${message.id}-steps`}
                  />
                </AgentMessageBoardBlock>
              )}

              {/* Navigation button */}
              {message.role === "assistant" && message.navigateTo && message.navigateLabel && (
                bypassRevealQueue ? (
                  <Button
                    size="sm"
                    variant="outline"
                    className="gap-2"
                    disabled={message.navigateDisabled}
                    onClick={() => onNavigate?.(message.navigateTo!, message)}
                  >
                    {message.navigateLabel}
                    <ArrowRight className="h-3.5 w-3.5" />
                  </Button>
                ) : (
                  <AgentMessageBoardBlock blockId={`${message.id}-nav`}>
                    <Button
                      size="sm"
                      variant="outline"
                      className="gap-2"
                      disabled={message.navigateDisabled}
                      onClick={() => onNavigate?.(message.navigateTo!, message)}
                    >
                      <AgentMessageBoardText
                        target={message.navigateLabel}
                        partId={`${message.id}-nav-label`}
                        blockId={`${message.id}-nav`}
                      />
                      <ArrowRight className="h-3.5 w-3.5" />
                    </Button>
                  </AgentMessageBoardBlock>
                )
              )}

              {/* Calendar invite prompt */}
              {message.role === "assistant" && message.type === "calendar_invite" && (
                <AgentMessageBoardBlock blockId={`${message.id}-calendar`}>
                  <div className="flex items-center gap-2 rounded-lg border border-violet-200 bg-violet-50 px-3 py-2">
                    <CalendarPlus className="h-4 w-4 shrink-0 text-violet-600" />
                    <span className="text-sm text-violet-800">
                      <AgentMessageBoardText
                        target={message.content}
                        partId={`${message.id}-calendar-text`}
                        blockId={`${message.id}-calendar`}
                      />
                    </span>
                  </div>
                </AgentMessageBoardBlock>
              )}


              {/* Workflow step paused — await_confirmation */}
              {message.role === "assistant" &&
                message.type === "confirmation_request" && (
                <AgentMessageBoardBlock blockId={`${message.id}-step-confirm`}>
                  <WorkflowStepConfirmCard
                    message={message.content || "Please confirm to continue."}
                    onContinue={() => onResumeWorkflow?.(message.id)}
                    disabled={isStreaming}
                  />
                </AgentMessageBoardBlock>
              )}

              {/* Column mapping is handled silently — stored in DB, not shown to user */}

              {/* Anomaly card (read-only, collapsed) — block id must match agentMessageBoardBlockIds */}
              {message.role === "assistant" &&
                message.anomalies &&
                message.anomalies.length > 0 &&
                (message.type === "spreadsheet_anomalies" || message.type === "analysis") && (
                <AgentMessageBoardBlock blockId={`${message.id}-anomalies`}>
                  <SpreadsheetInsightAnomalyCard
                    anomalies={message.anomalies}
                    spreadsheetId={message.spreadsheetId}
                    sheetId={message.sheetId}
                  />
                </AgentMessageBoardBlock>
              )}


              {/* TaskListCard: primary review surface. Renders before decision tree card. */}
              {message.role === "assistant" &&
                wantsTasks &&
                message.recommendedTasks &&
                message.recommendedTasks.length > 0 &&
                (message.type === "analysis" ||
                  message.type === "spreadsheet_anomalies" ||
                  message.type === "tasks_created") &&
                tasksCardMessageId != null &&
                message.id === tasksCardMessageId && (
                <AgentMessageBoardBlock blockId={`${message.id}-tasks`}>
                  <TaskListCard
                    tasks={message.recommendedTasks}
                    messageId={message.id}
                    blockId={`${message.id}-tasks`}
                    approvalRequired={approvalRequired}
                    generatedTaskIndexes={generatedTaskIndexes}
                    skippedTaskIndexes={skippedTaskIndexes}
                    createdTaskIdByIndex={createdTaskIdByIndex}
                    generating={Boolean(tasksApprovalGenerating) || Boolean(generatingTasks)}
                    generationStatus={taskGenerationStatus}
                    approvalMode={Boolean(pendingTaskApproval)}
                    selectionMode={taskSelectionMode}
                    selectedIndexes={selectedTaskIndexes}
                    onSelectedIndexesChange={onSelectedTaskIndexesChange}
                    onCreateSelected={pendingTaskApproval ? onApproveSelectedTasks : undefined}
                    createButtonDisabled={Boolean(approvalDisabled) || Boolean(tasksApprovalGenerating)}
                    onCreateAll={
                      !pendingTaskApproval &&
                      stepState?.analysisComplete &&
                      !stepState?.tasksCreated
                        ? () => onAction?.("create_tasks")
                        : undefined
                    }
                  />
                </AgentMessageBoardBlock>
              )}

              {message.role === "assistant" &&
                wantsDecisions &&
                message.recommendedDecisionTree?.nodes &&
                message.recommendedDecisionTree.nodes.length > 0 &&
                (message.type === "analysis" ||
                  message.type === "decisions_created" ||
                  message.type === "tasks_created") && (
                <AgentMessageBoardBlock blockId={`${message.id}-decisions`}>
                  <DecisionTreeListCard
                    nodes={message.recommendedDecisionTree.nodes}
                    messageId={message.id}
                    blockId={`${message.id}-decisions`}
                    approvalRequired={approvalRequired}
                    generatedDecisionRefs={generatedDecisionRefs}
                    skippedDecisionRefs={skippedDecisionRefs}
                    createdDecisionByRef={createdDecisionByRef}
                    generating={Boolean(decisionsApprovalGenerating) || Boolean(generatingDecisions)}
                    generationStatus={decisionGenerationStatus}
                    approvalMode={Boolean(pendingDecisionApproval)}
                    selectionMode={decisionSelectionMode}
                    selectedRefs={selectedDecisionRefs}
                    onSelectedRefsChange={onSelectedDecisionRefsChange}
                    onCreateSelected={pendingDecisionApproval ? onApproveSelectedDecisions : undefined}
                    createButtonDisabled={
                      Boolean(approvalDisabled) || Boolean(decisionsApprovalGenerating)
                    }
                    onCreateAll={
                      !approvalRequired &&
                      !pendingDecisionApproval &&
                      stepState?.analysisComplete &&
                      !stepState?.decisionsCreated
                        ? () => onAction?.("create_decisions")
                        : undefined
                    }
                  />
                </AgentMessageBoardBlock>
              )}

              {message.role === "assistant" &&
                (message.type === "analysis" || message.type === "tasks_created") &&
                message.calendarEvents !== undefined && (
                <AgentMessageBoardBlock blockId={`${message.id}-calendar-events`}>
                  <CalendarEventsCard
                    events={message.calendarEvents}
                    messageId={message.id}
                    blockId={`${message.id}-calendar-events`}
                  />
                </AgentMessageBoardBlock>
              )}

              {message.role === "assistant" &&
                showFollowUpToggle &&
                message.id === latestAnalysisMessageId &&
                (!stepState || downstreamOutputsComplete) && (
                <AgentMessageBoardBlock blockId={`${message.id}-followup`}>
                  <FollowUpCard
                    active={followUpActive}
                    onToggle={() => onAction?.(followUpActive ? "cancel_follow_up" : "start_follow_up")}
                    messageId={message.id}
                    blockId={`${message.id}-followup`}
                  />
                </AgentMessageBoardBlock>
              )}
            </div>
          </div>
          </div>
          {showMiroCard &&
            miroCardBlockId &&
            message.id === miroCardsAnchorMessageId &&
            renderMiroActionCards(miroCardBlockId)}
          </Fragment>
          )
        ))}

        {showMiroCard &&
          miroCardBlockId &&
          !miroCardsAnchorMessageId &&
          renderMiroActionCards(miroCardBlockId)}

        {showRevisitThinkingBubble && !hasThinkingMessage && (
          <div className="flex gap-3">
            <AgentMessageBoardAvatar role="assistant" forceVisible />
            <div className="max-w-[80%] space-y-2">
              <div className="rounded-lg px-4 py-2.5 text-sm bg-muted text-foreground animate-pulse">
                {AGENT_MESSAGES.CHAT_THINKING}
              </div>
            </div>
          </div>
        )}

        {stepState?.analysisComplete && (
          <AgentMessageBoardBlock blockId="reupload">
            <div className="flex justify-center pt-2 pb-1">
              <Button
                size="sm"
                variant="outline"
                className="gap-1.5 text-xs text-muted-foreground"
                onClick={() => onReupload?.()}
              >
                <UploadCloud className="h-3.5 w-3.5" />
                <AgentMessageBoardText
                  target="Upload New File"
                  partId="reupload-button"
                  blockId="reupload"
                />
              </Button>
            </div>
          </AgentMessageBoardBlock>
        )}
      </div>
    </AgentMessageBoardTextProvider>
  )
}
