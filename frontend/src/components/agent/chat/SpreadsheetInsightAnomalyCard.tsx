"use client"

import { useState } from "react"
import { AlertTriangle, TrendingDown, Info, ChevronDown, ChevronRight, MapPin } from "lucide-react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { cn } from "@/lib/utils"
import type { AnomalyItem, AnomalySeverity } from "@/types/agent"
import { SPREADSHEET_HIGHLIGHT_LOCATIONS_EVENT } from "@/lib/agentLaunchContext"

const severityConfig = {
  critical: { icon: AlertTriangle, color: "text-red-400", bg: "bg-red-500/20", label: "Critical" },
  warning: { icon: TrendingDown, color: "text-orange-400", bg: "bg-orange-500/20", label: "Warning" },
  info: { icon: Info, color: "text-green-400", bg: "bg-green-500/20", label: "Info" },
} as const

function getSeverity(anomaly: AnomalyItem): AnomalySeverity {
  if (anomaly.severity) return anomaly.severity
  return "info"
}

function formatLocations(anomaly: AnomalyItem): string {
  const locations = anomaly.locations ?? []
  if (locations.length === 0) return ""
  return locations
    .map((loc) => loc.a1 ?? `R${loc.row + 1}C${loc.col + 1}`)
    .join(", ")
}

interface SpreadsheetInsightAnomalyCardProps {
  anomalies: AnomalyItem[]
  spreadsheetId?: number
  sheetId?: number
}

export function SpreadsheetInsightAnomalyCard({
  anomalies,
  spreadsheetId,
  sheetId,
}: SpreadsheetInsightAnomalyCardProps) {
  const [expanded, setExpanded] = useState(false)

  if (!anomalies.length) return null

  const handleAnomalyClick = (anomaly: AnomalyItem) => {
    if (typeof window === "undefined") return
    const locations = anomaly.locations ?? []
    if (!locations.length || spreadsheetId == null || sheetId == null) return
    window.dispatchEvent(
      new CustomEvent(SPREADSHEET_HIGHLIGHT_LOCATIONS_EVENT, {
        detail: {
          spreadsheetId,
          sheetId,
          locations,
        },
      })
    )
  }

  return (
    <Card className="bg-card border-border">
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        className="flex w-full items-center gap-2 px-4 py-3 text-left"
        aria-expanded={expanded}
      >
        {expanded ? (
          <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
        )}
        <span className="text-sm font-semibold text-foreground">
          Anomalies ({anomalies.length})
        </span>
      </button>

      {expanded && (
        <CardContent className="flex flex-col gap-2 px-4 pb-4 pt-0">
          {anomalies.map((anomaly, index) => {
            const severity = getSeverity(anomaly)
            const config = severityConfig[severity]
            const Icon = config.icon
            const title = anomaly.title || anomaly.metric || "Anomaly"
            const locationLabel = formatLocations(anomaly)
            const hasLocations = Boolean(anomaly.locations?.length)

            return (
              <Card
                key={anomaly.id}
                className={cn(
                  "bg-muted/30 border-border transition-colors",
                  hasLocations && "cursor-pointer hover:bg-muted/50"
                )}
                onClick={() => hasLocations && handleAnomalyClick(anomaly)}
                onKeyDown={(e) => {
                  if (hasLocations && (e.key === "Enter" || e.key === " ")) {
                    e.preventDefault()
                    handleAnomalyClick(anomaly)
                  }
                }}
                role={hasLocations ? "button" : undefined}
                tabIndex={hasLocations ? 0 : undefined}
              >
                <CardHeader className="pb-2 pt-3 px-4">
                  <div className="flex items-center gap-3">
                    <div className={cn("flex h-8 w-8 items-center justify-center rounded-lg shrink-0", config.bg)}>
                      <Icon className={cn("h-4 w-4", config.color)} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <CardTitle className="text-sm font-semibold text-card-foreground truncate">
                        {title}
                      </CardTitle>
                    </div>
                    <span
                      className={cn(
                        "text-xs font-medium px-2 py-0.5 rounded-full shrink-0",
                        config.bg,
                        config.color
                      )}
                    >
                      {config.label}
                    </span>
                  </div>
                </CardHeader>
                <CardContent className="px-4 pb-3 pt-0 space-y-1">
                  <p className="text-sm text-muted-foreground">{anomaly.description}</p>
                  {locationLabel ? (
                    <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                      <MapPin className="h-3 w-3 shrink-0" />
                      <span>{locationLabel}</span>
                    </div>
                  ) : null}
                </CardContent>
              </Card>
            )
          })}
        </CardContent>
      )}
    </Card>
  )
}
