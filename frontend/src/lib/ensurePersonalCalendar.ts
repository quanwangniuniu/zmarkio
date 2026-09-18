import { CalendarAPI, type CalendarDTO } from '@/lib/api/calendarApi';
import { calendarsForScope } from '@/lib/bookingLinkScope';

export function normalizeCalendarList(payload: unknown): CalendarDTO[] {
  if (Array.isArray(payload)) {
    return payload;
  }
  if (
    payload &&
    typeof payload === 'object' &&
    Array.isArray((payload as { results?: unknown[] }).results)
  ) {
    return (payload as { results: CalendarDTO[] }).results;
  }
  return [];
}

export function findPersonalCalendar(
  calendars: CalendarDTO[],
): CalendarDTO | undefined {
  return (
    calendarsForScope(calendars, 'personal').find((calendar) => calendar.is_primary)
    ?? calendarsForScope(calendars, 'personal')[0]
  );
}

export async function provisionPersonalCalendar(
  known: CalendarDTO[] = [],
): Promise<{ calendar: CalendarDTO; created: boolean }> {
  const existing = findPersonalCalendar(known);
  if (existing) {
    return { calendar: existing, created: false };
  }

  const unscoped = normalizeCalendarList(
    await CalendarAPI.listCalendars()
      .then((res) => res.data)
      .catch(() => []),
  );
  const found = findPersonalCalendar(unscoped);
  if (found) {
    return { calendar: found, created: false };
  }

  const usedNames = new Set([...known, ...unscoped].map((calendar) => calendar.name));
  const created = await CalendarAPI.createCalendar({
    name: usedNames.has('My Calendar') ? 'Personal calendar' : 'My Calendar',
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC',
    visibility: 'private',
    is_primary: true,
  });
  return { calendar: created.data, created: true };
}
