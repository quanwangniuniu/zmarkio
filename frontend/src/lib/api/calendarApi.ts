import axios from 'axios';
import api from "../api";

export type CalendarViewType = "day" | "week" | "month" | "year" | "agenda";

export interface UserSummaryDTO {
  id: number;
  email: string;
  username: string;
  full_name: string;
}

export interface CalendarDTO {
  id: string;
  organization_id: string;
  project_id?: number | null;
  owner: UserSummaryDTO;
  name: string;
  description?: string | null;
  color: string;
  visibility: string;
  timezone: string;
  is_primary: boolean;
  location?: string | null;
}

export type RecurrenceFrequency = "DAILY" | "WEEKLY";

export interface RecurrenceRuleDTO {
  id?: string;
  frequency: RecurrenceFrequency;
  interval: number;
  count?: number | null;
  until?: string | null;
}

export interface RecurrenceInput {
  frequency: RecurrenceFrequency;
  interval: number;
  count?: number | null;
  until?: string | null;
}

export interface EventMetadata {
  source?: string | null;
  booking_link_slug?: string | null;
  booking_role?: string | null;
  [key: string]: unknown;
}

export interface EventDTO {
  id: string;
  calendar_id?: string;
  title: string;
  description?: string;
  start_datetime: string;
  end_datetime: string;
  timezone?: string;
  is_all_day: boolean;
  is_recurring: boolean;
  recurrence_rule?: RecurrenceRuleDTO | null;
  // Start of the specific occurrence (set on expanded recurring instances).
  // Required to target a single occurrence for "this only" / "this and future".
  original_start?: string | null;
  color?: string;
  etag?: string;
  metadata?: EventMetadata | null;
}

export type EventWritePayload = Partial<EventDTO> & {
  is_recurring?: boolean;
  recurrence?: RecurrenceInput | null;
};

// Scope of a recurring-event edit.
export type RecurringEditScope = "this" | "future" | "all";

export interface CalendarViewResponse {
  view_type: CalendarViewType;
  start_date: string;
  end_date: string;
  events: EventDTO[];
  calendars: CalendarDTO[];
}

export interface CalendarSubscriptionDTO {
  id: string;
  calendar: CalendarDTO | null;
  source_url: string | null;
  color_override: string | null;
  is_hidden: boolean;
}

export interface CreateCalendarPayload {
  name: string;
  color?: string;
  visibility?: string;
  timezone?: string;
  is_primary?: boolean;
  description?: string;
}

const withProjectScope = (projectId?: number | string | null) =>
  projectId != null ? { params: { project_id: projectId } } : {};

export const CalendarAPI = {
  listCalendars: (projectId?: number | string | null) =>
    api.get<CalendarDTO[]>("/api/calendars/", withProjectScope(projectId)),

  createCalendar: (payload: CreateCalendarPayload) =>
    api.post<CalendarDTO>("/api/calendars/", payload),

  listSubscriptions: () =>
    api.get<CalendarSubscriptionDTO[]>("/api/subscriptions/"),

  updateSubscription: (
    subscriptionId: string,
    data: Partial<Pick<CalendarSubscriptionDTO, "color_override" | "is_hidden">>,
  ) =>
    api.patch<CalendarSubscriptionDTO>(
      `/api/subscriptions/${subscriptionId}/`,
      data,
    ),

  getDayView: (params: {
    date: string;
    calendar_ids?: string[];
    project_id?: number | string | null;
  }) =>
    api.get<CalendarViewResponse>("/api/views/day/", {
      params: {
        date: params.date,
        calendar_ids: params.calendar_ids?.join(","),
        ...(params.project_id != null ? { project_id: params.project_id } : {}),
      },
    }),

  getWeekView: (params: {
    start_date: string;
    calendar_ids?: string[];
    project_id?: number | string | null;
  }) =>
    api.get<CalendarViewResponse>("/api/views/week/", {
      params: {
        start_date: params.start_date,
        calendar_ids: params.calendar_ids?.join(","),
        ...(params.project_id != null ? { project_id: params.project_id } : {}),
      },
    }),

  getMonthView: (params: {
    year: number;
    month: number;
    calendar_ids?: string[];
    project_id?: number | string | null;
  }) =>
    api.get<CalendarViewResponse>("/api/views/month/", {
      params: {
        year: params.year,
        month: params.month,
        calendar_ids: params.calendar_ids?.join(","),
        ...(params.project_id != null ? { project_id: params.project_id } : {}),
      },
    }),

  getAgendaView: (params: {
    start_date: string;
    end_date?: string;
    calendar_ids?: string[];
    project_id?: number | string | null;
  }) =>
    api.get<CalendarViewResponse>("/api/views/agenda/", {
      params: {
        start_date: params.start_date,
        end_date: params.end_date,
        calendar_ids: params.calendar_ids?.join(","),
        ...(params.project_id != null ? { project_id: params.project_id } : {}),
      },
    }),

  createEvent: (payload: EventWritePayload) =>
    api.post<EventDTO>("/api/events/", payload),

  // For now we do not send If-Match headers to avoid 412 conflicts
  // when the same user updates an event multiple times quickly.
  updateEvent: (eventId: string, payload: EventWritePayload, _etag?: string) =>
    api.patch<EventDTO>(`/api/events/${eventId}/`, payload),

  // Scope = "this only": override a single occurrence of a recurring series.
  updateEventInstance: (
    eventId: string,
    originalStart: string,
    payload: Partial<EventDTO>,
  ) =>
    api.patch<EventDTO>(`/api/events/${eventId}/instances/modify/`, payload, {
      params: { original_start: originalStart },
    }),

  // Scope = "this and future": split the series at the selected occurrence.
  // Returns the newly created series master event.
  splitEventSeries: (
    eventId: string,
    originalStart: string,
    payload: Partial<EventDTO>,
  ) =>
    api.post<EventDTO>(
      `/api/events/${eventId}/instances/modify-future/`,
      payload,
      { params: { original_start: originalStart } },
    ),

  deleteEvent: (eventId: string, _etag?: string) =>
    api.delete<void>(`/api/events/${eventId}/`),

  // Fetch system-derived calendar events (from Decisions and Tasks, read-only)
  getDerivedEvents: (params: {
    start: string;
    end: string;
    project_id?: number | string | null;
  }) =>
    api.get<{ count: number; results: DerivedCalendarEventDTO[] }>("/api/derived-events/", {
      params: {
        start: params.start,
        end: params.end,
        ...(params.project_id != null ? { project_id: params.project_id } : {}),
      },
    }),
};

// Derived CalendarEvent from Decision/Task (read-only, system-generated)
export interface DerivedCalendarEventDTO {
  id: number;
  event_type: "decision" | "task" | "decision_review";
  title: string;
  description: string;  // User-friendly description with source entity details
  start_time: string;
  end_time: string | null;
  decision_id: number | null;
  task_id: number | null;
  decision_slug: string | null;
  task_slug: string | null;
  review_id: number | null;
  project_id: number | null;  // For permission header on navigation
}

// Convert a DerivedCalendarEvent to EventDTO format for display in calendar
export function derivedEventToEventDTO(event: DerivedCalendarEventDTO): EventDTO {
  // Create a combined description that includes both user-friendly content and navigation metadata
  const userDescription = event.description || "";
  const navigationMetadata = JSON.stringify({
    isDerived: true,
    event_type: event.event_type,
    decision_id: event.decision_id,
    task_id: event.task_id,
    decision_slug: event.decision_slug,
    task_slug: event.task_slug,
    review_id: event.review_id,
    project_id: event.project_id,  // Used to build correct navigation URL with permission
  });
  
  // Combine user description with metadata, separated by a delimiter
  const combinedDescription = userDescription + "\n\n__METADATA__\n" + navigationMetadata;
  
  // Determine if this is an all-day event
  // An event is all-day if:
  // 1. It starts at 00:00:00 and ends at 23:59:59 (or similar end-of-day time)
  // 2. Or it spans multiple days with start at 00:00:00
  const startDate = new Date(event.start_time);
  const endDate = event.end_time ? new Date(event.end_time) : startDate;
  const isAllDay = startDate.getUTCHours() === 0 && startDate.getUTCMinutes() === 0 && startDate.getUTCSeconds() === 0 &&
    ((endDate.getUTCHours() === 23 && endDate.getUTCMinutes() === 59) || 
     (startDate.toDateString() !== endDate.toDateString())); // Multi-day event

  // For all-day events, convert to local date format to avoid timezone issues
  let startDateTime = event.start_time;
  let endDateTime = event.end_time;
  
  // If no end_time provided, set a default duration for point-in-time events
  if (!endDateTime) {
    const startTime = new Date(event.start_time);
    const endTime = new Date(startTime.getTime() + 60 * 60 * 1000); // Add 1 hour
    endDateTime = endTime.toISOString();
  }
  
  if (isAllDay) {
    // Convert UTC dates to local date strings to avoid timezone shifts
    const startLocalDate = startDate.getUTCFullYear() + '-' + 
      String(startDate.getUTCMonth() + 1).padStart(2, '0') + '-' + 
      String(startDate.getUTCDate()).padStart(2, '0');
    const endLocalDate = endDate.getUTCFullYear() + '-' + 
      String(endDate.getUTCMonth() + 1).padStart(2, '0') + '-' + 
      String(endDate.getUTCDate()).padStart(2, '0');
    
    startDateTime = startLocalDate + 'T00:00:00';
    endDateTime = endLocalDate + 'T23:59:59';
  }

  return {
    id: `derived-${event.id}`,  // Prefix to prevent ID conflicts with regular events
    title: event.title,
    start_datetime: startDateTime,
    end_datetime: endDateTime,
    is_all_day: isAllDay,
    is_recurring: false,
    color: eventTypeToColor(event.event_type),
    description: combinedDescription,
  };
}

// Map event type to display color
function eventTypeToColor(eventType: string): string {
  switch (eventType) {
    case "decision": return "#8B5CF6";        // Purple - Decision
    case "decision_review": return "#F59E0B"; // Orange - Review
    case "task": return "#10B981";            // Green - Task
    default: return "#6B7280";
  }
}

// Helper functions to extract information from derived event descriptions
export function extractUserDescription(eventDescription: string): string {
  if (!eventDescription) return "";
  
  const metadataDelimiter = "\n\n__METADATA__\n";
  const parts = eventDescription.split(metadataDelimiter);
  return parts[0] || "";
}

export function extractNavigationMetadata(eventDescription: string): any {
  if (!eventDescription) return null;
  
  const metadataDelimiter = "\n\n__METADATA__\n";
  const parts = eventDescription.split(metadataDelimiter);
  
  if (parts.length < 2) {
    // Fallback: try to parse the entire description as JSON (for backward compatibility)
    try {
      return JSON.parse(eventDescription);
    } catch {
      return null;
    }
  }
  
  try {
    return JSON.parse(parts[1]);
  } catch {
    return null;
  }
}


// ── Public booking links ────────────────────────────────────────

/**
 * Booking pages are viewed by external prospects who have no account, so these
 * calls use their own axios instance with no auth interceptors — the shared
 * `api` client attaches tokens and redirects on 401, neither of which makes
 * sense here. Mirrors googleAdsPublicPreviewApi.
 */
const publicApi = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL || '',
  timeout: 15000,
  headers: {
    'Content-Type': 'application/json',
    Accept: 'application/json, text/plain, */*',
  },
});

export interface BookingSlotDTO {
  /** ISO 8601 UTC. */
  start: string;
  end: string;
}

export interface ViewerBookingDTO {
  start: string;
  end: string;
  title: string;
}

export interface PublicBookingLinkDTO {
  slug: string;
  title: string;
  description: string | null;
  duration_minutes: number;
  /** Lets the client expire slots that lapse while the page sits open. */
  min_notice_minutes: number;
  /** The owner's timezone, for showing what time it is on their side. */
  timezone: string;
  owner_name: string;
  slots: BookingSlotDTO[];
  /** When true, only named invitees who are signed in can book. */
  invitees_only?: boolean;
  /** False on an invitees-only link until the viewer is a named invitee. */
  viewer_can_book?: boolean;
  /** Signed-in viewer shares a project with the host. Guests never see this. */
  same_project?: boolean;
  /** Upcoming bookings this signed-in viewer already has on this link. */
  viewer_bookings?: ViewerBookingDTO[];
}

export interface BookingRequestPayload {
  name: string;
  email: string;
  /** Optional: helps the host reach a guest whose email bounces. */
  phone?: string;
  /** ISO 8601 with an explicit offset — the backend rejects naive datetimes. */
  start: string;
  notes?: string;
}

export interface BookingConfirmationDTO {
  status: string;
  start: string;
  end: string;
  title: string;
  timezone: string;
  /**
   * The guest's handle on this booking. Recovery sends it to the booking email.
   */
  cancel_token: string;
  /** Subscribable calendar feed — stays in step if the booking is cancelled. */
  feed_url: string;
}

function bookingBase(orgSlug: string, linkSlug: string): string {
  return `/api/public/book/${encodeURIComponent(orgSlug)}/${encodeURIComponent(linkSlug)}`;
}

export const PublicBookingAPI = {
  /** Link details plus bookable slots. `from`/`to` are ISO 8601. */
  getAvailability: (
    orgSlug: string,
    linkSlug: string,
    range?: { from?: string; to?: string },
    accessToken?: string | null,
  ) =>
    publicApi
      .get<PublicBookingLinkDTO>(`${bookingBase(orgSlug, linkSlug)}/`, {
        params: range,
        headers: accessToken
          ? { Authorization: `Bearer ${accessToken}` }
          : undefined,
      })
      .then((res) => res.data),

  createBooking: (
    orgSlug: string,
    linkSlug: string,
    payload: BookingRequestPayload,
    accessToken?: string | null,
  ) => {
    const post = (token?: string | null) =>
      publicApi
        .post<BookingConfirmationDTO>(
          `${bookingBase(orgSlug, linkSlug)}/bookings/`,
          payload,
          token
            ? { headers: { Authorization: `Bearer ${token}` } }
            : undefined,
        )
        .then((res) => res.data);

    // An expired session must not silently downgrade a member to a guest.
    return post(accessToken);
  },

  cancelBooking: (orgSlug: string, linkSlug: string, token: string) =>
    publicApi
      .post<{ status: string }>(`${bookingBase(orgSlug, linkSlug)}/cancel/`, { token })
      .then((res) => res.data),

  lookupBookings: (
    orgSlug: string,
    linkSlug: string,
    query: { email: string },
  ) =>
    publicApi
      .post<{ status: string }>(
        `${bookingBase(orgSlug, linkSlug)}/lookup/`,
        query,
      )
      .then((res) => res.data),
};


// ── Owner-facing booking link management ────────────────────────

/** A weekly availability window, in the owner's timezone. */
export interface BookingWindowDTO {
  /** Monday=0 … Sunday=6. */
  weekday: number;
  /** "HH:MM" */
  start: string;
  end: string;
}

export interface BookingLinkDTO {
  id: string;
  slug: string;
  /** The org that owns this link. Authoritative for building the public URL. */
  organization_slug: string;
  title: string;
  description: string | null;
  duration_minutes: number;
  slot_increment_minutes: number;
  buffer_before_minutes: number;
  buffer_after_minutes: number;
  min_notice_minutes: number;
  max_advance_days: number;
  timezone: string;
  availability_windows: BookingWindowDTO[];
  is_active: boolean;
  /** Team (project calendar) or personal. */
  scope: 'team' | 'personal';
  /** Calendar the bookings land on. */
  calendar: string;
  /** Whose time the link books. Not necessarily whoever set it up. */
  host: { id: number; name: string };
  /** Who it was made for. id is null for a guest with no account here. */
  invitees: { id: number | null; name: string; email: string }[];
  invitee_emails: string[];
  /** When true, only named invitees who are signed in can book. */
  invitees_only: boolean;
  /** Blank when the host set the link up themselves. */
  created_by_name: string;
  created_at: string;
  updated_at: string;
}

export type BookingLinkWritePayload = Partial<
  Omit<
    BookingLinkDTO,
    'id' | 'created_at' | 'updated_at' | 'host' | 'calendar' | 'invitees'
  >
> & {
  /** Required on create: the calendar bookings are written to. */
  calendar_id?: string;
  /** The host. Omitted or null books your own time. */
  host_id?: number | null;
  /** Colleagues as guests. An empty list clears whoever was named. */
  invitee_ids?: number[];
};

const BOOKING_LINKS_BASE = '/api/booking-links';

/** The list is unpaginated server-side; stay tolerant of an envelope anyway. */
function unwrapList<T>(data: unknown): T[] {
  return Array.isArray(data) ? data : ((data as { results?: T[] })?.results ?? []);
}

export const BookingLinkAPI = {
  list: () =>
    api
      .get<BookingLinkDTO[] | { results: BookingLinkDTO[] }>(`${BOOKING_LINKS_BASE}/`)
      .then((res) => unwrapList<BookingLinkDTO>(res.data)),

  create: (payload: BookingLinkWritePayload) =>
    api.post<BookingLinkDTO>(`${BOOKING_LINKS_BASE}/`, payload),

  update: (id: string, payload: BookingLinkWritePayload) =>
    api.patch<BookingLinkDTO>(`${BOOKING_LINKS_BASE}/${id}/`, payload),

  destroy: (id: string) => api.delete(`${BOOKING_LINKS_BASE}/${id}/`),
};

/**
 * The shareable URL for a link.
 *
 * The org slug is part of the path because the public endpoint has no
 * authenticated user to resolve the tenant from — see the booking views.
 */
export function bookingLinkUrl(orgSlug: string, linkSlug: string): string {
  const origin = typeof window === 'undefined' ? '' : window.location.origin;
  // Encoded for the same reason bookingBase() encodes: both segments are
  // slugs today, but this is the string a user copies and pastes, so it should
  // stay a valid URL whatever it is handed.
  return `${origin}/book/${encodeURIComponent(orgSlug)}/${encodeURIComponent(linkSlug)}`;
}
