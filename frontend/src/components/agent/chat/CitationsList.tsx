"use client"

import { BookOpen } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import type { Citation } from "@/types/agent"

interface CitationsListProps {
  citations: Citation[]
  messageId: string
}

const SOURCE_TYPE_LABELS: Record<string, string> = {
  meeting: "Meeting",
  notion_draft: "Notion Draft",
  retrospective: "Retrospective",
}

function sourceTypeLabel(sourceType: string): string {
  return SOURCE_TYPE_LABELS[sourceType] ?? sourceType
}

/** citation_metadata varies by source_type and is untyped on the wire -- never
 * trust `title` is a string without checking. */
function citationTitle(citation: Citation): string {
  const title = citation.citation_metadata?.title
  if (typeof title === "string" && title.trim()) {
    return title
  }
  return `${sourceTypeLabel(citation.source_type)} #${citation.source_id}`
}

export function CitationsList({ citations, messageId }: CitationsListProps) {
  if (citations.length === 0) {
    return null
  }

  return (
    <div className="space-y-2 rounded-lg border border-border bg-muted/50 px-3 py-2">
      <div className="flex items-center gap-2 text-sm font-medium text-foreground">
        <BookOpen className="h-4 w-4 shrink-0" />
        <span>Sources ({citations.length})</span>
      </div>
      <ul className="space-y-2">
        {citations.map((citation) => (
          <li
            key={`${messageId}-citation-${citation.n}`}
            className="rounded-lg border border-border bg-background px-3 py-2 text-sm"
          >
            <div className="flex items-center gap-2 flex-wrap">
              <Badge variant="secondary" className="shrink-0">
                [{citation.n}] {sourceTypeLabel(citation.source_type)}
              </Badge>
              <span className="font-medium text-foreground">{citationTitle(citation)}</span>
              {typeof citation.similarity === "number" && (
                <span className="text-xs text-muted-foreground">
                  similarity {Math.round(citation.similarity * 100)}%
                </span>
              )}
            </div>
            {citation.snippet && (
              <p className="text-xs text-muted-foreground mt-1 line-clamp-3">
                {citation.snippet}
              </p>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
