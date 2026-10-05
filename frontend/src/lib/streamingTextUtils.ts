/** Average human typing speed (~40 WPM, ~5 chars/word). */
export const NORMAL_TYPING_CHARS_PER_MINUTE = 200

export const DEFAULT_MS_PER_CHAR = Math.round(
  60_000 / NORMAL_TYPING_CHARS_PER_MINUTE
)

/** Backlog at or above this is treated as "a lot" — render quickly to drain the queue. */
export const BACKLOG_FAST_THRESHOLD = 80

/** Backlog below this with more content expected — render slowly while waiting for chunks. */
export const BACKLOG_SLOW_THRESHOLD = 24

/** Faster reveal when catching up or finishing (~6× normal). */
export const FAST_MS_PER_CHAR = Math.max(8, Math.round(DEFAULT_MS_PER_CHAR / 6))

/** Floor for proportional drain — very large queues can reveal many chars per frame. */
export const MIN_MS_PER_CHAR = 2

/** Backlog at which proportional drain reaches {@link FAST_MS_PER_CHAR}. */
export const BACKLOG_PROPORTIONAL_REF = BACKLOG_FAST_THRESHOLD

/** Slower reveal while waiting for the next streamed chunk (~1.5× normal). */
export const SLOW_MS_PER_CHAR = Math.round(DEFAULT_MS_PER_CHAR * 1.5)

/**
 * ms-per-char scales inversely with backlog: larger queue → faster render.
 * At {@link BACKLOG_PROPORTIONAL_REF} chars, speed matches {@link FAST_MS_PER_CHAR}.
 */
export function msPerCharForBacklogDrain(backlog: number): number {
  if (backlog <= BACKLOG_SLOW_THRESHOLD) {
    return FAST_MS_PER_CHAR
  }
  const proportional = Math.round(
    (FAST_MS_PER_CHAR * BACKLOG_PROPORTIONAL_REF) / backlog
  )
  return Math.max(MIN_MS_PER_CHAR, proportional)
}

export type AdaptiveTypingSpeedInput = {
  /** Characters waiting in the render queue (target minus displayed). */
  backlog: number
  /** True when more content is likely to arrive soon (e.g. the LLM still streaming). */
  expectMoreContent: boolean
}

/**
 * Adaptive ms-per-char for the message-board typing effect.
 *
 * 1. Large backlog → fast, proportional to queue size (bigger queue → quicker)
 * 2. Small backlog + more content expected → slow
 * 3. Small backlog + no more content → fast (fixed catch-up)
 */
export function computeAdaptiveMsPerChar({
  backlog,
  expectMoreContent,
}: AdaptiveTypingSpeedInput): number {
  if (backlog <= 0) return DEFAULT_MS_PER_CHAR

  if (expectMoreContent && backlog < BACKLOG_SLOW_THRESHOLD) {
    return SLOW_MS_PER_CHAR
  }

  if (backlog < BACKLOG_SLOW_THRESHOLD) {
    return FAST_MS_PER_CHAR
  }

  return msPerCharForBacklogDrain(backlog)
}

/** Code-point length for consistent backlog math. */
export function textLength(text: string): number {
  return [...text].length
}

/** Slice by code points so displayed/target stay aligned. */
export function sliceByCodePoints(text: string, endIndex: number): string {
  if (endIndex <= 0) return ""
  const chars = [...text]
  return chars.slice(0, endIndex).join("")
}

export function getBacklog(target: string, displayed: string): number {
  return Math.max(0, textLength(target) - textLength(displayed))
}

/** How many characters to reveal for elapsed time at a fixed typing pace. */
export function charsToRevealForElapsed(
  elapsedMs: number,
  msPerChar: number = DEFAULT_MS_PER_CHAR
): number {
  if (elapsedMs <= 0 || msPerChar <= 0) return 0
  return Math.floor(elapsedMs / msPerChar)
}

function isDisplayedPrefixOfTarget(target: string, displayed: string): boolean {
  if (!displayed) return true
  return sliceByCodePoints(target, textLength(displayed)) === displayed
}

/**
 * Returns the next displayed string after revealing `count` characters from target.
 */
export function nextRevealChunk(target: string, displayed: string, count: number): string {
  if (count <= 0 || !target) return displayed
  const displayedLen = textLength(displayed)
  const targetLen = textLength(target)
  if (displayedLen >= targetLen) return displayed

  const targetChars = [...target]
  const chunkLen = Math.min(count, targetLen - displayedLen)
  return targetChars.slice(0, displayedLen + chunkLen).join("")
}

/** Advance displayed toward target using elapsed time at human typing speed. */
export function advanceTypingDisplay(
  target: string,
  displayed: string,
  elapsedMs: number,
  msPerChar: number = DEFAULT_MS_PER_CHAR
): string {
  if (!isDisplayedPrefixOfTarget(target, displayed)) {
    displayed = ""
  }
  const backlog = getBacklog(target, displayed)
  if (backlog <= 0) return displayed

  const chars = Math.min(backlog, charsToRevealForElapsed(elapsedMs, msPerChar))
  if (chars <= 0) return displayed
  return nextRevealChunk(target, displayed, chars)
}

export type TextSegment = {
  id: string
  text: string
}

/** Flatten segments with newline separators for offset-based slicing. */
export function flattenSegments(segments: TextSegment[]): {
  target: string
  offsets: { id: string; start: number; end: number }[]
} {
  let target = ""
  const offsets: { id: string; start: number; end: number }[] = []
  for (const seg of segments) {
    if (target) target += "\n"
    const start = textLength(target)
    target += seg.text
    offsets.push({ id: seg.id, start, end: textLength(target) })
  }
  return { target, offsets }
}

/** Map partially displayed flat string back to per-segment visible text. */
export function sliceSegmentsByDisplayed(
  segments: TextSegment[],
  displayed: string
): Record<string, string> {
  const { offsets } = flattenSegments(segments)
  const displayedLen = textLength(displayed)
  const result: Record<string, string> = {}

  for (const seg of segments) {
    const off = offsets.find((o) => o.id === seg.id)
    if (!off) {
      result[seg.id] = ""
      continue
    }
    if (displayedLen <= off.start) {
      result[seg.id] = ""
    } else if (displayedLen >= off.end) {
      result[seg.id] = seg.text
    } else {
      const visibleCount = displayedLen - off.start
      result[seg.id] = sliceByCodePoints(seg.text, visibleCount)
    }
  }
  return result
}
