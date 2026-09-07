import { FileSpreadsheet, Loader2, TableProperties } from "lucide-react"

export type AgentWorkflowBannerWorkflow =
  | "spreadsheet_analysis"
  | "pattern_generation"
  | "pivot_table_generation"

type AgentWorkflowBannerProps = {
  workflow: AgentWorkflowBannerWorkflow
  spreadsheetName?: string | null
  sheetName?: string | null
  /** Show a spinner while the agent is actively processing this workflow. */
  busy?: boolean
}

const WORKFLOW_LABEL: Record<AgentWorkflowBannerWorkflow, string> = {
  spreadsheet_analysis: "Analyzing",
  pattern_generation: "Pattern generation",
  pivot_table_generation: "Pivot table",
}

const WORKFLOW_ICON: Record<
  AgentWorkflowBannerWorkflow,
  typeof FileSpreadsheet
> = {
  spreadsheet_analysis: FileSpreadsheet,
  pattern_generation: TableProperties,
  pivot_table_generation: TableProperties,
}

/** Build the "· spreadsheet · sheet" target suffix, tolerating a missing name. */
function formatTarget(
  spreadsheetName?: string | null,
  sheetName?: string | null
): string {
  const parts = [spreadsheetName, sheetName].filter(
    (part): part is string => Boolean(part && part.trim())
  )
  return parts.join(" · ")
}

/**
 * Full-width banner shown in the AI Agent chat board while the chat is working on
 * a spreadsheet-oriented workflow, naming the spreadsheet + sheet being operated
 * on. Rendered by AgentChatPage above the message list.
 */
export function AgentWorkflowBanner({
  workflow,
  spreadsheetName,
  sheetName,
  busy = false,
}: AgentWorkflowBannerProps) {
  const Icon = WORKFLOW_ICON[workflow]
  const target = formatTarget(spreadsheetName, sheetName)

  return (
    <div className="flex shrink-0 items-center gap-2 border-b border-[#3CCED7]/20 bg-[#3CCED7]/5 px-3 py-1.5">
      {busy ? (
        <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-[#0E8A96]" />
      ) : (
        <Icon className="h-3.5 w-3.5 shrink-0 text-[#0E8A96]" />
      )}
      <span className="min-w-0 truncate text-xs text-[#0E8A96]">
        <span className="font-semibold">{WORKFLOW_LABEL[workflow]}</span>
        {target && <span className="text-[#3CCED7]"> · {target}</span>}
      </span>
    </div>
  )
}
