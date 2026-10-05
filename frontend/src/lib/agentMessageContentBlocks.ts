import { textLength } from "@/lib/streamingTextUtils"

export type AgentMessageContentBlockType = "paragraph" | "list" | "table"

export type AgentMessageContentBlock = {
  type: AgentMessageContentBlockType
  markdown: string
  /** Code-point start offset in the source string (inclusive). */
  start: number
  /** Code-point end offset in the source string (exclusive). */
  end: number
}

type LineInfo = {
  text: string
  start: number
  end: number
}

function splitLinesWithOffsets(source: string): LineInfo[] {
  const chars = [...source]
  const lines: LineInfo[] = []
  let lineStart = 0

  for (let i = 0; i < chars.length; i++) {
    if (chars[i] !== "\n") continue
    lines.push({
      text: chars.slice(lineStart, i).join(""),
      start: lineStart,
      end: i + 1,
    })
    lineStart = i + 1
  }

  if (lineStart <= chars.length) {
    lines.push({
      text: chars.slice(lineStart).join(""),
      start: lineStart,
      end: chars.length,
    })
  }

  return lines
}

function sliceByCodePointsRange(source: string, start: number, end: number): string {
  if (start >= end) return ""
  return [...source].slice(start, end).join("")
}

function isBlankLine(line: string): boolean {
  return line.trim().length === 0
}

/** GFM list marker or Unicode bullet dot at line start (the model often uses •). */
function isListLine(line: string): boolean {
  return /^\s*(?:[-*+]\s+|\d+\.\s+|•\s*)/.test(line)
}

/** True when a paragraph contains multiple inline •-separated items. */
export function containsInlineBulletList(text: string): boolean {
  if (!text.includes("•")) return false
  const matches = text.match(/(?:^|\s)•\s+/g)
  return (matches?.length ?? 0) >= 2
}

/**
 * Converts inline "text • item • item" (and line-start • lists) into markdown lists
 * so react-markdown renders them with list-disc styling.
 */
export function normalizeAgentMessageMarkdown(markdown: string): string {
  if (!markdown.includes("•")) {
    return normalizeBulletDotLineMarkdown(markdown)
  }

  const lines = markdown.split("\n")
  const allBulletDotLines =
    lines.length > 0 && lines.every((l) => l.trim() === "" || /^\s*•\s*/.test(l))
  if (allBulletDotLines) {
    return normalizeBulletDotLineMarkdown(markdown)
  }

  if (containsInlineBulletList(markdown)) {
    return normalizeInlineBulletParagraph(markdown)
  }

  return normalizeBulletDotLineMarkdown(markdown)
}

function normalizeBulletDotLineMarkdown(markdown: string): string {
  return markdown
    .split("\n")
    .map((line) => ( /^\s*•\s*/.test(line) ? line.replace(/^\s*•\s*/, "- ") : line))
    .join("\n")
}

function normalizeInlineBulletParagraph(text: string): string {
  const parts = text.split(/(?:^|\s)•\s+/)
  if (parts.length < 2) return text

  const prefix = parts[0].trimEnd()
  let items = parts.slice(1).map((p) => p.trim()).filter(Boolean)

  if (items.length === 0) return text

  const lastIdx = items.length - 1
  const last = items[lastIdx]
  const suffixMatch = last.match(
    /^([\s\S]*?\.)\s+((?:Found|Would|Note|Please|Analysis|In|The|This|If|You)\b[\s\S]*)$/
  )
  if (suffixMatch) {
    items[lastIdx] = suffixMatch[1].trim()
    const suffix = suffixMatch[2].trim()
    const listMd = items.map((item) => `- ${item}`).join("\n")
    const chunks: string[] = []
    if (prefix) chunks.push(prefix)
    chunks.push(listMd)
    if (suffix) chunks.push(suffix)
    return chunks.join("\n\n")
  }

  const listMd = items.map((item) => `- ${item}`).join("\n")
  const chunks: string[] = []
  if (prefix) chunks.push(prefix)
  chunks.push(listMd)
  return chunks.join("\n\n")
}

function isTableRowCandidate(line: string): boolean {
  const t = line.trim()
  if (!t.includes("|")) return false
  return (t.match(/\|/g) || []).length >= 2
}

function isTableSeparatorRow(line: string): boolean {
  const t = line.trim()
  if (!t.includes("|") || !t.includes("-")) return false
  return /^\|?[\s:|-]+\|?$/.test(t) && /-/.test(t)
}

function isTableStart(lines: LineInfo[], idx: number): boolean {
  if (idx + 1 >= lines.length) return false
  return isTableRowCandidate(lines[idx].text) && isTableSeparatorRow(lines[idx + 1].text)
}

export function parseAgentMessageContentBlocks(source: string): AgentMessageContentBlock[] {
  const lines = splitLinesWithOffsets(source)
  const blocks: AgentMessageContentBlock[] = []

  let i = 0
  while (i < lines.length) {
    const line = lines[i]

    if (isBlankLine(line.text)) {
      i++
      continue
    }

    if (isTableStart(lines, i)) {
      const start = lines[i].start
      let end = lines[i + 1].end
      i += 2
      while (i < lines.length) {
        const t = lines[i].text
        if (isBlankLine(t) || !isTableRowCandidate(t)) break
        end = lines[i].end
        i++
      }
      blocks.push({
        type: "table",
        markdown: sliceByCodePointsRange(source, start, end),
        start,
        end,
      })
      continue
    }

    if (isListLine(line.text)) {
      const start = line.start
      let end = line.end
      i++
      while (i < lines.length) {
        const t = lines[i].text
        if (isBlankLine(t)) break
        if (isListLine(t) || /^\s+/.test(t) || /^\s*•\s*/.test(t)) {
          end = lines[i].end
          i++
          continue
        }
        break
      }
      blocks.push({
        type: "list",
        markdown: sliceByCodePointsRange(source, start, end),
        start,
        end,
      })
      continue
    }

    const start = line.start
    let end = line.end
    i++
    while (i < lines.length) {
      const t = lines[i].text
      if (isBlankLine(t)) break
      if (isTableStart(lines, i)) break
      if (isListLine(t)) break
      end = lines[i].end
      i++
    }
    const paragraphMarkdown = sliceByCodePointsRange(source, start, end)
    blocks.push({
      type: containsInlineBulletList(paragraphMarkdown) ? "list" : "paragraph",
      markdown: paragraphMarkdown,
      start,
      end,
    })
  }

  return blocks
}

export type SplitBlocksResult = {
  completeBlocks: AgentMessageContentBlock[]
  tail: string
}

export function splitBlocksForDisplayLength(
  blocks: AgentMessageContentBlock[],
  source: string,
  displayLength: number
): SplitBlocksResult {
  const safeLen = Math.max(0, Math.min(displayLength, textLength(source)))
  if (safeLen === 0) return { completeBlocks: [], tail: "" }

  const completeBlocks: AgentMessageContentBlock[] = []
  for (const b of blocks) {
    if (b.end <= safeLen) {
      completeBlocks.push(b)
      continue
    }
    break
  }

  const lastCompleteEnd =
    completeBlocks.length > 0 ? completeBlocks[completeBlocks.length - 1].end : 0
  const tail = sliceByCodePointsRange(source, lastCompleteEnd, safeLen)
  return { completeBlocks, tail }
}
