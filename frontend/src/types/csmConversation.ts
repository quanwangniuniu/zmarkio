export type ConversationStatus = 'active' | 'pending' | 'resolved' | 'closed';
export type ConversationChannel = 'web' | 'email' | 'whatsapp';
export type MessageSenderType = 'agent' | 'customer' | 'system';

export interface ConversationMessage {
  id: number;
  conversation: number;
  sender_type: MessageSenderType;
  sender_agent: number | null;
  sender_agent_name: string | null;
  sender_agent_email: string | null;
  content: string;
  rich_body: object | null;
  image_url: string | null;
  created_at: string;
}

export interface CustomerProfile {
  id: number;
  full_name: string;
  email: string;
  company: string;
  phone: string;
  project_id: number | null;
  organisation_id: number | null;
  organisation_name: string | null;
  region_name: string | null;
  status_label: number | null;
  status_label_name: string | null;
  status_label_color: string | null;
}

export interface LinkedTicket {
  id: number;
  title: string;
  status: string;
  priority: string;
}

export interface Ticket {
  id: number;
  queue: number | null;
  queue_name: string | null;
  title: string;
  description: string;
  status: string;
  status_display: string;
  // Current status's configured color (from the status machine, so custom
  // statuses resolve correctly), null if no machine row.
  status_color: string | null;
  // Valid next statuses for this ticket per the configured state machine.
  available_next_statuses: { slug: string; name: string; color: string }[];
  priority: string;
  priority_display: string;
  assigned_to: number | null;
  assigned_to_name: string | null;
  customer_email: string;
  conversation: number | null;
  created_at: string;
  first_response_due: string | null;
  resolution_due: string | null;
  sla: {
    first_response_due: string | null;
    resolution_due: string | null;
    first_response_breached: boolean;
    resolution_breached: boolean;
    first_response_met: boolean;
    clock_running: boolean;
    first_response_remaining_seconds: number | null;
    resolution_remaining_seconds: number | null;
    first_response_state: SlaState | null;
    resolution_state: SlaState | null;
  };
}

export type SlaState = 'ok' | 'amber' | 'breached';

export interface TicketSummary {
  id: number;
  title: string;
  status: string;
  status_display: string;
  priority: string;
  priority_display: string;
  assigned_to_name: string | null;
}

export interface Conversation {
  id: number;
  customer: number | null;
  customer_name: string | null;
  customer_email: string | null;
  queue: number | null;
  queue_name: string | null;
  queue_organisation_id: number | null;
  assigned_to: number | null;
  assigned_to_name: string | null;
  status: ConversationStatus;
  status_display: string;
  channel: ConversationChannel;
  channel_display: string;
  tags: string[];
  started_at: string;
  ended_at: string | null;
  elapsed_seconds: number;
  ticket: TicketSummary | null;
  created_at: string;
}

export interface ConversationDetail extends Conversation {
  messages: ConversationMessage[];
  customer_profile: CustomerProfile | null;
  linked_tickets: LinkedTicket[];
}

export interface SendMessagePayload {
  content: string;
  rich_body?: object | null;
}

export interface CreateTicketPayload {
  title: string;
  description: string;
  priority: 'critical' | 'high' | 'medium' | 'low';
  queue: number;
}

export interface UpdateConversationPayload {
  status?: ConversationStatus;
  queue?: number | null;
  assigned_to?: number | null;
  tags?: string[];
}

export interface AssignableAgent {
  id: number;
  name: string;
  email: string;
}

export interface QuickReplyTemplate {
  id: number;
  slug: string;
  organisation: number;
  team: number | null;
  title: string;
  content: string;
  rich_body: object | null;
  tags: string[];
  is_active: boolean;
  created_by: number | null;
  created_by_name: string | null;
  created_at: string;
  updated_at: string;
}

export interface QuickReplyTemplatePayload {
  organisation: number;
  team?: number | null;
  title: string;
  content: string;
  rich_body?: object | null;
  tags?: string[];
  is_active?: boolean;
}

export interface QuickReplyTemplateHistory {
  id: number;
  edited_by: number | null;
  edited_by_name: string | null;
  edited_at: string;
  title: string;
  content: string;
  rich_body: object | null;
  tags: string[];
}

export interface TemplateTag {
  id: number;
  organisation: number;
  name: string;
  created_at: string;
}

// WebSocket event types
export type CsmWsEvent =
  | { type: 'new_message'; message: ConversationMessage }
  | { type: 'new_conversation'; conversation: Conversation }
  | { type: 'conversation_updated'; conversation: Conversation }
  | { type: 'typing_indicator'; conversation_id: number; user_id: number; is_typing: boolean }
  | { type: 'joined'; conversation_id: number }
  | { type: 'guidance_updated'; experience_group_ids: number[] }
  | { type: 'error'; detail: string };
