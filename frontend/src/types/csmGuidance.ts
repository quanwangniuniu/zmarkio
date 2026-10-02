// Guidance entries (CSM-S03-02): admin-configured hints shown to agents in the
// conversation workspace for the conversation's matched Experience Group.

export type GuidanceType =
  | 'handoff'
  | 'suggested_reply'
  | 'escalation_procedure'
  | 'process_note';

export const GUIDANCE_TYPE_OPTIONS: { value: GuidanceType; label: string }[] = [
  { value: 'handoff', label: 'Handoff' },
  { value: 'suggested_reply', label: 'Suggested Reply' },
  { value: 'escalation_procedure', label: 'Escalation Procedure' },
  { value: 'process_note', label: 'Process Note' },
];

export interface GuidanceExperienceGroupLink {
  id: number;
  name: string;
  display_order: number;
}

export interface GuidanceEntry {
  id: number;
  project: number;
  guidance_type: GuidanceType;
  guidance_type_display: string;
  trigger_description: string;
  recommended_response: string;
  experience_groups: GuidanceExperienceGroupLink[];
  created_at: string;
  updated_at: string;
}

export interface GuidanceEntryPayload {
  guidance_type: GuidanceType;
  trigger_description: string;
  recommended_response: string;
  experience_group_ids: number[];
}

export interface GuidanceCapabilities {
  can_manage: boolean;
}

/** Read-only entry shape returned for the agent workspace panel. */
export interface WorkspaceGuidanceEntry {
  id: number;
  guidance_type: GuidanceType;
  guidance_type_display: string;
  trigger_description: string;
  recommended_response: string;
  display_order: number;
}

export interface ConversationGuidance {
  experience_group: { id: number; name: string } | null;
  entries: WorkspaceGuidanceEntry[];
}
