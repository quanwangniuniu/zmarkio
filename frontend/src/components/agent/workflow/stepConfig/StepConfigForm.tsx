"use client"

import { useState } from "react"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import type { WorkflowStepType } from "@/types/agent"

interface StepConfigFormProps {
  stepType: WorkflowStepType
  config: Record<string, unknown>
  disabled?: boolean
  onChange: (config: Record<string, unknown>) => void
}

function headersToJsonString(headers: unknown): string {
  if (!headers || typeof headers !== "object" || Array.isArray(headers)) {
    return "{}"
  }
  try {
    return JSON.stringify(headers, null, 2)
  } catch {
    return "{}"
  }
}

function parseHeadersJson(raw: string): { value: Record<string, string>; error: string | null } {
  const trimmed = raw.trim()
  if (!trimmed) return { value: {}, error: null }
  try {
    const parsed = JSON.parse(trimmed)
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { value: {}, error: "Headers must be a JSON object." }
    }
    for (const [key, val] of Object.entries(parsed)) {
      if (typeof val !== "string") {
        return { value: {}, error: `Header "${key}" must be a string value.` }
      }
    }
    return { value: parsed as Record<string, string>, error: null }
  } catch {
    return { value: {}, error: "Invalid JSON for headers." }
  }
}

export function StepConfigForm({ stepType, config, disabled, onChange }: StepConfigFormProps) {
  const [headersError, setHeadersError] = useState<string | null>(null)

  if (stepType === "await_confirmation") {
    const message = typeof config.message === "string" ? config.message : ""
    return (
      <div className="space-y-2">
        <Label className="text-xs font-medium">Confirmation message</Label>
        <Textarea
          value={message}
          disabled={disabled}
          rows={3}
          placeholder="Message shown to the user before continuing..."
          onChange={(e) => onChange({ ...config, message: e.target.value })}
          className="resize-none text-sm"
        />
      </div>
    )
  }

  if (stepType === "custom_api") {
    const method = typeof config.method === "string" ? config.method : "POST"
    const url = typeof config.url === "string" ? config.url : ""
    const timeout = typeof config.timeout === "number" ? String(config.timeout) : "30"
    const bodyTemplate =
      typeof config.body_template === "string" ? config.body_template : ""
    const headersJson = headersToJsonString(config.headers)

    return (
      <div className="space-y-3">
        <div>
          <Label className="text-xs font-medium">HTTP method</Label>
          <Select
            value={method}
            disabled={disabled}
            onValueChange={(v) => onChange({ ...config, method: v })}
          >
            <SelectTrigger className="mt-1">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {["GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => (
                <SelectItem key={m} value={m}>
                  {m}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div>
          <Label className="text-xs font-medium">URL</Label>
          <Input
            value={url}
            disabled={disabled}
            onChange={(e) => onChange({ ...config, url: e.target.value })}
            placeholder="https://example.com/hooks/agent"
            className="mt-1 text-sm"
          />
        </div>
        <div>
          <Label className="text-xs font-medium">Headers (JSON object)</Label>
          <Textarea
            value={headersJson}
            disabled={disabled}
            rows={4}
            onChange={(e) => {
              const { value, error } = parseHeadersJson(e.target.value)
              setHeadersError(error)
              if (!error) {
                onChange({ ...config, headers: value })
              }
            }}
            className="mt-1 resize-none font-mono text-xs"
          />
          {headersError && (
            <p className="mt-1 text-[11px] text-destructive">{headersError}</p>
          )}
          <p className="mt-1 text-[11px] text-muted-foreground">
            Example: {`{"Content-Type": "application/json", "Authorization": "Bearer token"}`}
          </p>
        </div>
        <div>
          <Label className="text-xs font-medium">Body template</Label>
          <Textarea
            value={bodyTemplate}
            disabled={disabled}
            rows={3}
            placeholder='Use __input__ to pass step input as JSON body'
            onChange={(e) => onChange({ ...config, body_template: e.target.value })}
            className="mt-1 resize-none text-sm font-mono"
          />
        </div>
        <div>
          <Label className="text-xs font-medium">Timeout (seconds)</Label>
          <Input
            value={timeout}
            disabled={disabled}
            type="number"
            min={1}
            onChange={(e) =>
              onChange({ ...config, timeout: Number(e.target.value) || 30 })
            }
            className="mt-1 text-sm"
          />
        </div>
      </div>
    )
  }

  if (stepType === "call_llm") {
    return (
      <p className="text-xs text-muted-foreground">
        Uses the configured LLM (Ollama). Per-step prompt overrides are not yet
        supported — the executor runs the standard analysis flow.
      </p>
    )
  }

  if (stepType === "generate_miro_snapshot" || stepType === "create_miro_board") {
    return (
      <p className="text-xs text-muted-foreground">
        Requires Miro API configuration and prior analysis output. No additional step config
        is needed.
      </p>
    )
  }

  if (
    stepType === "analyze_data" ||
    stepType === "detect_columns" ||
    stepType === "normalize_data" ||
    stepType === "generate_criteria" ||
    stepType === "create_tasks"
  ) {
    return (
      <p className="text-xs text-muted-foreground">
        This step uses built-in backend logic. No additional configuration required.
      </p>
    )
  }

  return (
    <p className="text-xs text-muted-foreground">
      This step type has no editable configuration in the UI.
    </p>
  )
}

export function validateStepConfig(
  stepType: WorkflowStepType,
  config: Record<string, unknown>
): string | null {
  if (stepType === "await_confirmation") {
    const message = typeof config.message === "string" ? config.message.trim() : ""
    if (!message) return "Confirmation message is required."
  }
  if (stepType === "custom_api") {
    const url = typeof config.url === "string" ? config.url.trim() : ""
    if (!url) return "URL is required for Custom API steps."
    try {
      new URL(url)
    } catch {
      return "URL must be a valid absolute URL."
    }
    if (config.headers !== undefined) {
      const { error } = parseHeadersJson(JSON.stringify(config.headers))
      if (error) return error
    }
  }
  return null
}
