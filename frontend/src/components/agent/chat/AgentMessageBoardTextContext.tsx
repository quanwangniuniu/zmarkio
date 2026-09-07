"use client"

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react"
import {
  areAllPartsComplete,
  getFirstIncompleteJobId,
  getFirstIncompletePartId,
  type RenderJob,
} from "@/lib/agentMessageBoardRenderQueue"
import {
  getAgentMessageBoardPartResumeLength,
  isAgentMessageBoardTextPartRead,
  setAgentMessageBoardRenderEffectsCompletedOnQuit,
  loadAgentMessageBoardRenderSnapshot,
  markAgentMessageBoardSessionQuit,
  markAgentMessageBoardTextPartRead,
  saveAgentMessageBoardRenderSnapshot,
  snapshotHasProgress,
  type PartProgress,
  type SessionRenderSnapshot,
} from "@/lib/agentMessageBoardReadState"

const SNAPSHOT_FLUSH_MS = 400
const EMPTY_BOARD_BLOCK_IDS: string[] = []

export type AgentMessageBoardTextContextValue = {
  /** Active message board session; changes reset the render queue. */
  sessionId: string | null
  /** True while the agent session is receiving streamed SSE content. */
  isStreaming: boolean
  queueVersion: number
  registerBlock: (blockId: string) => () => void
  registerTextPart: (partId: string, blockId?: string) => () => void
  isBlockRevealed: (blockId: string) => boolean
  isTextPartActive: (partId: string, blockId?: string) => boolean
  isTextPartComplete: (partId: string, blockId?: string) => boolean
  /** True when this part was fully read before (finished typing). */
  shouldSkipTyping: (partId: string) => boolean
  markTextPartComplete: (partId: string, blockId?: string) => void
  reopenTextPart: (partId: string, blockId?: string) => void
  getPartGeneration: (partId: string) => number
  /** Code-point length already revealed for a part (persisted on revisit). */
  getPartResumeLength: (partId: string) => number
  /** Complete a block that has no text parts (e.g. icon-only UI). */
  tryCompleteEmptyBlock: (blockId: string) => void
  /** Report how many characters are currently visible for a text part. */
  reportTextPartDisplay: (partId: string, visibleLength: number) => void
  getTextPartVisibleLength: (partId: string) => number
  getBlockTextPartIds: (blockId: string) => string[]
}

const AgentMessageBoardTextContext = createContext<AgentMessageBoardTextContextValue | null>(
  null
)

export function useAgentMessageBoardTextContext(): AgentMessageBoardTextContextValue {
  const ctx = useContext(AgentMessageBoardTextContext)
  if (!ctx) {
    throw new Error(
      "AgentMessageBoardText must be rendered inside AgentMessageBoardTextProvider (agent message board)."
    )
  }
  return ctx
}

type AgentMessageBoardTextProviderProps = {
  children: React.ReactNode
  isStreaming?: boolean
  sessionId?: string | null
  /** Top-to-bottom block ids; gates reveal so later blocks never show before earlier ones. */
  boardBlockIds?: string[]
  /** Non-message part ids to mark read on quit (e.g. reupload button). */
  extraPartIdsOnQuit?: string[]
  /** Notifies when full board render effects finish state changes. */
  onRenderFinishChange?: (finished: boolean) => void
}

function buildRenderSnapshot(
  jobs: RenderJob[],
  completedJobs: Set<string>,
  blockParts: Map<string, string[]>,
  blockPartsCompleted: Map<string, Set<string>>,
  partVisibleLength: Map<string, number>
): SessionRenderSnapshot {
  const completedJobIds = [...completedJobs]
  const blockPartsCompletedRecord: Record<string, string[]> = {}
  blockPartsCompleted.forEach((completed, blockId) => {
    blockPartsCompletedRecord[blockId] = [...completed]
  })

  const partProgress: Record<string, PartProgress> = {}
  const seen = new Set<string>()

  const recordPart = (partId: string, isComplete: boolean) => {
    if (seen.has(partId)) return
    seen.add(partId)
    const visibleLength = partVisibleLength.get(partId) ?? 0
    if (isComplete) {
      partProgress[partId] = { visibleLength, complete: true }
    } else if (visibleLength > 0) {
      partProgress[partId] = { visibleLength }
    }
  }

  for (const [blockId, parts] of blockParts) {
    const completed = blockPartsCompleted.get(blockId) ?? new Set()
    for (const partId of parts) {
      recordPart(partId, completed.has(partId))
    }
  }

  for (const job of jobs) {
    if (job.kind === "text") {
      recordPart(job.id, completedJobs.has(job.id))
    }
  }

  for (const [partId] of partVisibleLength) {
    if (seen.has(partId)) continue
    let isComplete = completedJobs.has(partId)
    if (!isComplete) {
      for (const [blockId, parts] of blockParts) {
        if (!parts.includes(partId)) continue
        const completed = blockPartsCompleted.get(blockId) ?? new Set()
        if (completed.has(partId)) {
          isComplete = true
          break
        }
      }
    }
    recordPart(partId, isComplete)
  }

  return {
    completedJobIds,
    blockPartsCompleted: blockPartsCompletedRecord,
    partProgress,
  }
}

function sortJobsByCanonicalOrder(jobs: RenderJob[], canonicalOrder: string[]): void {
  if (canonicalOrder.length === 0) return
  jobs.sort((a, b) => {
    const ai = canonicalOrder.indexOf(a.id)
    const bi = canonicalOrder.indexOf(b.id)
    if (ai === -1 && bi === -1) return 0
    if (ai === -1) return 1
    if (bi === -1) return -1
    return ai - bi
  })
}

function insertJobAtCanonicalOrder(
  jobs: RenderJob[],
  job: RenderJob,
  canonicalOrder: string[]
): void {
  const canonicalIndex = canonicalOrder.indexOf(job.id)
  if (canonicalIndex === -1) {
    jobs.push(job)
    return
  }

  let insertAt = jobs.length
  for (let i = 0; i < jobs.length; i++) {
    const existingIndex = canonicalOrder.indexOf(jobs[i].id)
    if (existingIndex !== -1 && existingIndex > canonicalIndex) {
      insertAt = i
      break
    }
  }
  jobs.splice(insertAt, 0, job)
}

function areAllPriorBlocksComplete(
  blockId: string,
  canonicalOrder: string[],
  completed: Set<string>
): boolean {
  const idx = canonicalOrder.indexOf(blockId)
  if (idx <= 0) return true
  for (let i = 0; i < idx; i++) {
    if (!completed.has(canonicalOrder[i])) return false
  }
  return true
}

function hasQueueRenderEffectsCompleted(jobs: RenderJob[], completed: Set<string>): boolean {
  // No queued jobs means there are no pending render effects.
  return getFirstIncompleteJobId(jobs, completed) == null
}

type DetachedBlockState = {
  parts: string[]
  completedParts: string[]
  blockCompleted: boolean
  partVisibleLengths: Record<string, number>
}

export function AgentMessageBoardTextProvider({
  children,
  isStreaming = false,
  sessionId = null,
  boardBlockIds = EMPTY_BOARD_BLOCK_IDS,
  extraPartIdsOnQuit = [],
  onRenderFinishChange,
}: AgentMessageBoardTextProviderProps) {
  const [queueVersion, setQueueVersion] = useState(0)
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const jobsRef = useRef<RenderJob[]>([])
  const completedJobsRef = useRef(new Set<string>())
  const blockPartsRef = useRef(new Map<string, string[]>())
  const blockPartsCompletedRef = useRef(new Map<string, Set<string>>())
  const generationRef = useRef(new Map<string, number>())
  const partVisibleLengthRef = useRef(new Map<string, number>())
  const detachedBlocksRef = useRef(new Map<string, DetachedBlockState>())
  const snapshotRef = useRef<SessionRenderSnapshot | null>(
    sessionId ? loadAgentMessageBoardRenderSnapshot(sessionId) : null
  )
  /** Latest queue state; child unregister runs before provider cleanup and clears refs. */
  const persistSnapshotRef = useRef<SessionRenderSnapshot | null>(null)
  const sessionIdRef = useRef(sessionId)
  const prevSessionIdRef = useRef<string | null | undefined>(undefined)
  const lastSnapshotFlushRef = useRef(0)
  const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const renderFinishReportedRef = useRef<boolean | null>(null)
  const canonicalBlockIdsRef = useRef<string[]>(boardBlockIds)
  canonicalBlockIdsRef.current = boardBlockIds

  sessionIdRef.current = sessionId

  const syncPersistedSnapshot = useCallback(() => {
    const sid = sessionIdRef.current
    if (!sid) return
    persistSnapshotRef.current = buildRenderSnapshot(
      jobsRef.current,
      completedJobsRef.current,
      blockPartsRef.current,
      blockPartsCompletedRef.current,
      partVisibleLengthRef.current
    )
  }, [])

  const flushPersistedSnapshot = useCallback(() => {
    const sid = sessionIdRef.current
    const snapshot = persistSnapshotRef.current
    if (!sid || !snapshot || !snapshotHasProgress(snapshot)) return
    saveAgentMessageBoardRenderSnapshot(String(sid), snapshot)
    lastSnapshotFlushRef.current = Date.now()
  }, [])

  const scheduleSnapshotFlush = useCallback(() => {
    const sid = sessionIdRef.current
    if (!sid) return
    const now = Date.now()
    if (now - lastSnapshotFlushRef.current >= SNAPSHOT_FLUSH_MS) {
      flushPersistedSnapshot()
      return
    }
    if (flushTimerRef.current != null) return
    flushTimerRef.current = setTimeout(() => {
      flushTimerRef.current = null
      flushPersistedSnapshot()
    }, SNAPSHOT_FLUSH_MS)
  }, [flushPersistedSnapshot])

  const syncActiveJobId = useCallback(() => {
    setActiveJobId(getFirstIncompleteJobId(jobsRef.current, completedJobsRef.current))
  }, [])

  const bumpQueue = useCallback(() => {
    setQueueVersion((v) => v + 1)
    syncActiveJobId()
  }, [syncActiveJobId])

  const hydratePartFromSnapshot = useCallback((partId: string, blockId?: string) => {
    const snapshot = snapshotRef.current
    if (!snapshot) return

    if (blockId) {
      const completedPartIds = snapshot.blockPartsCompleted[blockId]
      if (completedPartIds?.includes(partId)) {
        const completed = blockPartsCompletedRef.current.get(blockId) ?? new Set()
        completed.add(partId)
        blockPartsCompletedRef.current.set(blockId, completed)
      }
    } else if (snapshot.completedJobIds.includes(partId)) {
      completedJobsRef.current.add(partId)
    }

    const progress = snapshot.partProgress[partId]
    if (!progress) return

    if (progress.complete) {
      if (blockId) {
        const completed = blockPartsCompletedRef.current.get(blockId) ?? new Set()
        completed.add(partId)
        blockPartsCompletedRef.current.set(blockId, completed)
      } else {
        completedJobsRef.current.add(partId)
      }
      return
    }

    if (progress.visibleLength > 0) {
      partVisibleLengthRef.current.set(partId, progress.visibleLength)
    }
  }, [])

  const hydrateBlockFromSnapshot = useCallback((blockId: string) => {
    const snapshot = snapshotRef.current
    if (!snapshot) return

    if (snapshot.completedJobIds.includes(blockId)) {
      completedJobsRef.current.add(blockId)
    }

    const completedPartIds = snapshot.blockPartsCompleted[blockId]
    if (completedPartIds?.length) {
      const completed = blockPartsCompletedRef.current.get(blockId) ?? new Set()
      for (const partId of completedPartIds) {
        completed.add(partId)
      }
      blockPartsCompletedRef.current.set(blockId, completed)
    }
  }, [])

  const resetJobsAfter = useCallback(
    (jobId: string) => {
      const idx = jobsRef.current.findIndex((j) => j.id === jobId)
      if (idx === -1) return

      completedJobsRef.current.delete(jobId)
      generationRef.current.set(jobId, (generationRef.current.get(jobId) ?? 0) + 1)

      for (let i = idx + 1; i < jobsRef.current.length; i++) {
        const later = jobsRef.current[i]
        completedJobsRef.current.delete(later.id)
        generationRef.current.set(later.id, (generationRef.current.get(later.id) ?? 0) + 1)

        if (later.kind === "block") {
          const parts = blockPartsRef.current.get(later.id) ?? []
          const completed = blockPartsCompletedRef.current.get(later.id) ?? new Set()
          for (const partId of parts) {
            completed.delete(partId)
            generationRef.current.set(partId, (generationRef.current.get(partId) ?? 0) + 1)
          }
          blockPartsCompletedRef.current.set(later.id, completed)
        }
      }
    },
    []
  )

  const tryCompleteBlock = useCallback(
    (blockId: string) => {
      const parts = blockPartsRef.current.get(blockId) ?? []
      const completed = blockPartsCompletedRef.current.get(blockId) ?? new Set()
      if (!areAllPartsComplete(parts, completed)) return
      if (completedJobsRef.current.has(blockId)) return
      completedJobsRef.current.add(blockId)
      bumpQueue()
    },
    [bumpQueue]
  )

  const restoreDetachedBlock = useCallback((blockId: string) => {
    const detached = detachedBlocksRef.current.get(blockId)
    if (!detached) return

    detachedBlocksRef.current.delete(blockId)
    blockPartsRef.current.set(blockId, [...detached.parts])
    blockPartsCompletedRef.current.set(blockId, new Set(detached.completedParts))
    if (detached.blockCompleted) {
      completedJobsRef.current.add(blockId)
    }
    for (const [partId, len] of Object.entries(detached.partVisibleLengths)) {
      if (len > 0) {
        partVisibleLengthRef.current.set(partId, len)
      }
    }
  }, [])

  const registerBlock = useCallback(
    (blockId: string) => {
      if (!jobsRef.current.some((j) => j.id === blockId)) {
        insertJobAtCanonicalOrder(
          jobsRef.current,
          { id: blockId, kind: "block" },
          canonicalBlockIdsRef.current
        )
        if (!blockPartsRef.current.has(blockId)) {
          blockPartsRef.current.set(blockId, [])
        }
        if (!blockPartsCompletedRef.current.has(blockId)) {
          blockPartsCompletedRef.current.set(blockId, new Set())
        }
        restoreDetachedBlock(blockId)
        hydrateBlockFromSnapshot(blockId)
        bumpQueue()
      }
      return () => {
        const parts = blockPartsRef.current.get(blockId) ?? []
        const completedParts = blockPartsCompletedRef.current.get(blockId)
        const blockCompleted = completedJobsRef.current.has(blockId)
        const hasProgress =
          blockCompleted ||
          Boolean(completedParts?.size) ||
          parts.some((partId) => (partVisibleLengthRef.current.get(partId) ?? 0) > 0)

        if (hasProgress) {
          const partVisibleLengths: Record<string, number> = {}
          for (const partId of parts) {
            const len = partVisibleLengthRef.current.get(partId) ?? 0
            if (len > 0) partVisibleLengths[partId] = len
          }
          detachedBlocksRef.current.set(blockId, {
            parts: [...parts],
            completedParts: completedParts ? [...completedParts] : [],
            blockCompleted,
            partVisibleLengths,
          })
        }

        jobsRef.current = jobsRef.current.filter((j) => j.id !== blockId)
        completedJobsRef.current.delete(blockId)
        blockPartsRef.current.delete(blockId)
        blockPartsCompletedRef.current.delete(blockId)
        generationRef.current.delete(blockId)
        bumpQueue()
      }
    },
    [bumpQueue, hydrateBlockFromSnapshot, restoreDetachedBlock]
  )

  const registerTextPart = useCallback(
    (partId: string, blockId?: string) => {
      if (blockId) {
        const parts = blockPartsRef.current.get(blockId) ?? []
        if (!parts.includes(partId)) {
          blockPartsRef.current.set(blockId, [...parts, partId])
          const completed = blockPartsCompletedRef.current.get(blockId) ?? new Set()
          blockPartsCompletedRef.current.set(blockId, completed)
          hydratePartFromSnapshot(partId, blockId)
          const blockCompleted = snapshotRef.current?.completedJobIds.includes(blockId)
          if (blockCompleted && areAllPartsComplete(
            blockPartsRef.current.get(blockId) ?? [],
            blockPartsCompletedRef.current.get(blockId) ?? new Set()
          )) {
            completedJobsRef.current.add(blockId)
          }
          bumpQueue()
        }
        return () => {
          const next = (blockPartsRef.current.get(blockId) ?? []).filter((id) => id !== partId)
          blockPartsRef.current.set(blockId, next)
          blockPartsCompletedRef.current.get(blockId)?.delete(partId)
          generationRef.current.delete(partId)
          bumpQueue()
        }
      }

      if (!jobsRef.current.some((j) => j.id === partId)) {
        jobsRef.current.push({ id: partId, kind: "text" })
        hydratePartFromSnapshot(partId)
        bumpQueue()
      }
      return () => {
        jobsRef.current = jobsRef.current.filter((j) => j.id !== partId)
        completedJobsRef.current.delete(partId)
        generationRef.current.delete(partId)
        bumpQueue()
      }
    },
    [bumpQueue, hydratePartFromSnapshot]
  )

  const isBlockRevealed = useCallback(
    (blockId: string) => {
      if (completedJobsRef.current.has(blockId)) return true
      const canonicalOrder = canonicalBlockIdsRef.current
      if (
        canonicalOrder.length > 0 &&
        !areAllPriorBlocksComplete(blockId, canonicalOrder, completedJobsRef.current)
      ) {
        return false
      }
      if (activeJobId !== blockId) return false
      const job = jobsRef.current.find((j) => j.id === blockId)
      return job?.kind === "block"
    },
    [activeJobId]
  )

  const isTextPartActive = useCallback(
    (partId: string, blockId?: string) => {
      if (blockId) {
        if (activeJobId !== blockId) return false
        const job = jobsRef.current.find((j) => j.id === blockId)
        if (job?.kind !== "block") return false
        const parts = blockPartsRef.current.get(blockId) ?? []
        const completed = blockPartsCompletedRef.current.get(blockId) ?? new Set()
        return getFirstIncompletePartId(parts, completed) === partId
      }
      if (activeJobId !== partId) return false
      const job = jobsRef.current.find((j) => j.id === partId)
      return job?.kind === "text"
    },
    [activeJobId]
  )

  const isTextPartComplete = useCallback((partId: string, blockId?: string) => {
    if (blockId) {
      return blockPartsCompletedRef.current.get(blockId)?.has(partId) ?? false
    }
    return completedJobsRef.current.has(partId)
  }, [])

  const markTextPartComplete = useCallback(
    (partId: string, blockId?: string) => {
      if (sessionId) {
        markAgentMessageBoardTextPartRead(sessionId, partId)
      }

      if (blockId) {
        const completed = blockPartsCompletedRef.current.get(blockId) ?? new Set()
        if (completed.has(partId)) return
        completed.add(partId)
        blockPartsCompletedRef.current.set(blockId, completed)
        tryCompleteBlock(blockId)
        syncPersistedSnapshot()
        bumpQueue()
        return
      }
      if (completedJobsRef.current.has(partId)) return
      completedJobsRef.current.add(partId)
      syncPersistedSnapshot()
      bumpQueue()
    },
    [bumpQueue, tryCompleteBlock, sessionId, syncPersistedSnapshot]
  )

  const reopenTextPart = useCallback(
    (partId: string, blockId?: string) => {
      if (blockId) {
        const parts = blockPartsRef.current.get(blockId) ?? []
        const idx = parts.indexOf(partId)
        if (idx === -1) return

        const completed = blockPartsCompletedRef.current.get(blockId) ?? new Set()
        completed.delete(partId)
        blockPartsCompletedRef.current.set(blockId, completed)
        generationRef.current.set(partId, (generationRef.current.get(partId) ?? 0) + 1)

        for (let i = idx + 1; i < parts.length; i++) {
          const laterPart = parts[i]
          completed.delete(laterPart)
          generationRef.current.set(laterPart, (generationRef.current.get(laterPart) ?? 0) + 1)
        }
        blockPartsCompletedRef.current.set(blockId, completed)

        completedJobsRef.current.delete(blockId)
        resetJobsAfter(blockId)
        // resetJobsAfter only mutates refs; bump so the queue re-picks an active
        // job and consumers (typing loop) re-render to replay the reopened part.
        bumpQueue()
        return
      }

      resetJobsAfter(partId)
      bumpQueue()
    },
    [resetJobsAfter, bumpQueue]
  )

  const getPartGeneration = useCallback((partId: string) => {
    return generationRef.current.get(partId) ?? 0
  }, [])

  const shouldSkipTyping = useCallback(
    (partId: string) => {
      if (!sessionId) return false
      return isAgentMessageBoardTextPartRead(sessionId, partId)
    },
    [sessionId]
  )

  const getPartResumeLength = useCallback(
    (partId: string) => {
      const inMemory = partVisibleLengthRef.current.get(partId) ?? 0
      if (inMemory > 0) return inMemory
      if (!sessionId) return 0
      return getAgentMessageBoardPartResumeLength(sessionId, partId)
    },
    [sessionId]
  )

  const extraPartIdsOnQuitRef = useRef(extraPartIdsOnQuit)
  extraPartIdsOnQuitRef.current = extraPartIdsOnQuit
  const pendingSessionQueueBumpRef = useRef(false)

  const applySnapshotToQueueRefs = useCallback((snapshot: SessionRenderSnapshot | null) => {
    if (!snapshot) return

    for (const jobId of snapshot.completedJobIds) {
      completedJobsRef.current.add(jobId)
    }

    for (const [blockId, partIds] of Object.entries(snapshot.blockPartsCompleted)) {
      if (!partIds.length) continue
      const completed = blockPartsCompletedRef.current.get(blockId) ?? new Set()
      for (const partId of partIds) {
        completed.add(partId)
      }
      blockPartsCompletedRef.current.set(blockId, completed)
    }

    for (const [partId, progress] of Object.entries(snapshot.partProgress)) {
      if (progress.visibleLength > 0) {
        partVisibleLengthRef.current.set(partId, progress.visibleLength)
      }
    }
  }, [])

  const resetQueueForSession = useCallback((nextSessionId: string | null) => {
    snapshotRef.current = nextSessionId
      ? loadAgentMessageBoardRenderSnapshot(nextSessionId)
      : null
    jobsRef.current = []
    completedJobsRef.current.clear()
    blockPartsRef.current.clear()
    blockPartsCompletedRef.current.clear()
    generationRef.current.clear()
    partVisibleLengthRef.current.clear()
    detachedBlocksRef.current.clear()
    applySnapshotToQueueRefs(snapshotRef.current)
    setActiveJobId(
      getFirstIncompleteJobId(jobsRef.current, completedJobsRef.current)
    )
    persistSnapshotRef.current = snapshotRef.current
      ? {
          completedJobIds: [...snapshotRef.current.completedJobIds],
          blockPartsCompleted: { ...snapshotRef.current.blockPartsCompleted },
          partProgress: { ...snapshotRef.current.partProgress },
        }
      : {
          completedJobIds: [],
          blockPartsCompleted: {},
          partProgress: {},
        }
  }, [applySnapshotToQueueRefs])

  // Reset the render queue during render (before child layout registration). A useEffect
  // reset runs after child useLayoutEffect and would wipe jobsRef after blocks register.
  if (prevSessionIdRef.current !== sessionId) {
    const prev = prevSessionIdRef.current
    if (flushTimerRef.current != null) {
      clearTimeout(flushTimerRef.current)
      flushTimerRef.current = null
    }
    if (prev !== undefined && prev !== sessionId && prev) {
      syncPersistedSnapshot()
      const outgoing =
        persistSnapshotRef.current ??
        buildRenderSnapshot(
          jobsRef.current,
          completedJobsRef.current,
          blockPartsRef.current,
          blockPartsCompletedRef.current,
          partVisibleLengthRef.current
        )
      if (snapshotHasProgress(outgoing)) {
        saveAgentMessageBoardRenderSnapshot(String(prev), outgoing)
      }
      setAgentMessageBoardRenderEffectsCompletedOnQuit(
        String(prev),
        hasQueueRenderEffectsCompleted(jobsRef.current, completedJobsRef.current)
      )
    }
    resetQueueForSession(sessionId)
    pendingSessionQueueBumpRef.current = true
    prevSessionIdRef.current = sessionId
  }

  useLayoutEffect(() => {
    if (!pendingSessionQueueBumpRef.current) return
    pendingSessionQueueBumpRef.current = false
    bumpQueue()
  }, [sessionId, bumpQueue])

  // Keep per-session render completion persisted as the queue advances so revisit
  // logic can immediately know whether all board effects finished.
  useEffect(() => {
    if (!sessionId) return
    const finished = hasQueueRenderEffectsCompleted(
      jobsRef.current,
      completedJobsRef.current
    )
    setAgentMessageBoardRenderEffectsCompletedOnQuit(
      String(sessionId),
      finished
    )
    if (renderFinishReportedRef.current !== finished) {
      renderFinishReportedRef.current = finished
      onRenderFinishChange?.(finished)
    }
  }, [sessionId, queueVersion, onRenderFinishChange])

  useLayoutEffect(() => {
    const order = canonicalBlockIdsRef.current
    if (order.length === 0) return
    const before = jobsRef.current.map((j) => j.id).join("\0")
    sortJobsByCanonicalOrder(jobsRef.current, order)
    const after = jobsRef.current.map((j) => j.id).join("\0")
    if (before !== after) bumpQueue()
  }, [boardBlockIds, bumpQueue])

  useLayoutEffect(() => {
    return () => {
      if (flushTimerRef.current != null) {
        clearTimeout(flushTimerRef.current)
        flushTimerRef.current = null
      }
      const sid = sessionIdRef.current
      if (!sid) return
      syncPersistedSnapshot()
      flushPersistedSnapshot()
      const snapshot = persistSnapshotRef.current
      if (!snapshot || !snapshotHasProgress(snapshot)) return
      setAgentMessageBoardRenderEffectsCompletedOnQuit(
        String(sid),
        hasQueueRenderEffectsCompleted(jobsRef.current, completedJobsRef.current)
      )
      markAgentMessageBoardSessionQuit(
        String(sid),
        snapshot,
        extraPartIdsOnQuitRef.current
      )
    }
  }, [syncPersistedSnapshot, flushPersistedSnapshot])

  const tryCompleteEmptyBlock = useCallback(
    (blockId: string) => {
      if (activeJobId !== blockId) return
      const active = jobsRef.current.find((j) => j.id === blockId)
      if (!active || active.kind !== "block") return
      const parts = blockPartsRef.current.get(blockId) ?? []
      if (parts.length > 0) return
      tryCompleteBlock(blockId)
    },
    [activeJobId, tryCompleteBlock]
  )

  const reportTextPartDisplay = useCallback(
    (partId: string, visibleLength: number) => {
      const prev = partVisibleLengthRef.current.get(partId) ?? 0
      if (visibleLength === 0 && prev > 0) return
      const next = Math.max(prev, visibleLength)
      if (prev === next) return
      partVisibleLengthRef.current.set(partId, next)
      syncPersistedSnapshot()
      scheduleSnapshotFlush()
      if ((prev === 0 && next > 0) || (prev > 0 && next === 0)) {
        bumpQueue()
      }
    },
    [bumpQueue, syncPersistedSnapshot, scheduleSnapshotFlush]
  )

  const getTextPartVisibleLength = useCallback((partId: string) => {
    return partVisibleLengthRef.current.get(partId) ?? 0
  }, [])

  const getBlockTextPartIds = useCallback((blockId: string) => {
    return blockPartsRef.current.get(blockId) ?? []
  }, [])

  const value = useMemo(
    () => ({
      sessionId,
      isStreaming,
      queueVersion,
      registerBlock,
      registerTextPart,
      isBlockRevealed,
      isTextPartActive,
      isTextPartComplete,
      shouldSkipTyping,
      markTextPartComplete,
      reopenTextPart,
      getPartGeneration,
      getPartResumeLength,
      tryCompleteEmptyBlock,
      reportTextPartDisplay,
      getTextPartVisibleLength,
      getBlockTextPartIds,
    }),
    [
      sessionId,
      isStreaming,
      queueVersion,
      registerBlock,
      registerTextPart,
      isBlockRevealed,
      isTextPartActive,
      isTextPartComplete,
      shouldSkipTyping,
      markTextPartComplete,
      reopenTextPart,
      getPartGeneration,
      getPartResumeLength,
      tryCompleteEmptyBlock,
      reportTextPartDisplay,
      getTextPartVisibleLength,
      getBlockTextPartIds,
    ]
  )

  return (
    <AgentMessageBoardTextContext.Provider value={value}>
      {children}
    </AgentMessageBoardTextContext.Provider>
  )
}
