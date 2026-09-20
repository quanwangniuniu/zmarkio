import type { CalendarDTO } from '@/lib/api/calendarApi';

export type BookingScope = 'team' | 'personal';

export function calendarScope(calendar: { project_id?: number | null }): BookingScope {
  return calendar.project_id != null ? 'team' : 'personal';
}

export function calendarsForScope(
  calendars: CalendarDTO[],
  scope: BookingScope,
): CalendarDTO[] {
  return calendars.filter((calendar) => calendarScope(calendar) === scope);
}

export function defaultCalendarId(
  calendars: CalendarDTO[],
  scope: BookingScope,
): string {
  const pool = calendarsForScope(calendars, scope);
  if (scope === 'personal') {
    return pool.find((calendar) => calendar.is_primary)?.id ?? pool[0]?.id ?? '';
  }
  return pool[0]?.id ?? '';
}

export const SAME_PROJECT_TEAM_MESSAGE =
  "You're in the same project, so this link can only use your personal calendar.";

export function bookerCanUseTeamScope(sameProject: boolean): boolean {
  return !sameProject;
}

export function defaultBookingScope(calendars: CalendarDTO[]): BookingScope {
  if (calendarsForScope(calendars, 'personal').length > 0) {
    return 'personal';
  }
  return calendarsForScope(calendars, 'team').length > 0 ? 'team' : 'personal';
}

export function inferBookingScope(
  calendars: CalendarDTO[],
  calendarId: string | null | undefined,
  fallback: BookingScope = 'personal',
): BookingScope {
  const match = calendars.find((calendar) => calendar.id === calendarId);
  return match ? calendarScope(match) : fallback;
}
