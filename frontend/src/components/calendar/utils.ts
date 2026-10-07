import type { CalendarViewType } from "@/lib/api/calendarApi";
import type { EventPanelPosition } from "@/components/calendar/types";

export const VIEW_LABELS: Record<CalendarViewType, string> = {
  day: "Day",
  week: "Week",
  month: "Month",
  year: "Year",
  agenda: "Agenda",
};

export const VIEW_SHORTCUTS: Record<CalendarViewType, string> = {
  day: "D",
  week: "W",
  month: "M",
  year: "Y",
  agenda: "A",
};

export const CALENDAR_FILTER_STORAGE_KEY = "calendar:selected_calendar_id";
export const CALENDAR_FILTER_ALL_VALUE = "all";

export function calendarFilterStorageKey(projectId?: number | string | null): string {
  if (projectId == null) {
    return CALENDAR_FILTER_STORAGE_KEY;
  }
  return `${CALENDAR_FILTER_STORAGE_KEY}:project:${projectId}`;
}

export function calendarAllEventsStorageKey(projectId?: number | string | null): string {
  return `${calendarFilterStorageKey(projectId)}:all_events`;
}

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export function extractCalendarIdFromStoredValue(raw: string | null): string | null {
  if (!raw) {
    return null;
  }
  const trimmed = raw.trim();
  if (!trimmed) {
    return null;
  }

  const toValidId = (value: unknown): string | null => {
    if (typeof value !== "string") {
      return null;
    }
    const id = value.trim();
    if (!id) {
      return null;
    }
    return UUID_PATTERN.test(id) ? id : null;
  };

  const direct = toValidId(trimmed);
  if (direct) {
    return direct;
  }

  if (
    trimmed.startsWith("[") ||
    trimmed.startsWith("{") ||
    trimmed.startsWith('"')
  ) {
    try {
      const parsed = JSON.parse(trimmed);
      if (Array.isArray(parsed)) {
        return toValidId(parsed[0]);
      }
      if (typeof parsed === "object" && parsed) {
        const fromCalendarId = toValidId(
          (parsed as { calendarId?: unknown }).calendarId,
        );
        if (fromCalendarId) {
          return fromCalendarId;
        }
        return toValidId((parsed as { id?: unknown }).id);
      }
      return toValidId(parsed);
    } catch {
      return null;
    }
  }

  return null;
}

export function isAllEventsStoredValue(raw: string | null): boolean {
  return raw?.trim().toLowerCase() === CALENDAR_FILTER_ALL_VALUE;
}

export function isAllEventsSelection(
  visibleCalendarIds: string[] | undefined,
): boolean {
  return Array.isArray(visibleCalendarIds) && visibleCalendarIds.length === 0;
}

export function resolveVisibleCalendarSelection(
  projectId: number | string | null,
  accessibleCalendars: Array<{ id: string; project_id?: number | string | null }>,
  visibleCalendarIds: string[] | undefined,
): string[] | null {
  if (!projectId || accessibleCalendars.length === 0) {
    return null;
  }
  if (isAllEventsSelection(visibleCalendarIds)) {
    return null;
  }
  const projectCalendar = accessibleCalendars.find(
    (cal) => String(cal.project_id) === String(projectId),
  );
  if (visibleCalendarIds && visibleCalendarIds.length === 1) {
    const selectedStillExists = accessibleCalendars.some(
      (cal) => cal.id === visibleCalendarIds[0],
    );
    if (selectedStillExists) {
      return null;
    }
  }
  return projectCalendar ? [projectCalendar.id] : null;
}

export function sameCalendarIdList(
  a: string[] | undefined,
  b: string[] | undefined,
): boolean {
  if (a === b) {
    return true;
  }
  if (!a && !b) {
    return true;
  }
  if (!a || !b || a.length !== b.length) {
    return false;
  }
  for (let i = 0; i < a.length; i += 1) {
    if (a[i] !== b[i]) {
      return false;
    }
  }
  return true;
}

export function computePanelPosition(
  rect: DOMRect | null,
  view?: CalendarViewType,
): EventPanelPosition {
  const viewportWidth =
    typeof window !== "undefined" ? window.innerWidth : 1024;
  const viewportHeight =
    typeof window !== "undefined" ? window.innerHeight : 768;
  const margin = 16;
  const panelWidth = Math.min(420, viewportWidth - margin * 2);
  const panelHeight = Math.min(388, viewportHeight - margin * 2);

  if (view === "day" || !rect) {
    const top = Math.max(margin, (viewportHeight - panelHeight) / 2);
    const left = Math.max(margin, (viewportWidth - panelWidth) / 2);
    return { top, left };
  }

  let left = rect.right + margin;
  if (left + panelWidth > viewportWidth - margin) {
    left = rect.left - panelWidth - margin;
  }
  left = Math.max(margin, Math.min(left, viewportWidth - panelWidth - margin));

  let top = rect.top;
  if (top < margin) {
    top = rect.bottom + margin;
  } else if (top + panelHeight > viewportHeight - margin) {
    top = rect.bottom - panelHeight - margin;
  }
  top = Math.max(margin, Math.min(top, viewportHeight - panelHeight - margin));

  return { top, left };
}
