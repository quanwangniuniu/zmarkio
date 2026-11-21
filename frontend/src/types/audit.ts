/**
 * Meeting Audit Log Types and Constants
 *
 * Provides comprehensive TypeScript types and constants for the Meeting Auditability feature.
 * Matches the backend MeetingAuditLog model and serializer structure.
 */

/**
 * Union type of all 19 possible audit event types.
 * These events track mutations to meetings and their direct children (participants, agenda, etc).
 */
export type AuditEventType =
  | 'meeting.status_changed'
  | 'meeting.title_changed'
  | 'meeting.objective_changed'
  | 'meeting.summary_changed'
  | 'meeting.type_changed'
  | 'meeting.datetime_changed'
  | 'meeting.participant_added'
  | 'meeting.participant_removed'
  | 'meeting.agenda_item_added'
  | 'meeting.agenda_item_edited'
  | 'meeting.agenda_item_deleted'
  | 'meeting.document_edited'
  | 'meeting.action_item_added'
  | 'meeting.action_item_edited'
  | 'meeting.action_item_resolved'
  | 'meeting.decision_created'
  | 'meeting.decision_updated'
  | 'meeting.decision_deleted'
  | 'meeting.task_created'
  | 'meeting.task_updated'
  | 'meeting.task_deleted'
  | 'meeting.template_applied'
  | 'meeting.tags_changed';

/**
 * Actor information embedded in audit log entries.
 * Null when the event is system-initiated.
 */
export interface AuditActor {
  id: number;
  display_name: string;
  email: string;
}

/**
 * Single immutable audit log entry.
 * Represents a mutation to a meeting or its direct children.
 */
export interface AuditLogEntry {
  id: string;
  event_type: AuditEventType;
  timestamp: string; // ISO 8601 datetime
  actor: AuditActor | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  context: Record<string, unknown> | null;
}

/**
 * Filter parameters for audit log queries.
 * All fields are optional and combined with AND logic.
 */
export interface AuditLogFilters {
  event_type?: AuditEventType[];
  actor_id?: number;
  from?: string; // ISO 8601 datetime
  to?: string; // ISO 8601 datetime
}

/**
 * Paginated API response structure for audit log list endpoint.
 * Matches Django REST Framework pagination format.
 */
export interface AuditLogResponse {
  count: number;
  next: string | null;
  previous: string | null;
  results: AuditLogEntry[];
}

/**
 * Metadata for displaying an audit event in the UI.
 * Includes human-readable label, category, and visual properties.
 */
export interface EventMetadata {
  category: string;
  label: string;
  description?: string;
  icon?: string;
}

/**
 * Category metadata for grouping related event types.
 * Used for filtering and visual organization in the audit log UI.
 */
export interface CategoryMetadata {
  label: string;
  icon: string;
  color: string;
}

/**
 * All 19 audit event type constants.
 * Use these instead of string literals to avoid typos.
 */
export const AUDIT_EVENT_TYPES = {
  STATUS_CHANGED: 'meeting.status_changed' as const,
  TITLE_CHANGED: 'meeting.title_changed' as const,
  OBJECTIVE_CHANGED: 'meeting.objective_changed' as const,
  SUMMARY_CHANGED: 'meeting.summary_changed' as const,
  TYPE_CHANGED: 'meeting.type_changed' as const,
  DATETIME_CHANGED: 'meeting.datetime_changed' as const,
  PARTICIPANT_ADDED: 'meeting.participant_added' as const,
  PARTICIPANT_REMOVED: 'meeting.participant_removed' as const,
  AGENDA_ITEM_ADDED: 'meeting.agenda_item_added' as const,
  AGENDA_ITEM_EDITED: 'meeting.agenda_item_edited' as const,
  AGENDA_ITEM_DELETED: 'meeting.agenda_item_deleted' as const,
  DOCUMENT_EDITED: 'meeting.document_edited' as const,
  ACTION_ITEM_ADDED: 'meeting.action_item_added' as const,
  ACTION_ITEM_EDITED: 'meeting.action_item_edited' as const,
  ACTION_ITEM_RESOLVED: 'meeting.action_item_resolved' as const,
  DECISION_CREATED: 'meeting.decision_created' as const,
  DECISION_UPDATED: 'meeting.decision_updated' as const,
  DECISION_DELETED: 'meeting.decision_deleted' as const,
  TASK_CREATED: 'meeting.task_created' as const,
  TASK_UPDATED: 'meeting.task_updated' as const,
  TASK_DELETED: 'meeting.task_deleted' as const,
  TEMPLATE_APPLIED: 'meeting.template_applied' as const,
  TAGS_CHANGED: 'meeting.tags_changed' as const,
} as const;

/**
 * Array of all 19 event type values.
 * Useful for iteration and type validation.
 */
export const AUDIT_EVENTS: AuditEventType[] = [
  AUDIT_EVENT_TYPES.STATUS_CHANGED,
  AUDIT_EVENT_TYPES.TITLE_CHANGED,
  AUDIT_EVENT_TYPES.OBJECTIVE_CHANGED,
  AUDIT_EVENT_TYPES.SUMMARY_CHANGED,
  AUDIT_EVENT_TYPES.TYPE_CHANGED,
  AUDIT_EVENT_TYPES.DATETIME_CHANGED,
  AUDIT_EVENT_TYPES.PARTICIPANT_ADDED,
  AUDIT_EVENT_TYPES.PARTICIPANT_REMOVED,
  AUDIT_EVENT_TYPES.AGENDA_ITEM_ADDED,
  AUDIT_EVENT_TYPES.AGENDA_ITEM_EDITED,
  AUDIT_EVENT_TYPES.AGENDA_ITEM_DELETED,
  AUDIT_EVENT_TYPES.DOCUMENT_EDITED,
  AUDIT_EVENT_TYPES.ACTION_ITEM_ADDED,
  AUDIT_EVENT_TYPES.ACTION_ITEM_EDITED,
  AUDIT_EVENT_TYPES.ACTION_ITEM_RESOLVED,
  AUDIT_EVENT_TYPES.DECISION_CREATED,
  AUDIT_EVENT_TYPES.DECISION_UPDATED,
  AUDIT_EVENT_TYPES.DECISION_DELETED,
  AUDIT_EVENT_TYPES.TASK_CREATED,
  AUDIT_EVENT_TYPES.TASK_UPDATED,
  AUDIT_EVENT_TYPES.TASK_DELETED,
  AUDIT_EVENT_TYPES.TEMPLATE_APPLIED,
  AUDIT_EVENT_TYPES.TAGS_CHANGED,
];

/**
 * Event categories for organizing audit events.
 * Used for filtering, grouping, and visual styling in the audit log UI.
 */
export const AUDIT_EVENT_CATEGORIES: Record<string, CategoryMetadata> = {
  lifecycle: {
    label: 'Meeting Lifecycle',
    icon: 'calendar-clock',
    color: 'blue',
  },
  metadata: {
    label: 'Meeting Metadata',
    icon: 'pencil',
    color: 'slate',
  },
  participant: {
    label: 'Participants',
    icon: 'users',
    color: 'purple',
  },
  agenda: {
    label: 'Agenda',
    icon: 'list',
    color: 'green',
  },
  document: {
    label: 'Document',
    icon: 'file-text',
    color: 'amber',
  },
  action_item: {
    label: 'Action Items',
    icon: 'checkmark',
    color: 'orange',
  },
  decision: {
    label: 'Decisions',
    icon: 'lightbulb',
    color: 'yellow',
  },
  task: {
    label: 'Tasks',
    icon: 'task',
    color: 'cyan',
  },
  template: {
    label: 'Templates',
    icon: 'layout',
    color: 'indigo',
  },
  tags: {
    label: 'Tags',
    icon: 'tag',
    color: 'pink',
  },
};

/**
 * Metadata for each of the 19 audit event types.
 * Maps event type to category, display label, description, and optional icon override.
 */
export const AUDIT_EVENT_METADATA: Record<AuditEventType, EventMetadata> = {
  'meeting.status_changed': {
    category: 'lifecycle',
    label: 'Status changed',
    description: 'Meeting status transitioned to a new state',
  },
  'meeting.title_changed': {
    category: 'metadata',
    label: 'Title changed',
    description: 'Meeting title was updated',
  },
  'meeting.objective_changed': {
    category: 'metadata',
    label: 'Objective changed',
    description: 'Meeting objective was updated',
  },
  'meeting.summary_changed': {
    category: 'metadata',
    label: 'Summary changed',
    description: 'Meeting summary was updated',
  },
  'meeting.type_changed': {
    category: 'metadata',
    label: 'Type changed',
    description: 'Meeting type was reassigned',
  },
  'meeting.datetime_changed': {
    category: 'metadata',
    label: 'Date/time changed',
    description: 'Meeting date or time was updated',
  },
  'meeting.participant_added': {
    category: 'participant',
    label: 'Participant added',
    description: 'User was added as a participant',
  },
  'meeting.participant_removed': {
    category: 'participant',
    label: 'Participant removed',
    description: 'User was removed from participants',
  },
  'meeting.agenda_item_added': {
    category: 'agenda',
    label: 'Agenda item added',
    description: 'New agenda item was created',
  },
  'meeting.agenda_item_edited': {
    category: 'agenda',
    label: 'Agenda item edited',
    description: 'Agenda item content or order was changed',
  },
  'meeting.agenda_item_deleted': {
    category: 'agenda',
    label: 'Agenda item deleted',
    description: 'Agenda item was removed',
  },
  'meeting.document_edited': {
    category: 'document',
    label: 'Document edited',
    description: 'Meeting document content was updated',
  },
  'meeting.action_item_added': {
    category: 'action_item',
    label: 'Action item added',
    description: 'New action item was created',
  },
  'meeting.action_item_edited': {
    category: 'action_item',
    label: 'Action item edited',
    description: 'Action item title or status was changed',
  },
  'meeting.action_item_resolved': {
    category: 'action_item',
    label: 'Action item resolved',
    description: 'Action item was marked as resolved',
  },
  'meeting.decision_created': {
    category: 'decision',
    label: 'Decision created',
    description: 'Decision was linked to the meeting',
  },
  'meeting.decision_updated': {
    category: 'decision',
    label: 'Decision updated',
    description: 'Decision linked to this meeting was modified',
  },
  'meeting.decision_deleted': {
    category: 'decision',
    label: 'Decision deleted',
    description: 'Decision linked to this meeting was deleted',
  },
  'meeting.task_created': {
    category: 'task',
    label: 'Task created',
    description: 'Task was created from this meeting',
  },
  'meeting.task_updated': {
    category: 'task',
    label: 'Task updated',
    description: 'Task linked to this meeting was modified',
  },
  'meeting.task_deleted': {
    category: 'task',
    label: 'Task deleted',
    description: 'Task linked to this meeting was deleted',
  },
  'meeting.template_applied': {
    category: 'template',
    label: 'Template applied',
    description: 'Meeting template was applied',
  },
  'meeting.tags_changed': {
    category: 'tags',
    label: 'Tags changed',
    description: 'Meeting tags were added or removed',
  },
};

/**
 * Pagination constants for audit log listing.
 */
export const AUDIT_LOG_PAGINATION = {
  DEFAULT_PAGE_SIZE: 50,
  MAX_PAGE_SIZE: 200,
} as const;

/**
 * Date format constants for audit log filtering and display.
 */
export const AUDIT_LOG_DATE_FORMATS = {
  ISO_8601: 'YYYY-MM-DDTHH:mm:ss[Z]', // ISO 8601 format
  DISPLAY_DATE: 'MMM D, YYYY', // e.g., "May 24, 2026"
  DISPLAY_TIME: 'h:mm A', // e.g., "2:30 PM"
  DISPLAY_DATETIME: 'MMM D, YYYY h:mm A', // e.g., "May 24, 2026 2:30 PM"
} as const;

/**
 * Helper function to get all event types for a specific category.
 */
export function getEventTypesForCategory(category: string): AuditEventType[] {
  return AUDIT_EVENTS.filter(
    (eventType) => AUDIT_EVENT_METADATA[eventType].category === category
  );
}

/**
 * Helper function to validate if a string is a valid AuditEventType.
 */
export function isValidAuditEventType(value: unknown): value is AuditEventType {
  return typeof value === 'string' && AUDIT_EVENTS.includes(value as AuditEventType);
}

/**
 * Admin action logs
 */
export type AdminAuditAction =
  | 'role.created'
  | 'role.updated'
  | 'role.deleted'
  | 'role.permissions_updated'
  | 'role.permissions_copied'
  | 'user_role.assigned'
  | 'user_role.removed'
  | 'project.updated'
  | 'project.deleted'
  | 'project.labels_updated'
  | 'project.config_updated'
  | 'org.slug_updated'
  | 'org.deleted'
  | 'org.admin_assigned'
  | 'org.admin_removed';

export interface AdminAuditEvent {
  id: string;
  organization_id: number | null;
  project_id: number | null;
  actor_id: number | null;
  actor_email: string | null;
  actor_name: string | null;
  action: AdminAuditAction;
  target_type: string;
  target_id: string;
  target_name: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  timestamp: string; // ISO 8601
}

export interface PaginatedAdminAuditEvents {
  count: number;
  next: string | null;
  previous: string | null;
  results: AdminAuditEvent[];
}

export interface AdminAuditEventFilters {
  action?: AdminAuditAction;
  target_type?: string;
  project_id?: number | string;
}

