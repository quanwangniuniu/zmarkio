import type { CalendarDTO, EventDTO } from "@/lib/api/calendarApi";
import type { SidebarCalendarItem } from "@/hooks/useCalendarSidebarData";
import { addDays, startOfDay, startOfWeek } from "date-fns";

/**
 * Sample calendar data matching the API response format.
 * Used in Storybook to simulate real calendar data.
 */
export function getSampleCalendars(): CalendarDTO[] {
  return [
    {
      id: "11111111-1111-4111-8111-111111111111",
      organization_id: "org-1",
      owner: {
        id: 1,
        email: "alex@example.com",
        username: "alex",
        full_name: "Alex",
      },
      name: "Team Calendar",
      color: "#1E88E5",
      visibility: "private",
      timezone: "UTC",
      is_primary: true,
    },
    {
      id: "22222222-2222-4222-8222-222222222222",
      organization_id: "org-1",
      owner: {
        id: 2,
        email: "sam@example.com",
        username: "sam",
        full_name: "Sam",
      },
      name: "Marketing",
      color: "#43A047",
      visibility: "private",
      timezone: "UTC",
      is_primary: false,
    },
  ];
}

/**
 * Sample events matching the API format.
 * Generates events relative to the given date for consistent display.
 */
export function getSampleEvents(anchorDate: Date = new Date()): EventDTO[] {
  const weekStart = startOfWeek(anchorDate, { weekStartsOn: 1 });
  const today = startOfDay(anchorDate);
  const toIso = (d: Date) => d.toISOString().slice(0, 19) + ".000Z";

  return [
    {
      id: "event-1",
      calendar_id: "11111111-1111-4111-8111-111111111111",
      title: "Design Review",
      description: "Review latest UI iterations.",
      start_datetime: toIso(new Date(today.getTime() + 9 * 60 * 60 * 1000)),
      end_datetime: toIso(new Date(today.getTime() + 10 * 60 * 60 * 1000)),
      timezone: "UTC",
      is_all_day: false,
      is_recurring: false,
      color: "#1E88E5",
      etag: "etag-1",
    },
    {
      id: "event-2",
      calendar_id: "22222222-2222-4222-8222-222222222222",
      title: "Campaign Sync",
      description: "Weekly campaign status.",
      start_datetime: toIso(new Date(today.getTime() + 13 * 60 * 60 * 1000)),
      end_datetime: toIso(new Date(today.getTime() + 14 * 60 * 60 * 1000)),
      timezone: "UTC",
      is_all_day: false,
      is_recurring: false,
      color: "#43A047",
      etag: "etag-2",
    },
    {
      id: "event-3",
      calendar_id: "11111111-1111-4111-8111-111111111111",
      title: "Sprint Planning",
      description: "Plan next sprint tasks.",
      start_datetime: toIso(
        new Date(addDays(weekStart, 1).getTime() + 10 * 60 * 60 * 1000),
      ),
      end_datetime: toIso(
        new Date(addDays(weekStart, 1).getTime() + 12 * 60 * 60 * 1000),
      ),
      timezone: "UTC",
      is_all_day: false,
      is_recurring: false,
      color: "#1E88E5",
      etag: "etag-3",
    },
  ];
}

export function getSampleSidebarMyCalendars(): SidebarCalendarItem[] {
  const calendars = getSampleCalendars();
  return [
    {
      calendarId: calendars[0].id,
      subscriptionId: "sub-1",
      name: calendars[0].name,
      color: calendars[0].color,
      isHidden: false,
      isMine: true,
      isTeam: false,
    },
  ];
}

export function getSampleSidebarOtherCalendars(): SidebarCalendarItem[] {
  const calendars = getSampleCalendars();
  return [
    {
      calendarId: calendars[1].id,
      subscriptionId: "sub-2",
      name: calendars[1].name,
      color: calendars[1].color,
      isHidden: false,
      isMine: false,
      isTeam: true,
    },
  ];
}
