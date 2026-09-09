export interface Customer {
  id: number;
  /**
   * The account behind this customer, when they have one. Null means they
   * exist only as a contact record and can be reached by email alone.
   */
  user_id: number | null;
  email: string;
  full_name: string;
  company: string;
  phone: string;
  experience_group: number | null;
  experience_group_name: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  region: number | null;
  organisation: number | null;
  status_label: number | null;
  status_label_name: string | null;
  status_label_color: string | null;
}

export interface CreateCustomerData {
  email: string;
  full_name: string;
  company?: string;
  phone?: string;
  experience_group?: number | null;
  region?: number | null;
  organisation?: number | null;
  status_label?: number | null;
}

export interface UpdateCustomerData {
  email?: string;
  full_name?: string;
  company?: string;
  phone?: string;
  experience_group?: number | null;
  is_active?: boolean;
  region?: number | null;
  organisation?: number | null;
  status_label?: number | null;
}

// ── Customer Status Labels (MED-217 Part A) ───────────────────────────────────

export interface CustomerStatusLabel {
  id: number;
  project: number;
  name: string;
  color: string;
  order: number;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface CreateStatusLabelData {
  name: string;
  color: string;
}

// ── Internal Notes (MED-217 Part B) ───────────────────────────────────────────

export type NoteAuditEvent = 'note.created' | 'note.edited' | 'note.deleted';

export interface CustomerInternalNote {
  id: number;
  customer: number;
  author: number;
  author_email: string;
  author_name: string;
  author_avatar: string | null;
  body: import('@/types/comment').TiptapJSONContent;
  body_text: string;
  body_format: 'rich_text_json';
  is_edited: boolean;
  is_author: boolean;
  created_at: string;
  updated_at: string;
}

export interface CustomerInternalNoteAuditLog {
  id: string; // UUID
  customer: number;
  actor: number | null;
  actor_email: string | null;
  event_type: NoteAuditEvent;
  event_type_display: string;
  timestamp: string;
  note_id: number | null;
  note_body: string | null;
}

export interface UpdateStatusLabelData {
  name?: string;
  color?: string;
  order?: number;
}
