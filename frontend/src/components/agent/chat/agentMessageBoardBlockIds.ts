import type { WorkflowStepState } from "@/types/agent"

export type MessageForBlockIds = {
  id: string
  role?: "user" | "assistant"
  content: string
  type?: string
  eventType?: string
  stepProgress?: unknown[]
  navigateTo?: string
  navigateLabel?: string
  anomalies?: unknown[]
  recommendedTasks?: unknown[]
  recommendedDecisionTree?: { nodes?: unknown[] }
  calendarEvents?: unknown[]
}

export type MiroCardsAnchorMode = "after_miro_started" | "after_tasks_created"

/**
 * Message id after which the recommended Miro card is inserted.
 * Approval + tasks: after the last tasks_created bubble (before queued Miro status).
 * Otherwise: after the last miro_generation_started bubble when present.
 */
export function getMiroCardsAnchorMessageId(
  messages: MessageForBlockIds[],
  options?: { mode?: MiroCardsAnchorMode }
): string | null {
  const mode = options?.mode ?? "after_miro_started"

  if (mode === "after_tasks_created") {
    let anchorId: string | null = null
    for (const message of messages) {
      if (message.role === "assistant" && message.type === "tasks_created") {
        anchorId = message.id
      }
    }
    return anchorId
  }

  let anchorId: string | null = null
  for (const message of messages) {
    if (message.role === "assistant" && message.eventType === "miro_generation_started") {
      anchorId = message.id
    }
  }
  return anchorId
}

/** Block id for the single recommended Miro card (stable across status / anchor changes). */
export function getMiroGenerateBlockId(options: {
  bottomCardsMessageId: string
  miroCardsAnchorMessageId?: string | null
  showMiroCard?: boolean
}): string | null {
  if (!options.showMiroCard) return null
  return `${options.bottomCardsMessageId}-miro-generate`
}

function getMiroActionCardBlockIds(options: {
  bottomCardsMessageId: string
  miroCardsAnchorMessageId?: string | null
  showMiroCard?: boolean
}): string[] {
  const blockId = getMiroGenerateBlockId(options)
  return blockId ? [blockId] : []
}

export type AssistantMessageBlockIdsOptions = {
  latestAnalysisMessageId?: string | null
  showFollowUpToggle?: boolean
  stepState?: WorkflowStepState
  /** When set, only this message gets the recommended-tasks block id (approval flow). */
  tasksCardMessageId?: string | null
  /** Miro nav is shown on the action card; omit per-message nav from the render queue. */
  suppressMiroMessageNav?: boolean
  wantsTasks?: boolean
  wantsDecisions?: boolean
}

/** Block ids for an assistant message in the same render order as MessageList. */
export function getAssistantMessageBlockIds(
  message: MessageForBlockIds,
  options: AssistantMessageBlockIdsOptions = {}
): string[] {
  const {
    latestAnalysisMessageId,
    showFollowUpToggle,
    stepState,
    tasksCardMessageId,
    wantsTasks = true,
    wantsDecisions = true,
  } = options
  const ids: string[] = []

  if (message.content && message.type !== "calendar_invite") {
    ids.push(`${message.id}-bubble`)
  }

  if (message.stepProgress && message.stepProgress.length > 0) {
    ids.push(`${message.id}-steps`)
  }

  if (message.navigateTo && message.navigateLabel) {
    const hideMiroNav =
      options.suppressMiroMessageNav && message.navigateTo === "miro"
    if (!hideMiroNav) {
      ids.push(`${message.id}-nav`)
    }
  }

  if (message.type === "calendar_invite") {
    ids.push(`${message.id}-calendar`)
  }

  if (message.anomalies && message.anomalies.length > 0) {
    ids.push(`${message.id}-anomalies`)
  }

  if (
    wantsTasks &&
    message.recommendedTasks &&
    message.recommendedTasks.length > 0 &&
    tasksCardMessageId != null &&
    message.id === tasksCardMessageId
  ) {
    ids.push(`${message.id}-tasks`)
  }

  if (
    wantsDecisions &&
    message.recommendedDecisionTree?.nodes &&
    message.recommendedDecisionTree.nodes.length > 0 &&
    (message.type === "analysis" ||
      message.type === "decisions_created" ||
      message.type === "tasks_created")
  ) {
    ids.push(`${message.id}-decisions`)
  }

  if (
    (message.type === "analysis" || message.type === "tasks_created") &&
    message.calendarEvents !== undefined
  ) {
    ids.push(`${message.id}-calendar-events`)
  }

  if (
    showFollowUpToggle &&
    message.id === latestAnalysisMessageId &&
    (!stepState || stepState.tasksCreated)
  ) {
    ids.push(`${message.id}-followup`)
  }

  return ids
}

export type MessageBoardBlockIdsOptions = AssistantMessageBlockIdsOptions & {
  bottomCardsMessageId: string
  showMiroCard?: boolean
  showReupload?: boolean
  suppressMiroMessageNav?: boolean
  miroCardsAnchorMode?: MiroCardsAnchorMode
  /**
   * Pattern & pivot generation modes render messages directly and do not use
   * the reveal queue. When set, no board block ids are produced so the queue
   * cannot get out of sync with what those modes show.
   */
  bypassRevealQueue?: boolean
}

/** All board block ids in top-to-bottom render order (matches MessageList). */
export function getMessageBoardBlockIds(
  messages: MessageForBlockIds[],
  options: MessageBoardBlockIdsOptions
): string[] {
  if (options.bypassRevealQueue) {
    // Bypass clients render messages directly; emitting block ids here would
    // desync the reveal queue from what is actually on screen.
    return []
  }

  const ids: string[] = []
  const miroAnchorId = getMiroCardsAnchorMessageId(messages, {
    mode: options.miroCardsAnchorMode,
  })
  const miroCardBlockIds = getMiroActionCardBlockIds({
    bottomCardsMessageId: options.bottomCardsMessageId,
    miroCardsAnchorMessageId: miroAnchorId,
    showMiroCard: options.showMiroCard,
  })

  for (const message of messages) {
    if (message.type === "approval_request") continue
    if (message.role !== "assistant") continue
    ids.push(
      ...getAssistantMessageBlockIds(message, {
        latestAnalysisMessageId: options.latestAnalysisMessageId,
        showFollowUpToggle: options.showFollowUpToggle,
        stepState: options.stepState,
        tasksCardMessageId: options.tasksCardMessageId,
        suppressMiroMessageNav: options.suppressMiroMessageNav,
        wantsTasks: options.wantsTasks,
        wantsDecisions: options.wantsDecisions,
      })
    )
    if (message.id === miroAnchorId) {
      ids.push(...miroCardBlockIds)
    }
  }

  if (!miroAnchorId && miroCardBlockIds.length > 0) {
    ids.push(...miroCardBlockIds)
  }

  if (options.showReupload) {
    ids.push("reupload")
  }

  return ids
}
