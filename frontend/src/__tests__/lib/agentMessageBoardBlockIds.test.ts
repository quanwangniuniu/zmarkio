import {
  getAssistantMessageBlockIds,
  getMessageBoardBlockIds,
  getMiroGenerateBlockId,
} from "@/components/agent/chat/agentMessageBoardBlockIds"

describe("agentMessageBoardBlockIds", () => {
  const analysisMessage = {
    id: "msg-analysis",
    role: "assistant" as const,
    content: "Summary",
    type: "analysis",
    recommendedTasks: [{ title: "Task A" }],
  }

  const followUpMessage = {
    id: "msg-followup",
    role: "assistant" as const,
    content: "Here is more detail.",
    type: "text" as const,
  }

  it("queues miro generate card after prior blocks when shown at bottom", () => {
    const ids = getMessageBoardBlockIds([analysisMessage, followUpMessage], {
      bottomCardsMessageId: "msg-analysis",
      showMiroCard: true,
      showReupload: true,
    })

    expect(ids).toContain("msg-analysis-miro-generate")
    expect(ids.indexOf("msg-analysis-miro-generate")).toBeGreaterThan(
      ids.indexOf("msg-followup-bubble")
    )
    expect(ids.indexOf("reupload")).toBeGreaterThan(ids.indexOf("msg-analysis-miro-generate"))
  })

  it("orders miro card after queued status bubble when anchored on miro started", () => {
    const queuedMiroMessage = {
      id: "msg-queued",
      role: "assistant" as const,
      content: "Queued Miro board generation",
      type: "miro_status",
      eventType: "miro_generation_started",
      navigateTo: "miro",
      navigateLabel: "Generating Miro...",
    }
    const readyMiroMessage = {
      id: "msg-ready",
      role: "assistant" as const,
      content: "Miro board is ready",
      type: "miro_status",
      eventType: "miro_board_created",
    }

    const ids = getMessageBoardBlockIds(
      [analysisMessage, queuedMiroMessage, readyMiroMessage],
      {
        bottomCardsMessageId: "msg-analysis",
        showMiroCard: true,
        suppressMiroMessageNav: true,
      }
    )

    const queuedBubbleIdx = ids.indexOf("msg-queued-bubble")
    const miroCardIdx = ids.indexOf("msg-analysis-miro-generate")
    const readyBubbleIdx = ids.indexOf("msg-ready-bubble")

    expect(queuedBubbleIdx).toBeGreaterThanOrEqual(0)
    expect(miroCardIdx).toBeGreaterThan(queuedBubbleIdx)
    expect(ids).not.toContain("msg-queued-nav")
    expect(readyBubbleIdx).toBeGreaterThan(miroCardIdx)
  })

  it("orders miro card between tasks created and queued miro when approval anchor mode is used", () => {
    const tasksCreatedMessage = {
      id: "msg-tasks",
      role: "assistant" as const,
      content: "Created 4 tasks.",
      type: "tasks_created",
      navigateTo: "tasks",
      navigateLabel: "Go to Tasks",
    }
    const queuedMiroMessage = {
      id: "msg-queued",
      role: "assistant" as const,
      content: "Queued Miro board generation",
      type: "miro_status",
      eventType: "miro_generation_started",
      navigateTo: "miro",
      navigateLabel: "Generating Miro...",
    }
    const readyMiroMessage = {
      id: "msg-ready",
      role: "assistant" as const,
      content: "Miro board is ready",
      type: "miro_status",
      eventType: "miro_board_created",
    }

    const ids = getMessageBoardBlockIds(
      [analysisMessage, tasksCreatedMessage, queuedMiroMessage, readyMiroMessage],
      {
        bottomCardsMessageId: "msg-analysis",
        showMiroCard: true,
        suppressMiroMessageNav: true,
        miroCardsAnchorMode: "after_tasks_created",
      }
    )

    const tasksBubbleIdx = ids.indexOf("msg-tasks-bubble")
    const miroCardIdx = ids.indexOf("msg-analysis-miro-generate")
    const queuedBubbleIdx = ids.indexOf("msg-queued-bubble")
    const readyBubbleIdx = ids.indexOf("msg-ready-bubble")

    expect(tasksBubbleIdx).toBeGreaterThanOrEqual(0)
    expect(miroCardIdx).toBeGreaterThan(tasksBubbleIdx)
    expect(queuedBubbleIdx).toBeGreaterThan(miroCardIdx)
    expect(readyBubbleIdx).toBeGreaterThan(queuedBubbleIdx)
    expect(ids).not.toContain("msg-queued-nav")
  })

  it("matches per-message block ids from getAssistantMessageBlockIds", () => {
    const boardIds = getMessageBoardBlockIds([analysisMessage], {
      bottomCardsMessageId: "msg-analysis",
      tasksCardMessageId: "msg-analysis",
    })
    const messageIds = getAssistantMessageBlockIds(analysisMessage, {
      tasksCardMessageId: "msg-analysis",
    })
    expect(boardIds.slice(0, messageIds.length)).toEqual(messageIds)
  })

  it("renders recommended tasks block only on the designated message during approval", () => {
    const earlierAnalysis = {
      id: "msg-analysis-first",
      role: "assistant" as const,
      content: "Summary",
      type: "analysis",
      recommendedTasks: [{ title: "Task A" }],
    }
    const approvalTasksMessage = {
      id: "msg-approval",
      role: "assistant" as const,
      content: "Approval required before task.",
      type: "analysis",
      recommendedTasks: [{ title: "Task A" }],
    }

    const boardIds = getMessageBoardBlockIds(
      [earlierAnalysis, approvalTasksMessage],
      {
        bottomCardsMessageId: "msg-approval",
        tasksCardMessageId: "msg-approval",
      }
    )

    expect(boardIds).not.toContain("msg-analysis-first-tasks")
    expect(boardIds).toContain("msg-approval-tasks")
  })

  it("getMiroGenerateBlockId stays tied to bottomCardsMessageId when anchor changes", () => {
    expect(
      getMiroGenerateBlockId({
        bottomCardsMessageId: "msg-analysis",
        miroCardsAnchorMessageId: "msg-queued",
        showMiroCard: true,
      })
    ).toBe("msg-analysis-miro-generate")
  })

  it("orders decision tree block after tasks block on the same analysis message", () => {
    const analysisWithBoth = {
      id: "msg-analysis",
      role: "assistant" as const,
      content: "Summary",
      type: "analysis",
      recommendedTasks: [{ summary: "Task A", type: "report", priority: "HIGH" as const }],
      recommendedDecisionTree: {
        nodes: [{ ref: "n1", layer: 1, title: "Decision A", parent_refs: [] }],
      },
    }

    const ids = getMessageBoardBlockIds([analysisWithBoth], {
      bottomCardsMessageId: "msg-analysis",
      tasksCardMessageId: "msg-analysis",
      wantsTasks: true,
      wantsDecisions: true,
    })

    const tasksIdx = ids.indexOf("msg-analysis-tasks")
    const decisionsIdx = ids.indexOf("msg-analysis-decisions")

    expect(tasksIdx).toBeGreaterThanOrEqual(0)
    expect(decisionsIdx).toBeGreaterThan(tasksIdx)
  })

  it("getMiroGenerateBlockId returns null when card is hidden", () => {
    expect(
      getMiroGenerateBlockId({
        bottomCardsMessageId: "msg-analysis",
        showMiroCard: false,
      })
    ).toBeNull()
  })
})
