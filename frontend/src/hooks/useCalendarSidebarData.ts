import { useEffect, useMemo, useState } from "react";
import {
  CalendarAPI,
  CalendarDTO,
  CalendarSubscriptionDTO,
} from "@/lib/api/calendarApi";
import { useAuthStore } from "@/lib/authStore";
import {
  findPersonalCalendar,
  provisionPersonalCalendar,
} from "@/lib/ensurePersonalCalendar";

export interface SidebarCalendarItem {
  calendarId: string;
  subscriptionId: string | null;
  name: string;
  color: string;
  isHidden: boolean;
  isMine: boolean;
  isTeam: boolean;
}

interface UseCalendarSidebarResult {
  myCalendars: SidebarCalendarItem[];
  otherCalendars: SidebarCalendarItem[];
  isLoading: boolean;
  error: Error | null;
  toggleVisibility: (item: SidebarCalendarItem) => Promise<void>;
}

const sidebarCache: {
  projectId?: number | string | null;
  calendars?: CalendarDTO[];
  subscriptions?: CalendarSubscriptionDTO[];
} = {};

export function clearCalendarSidebarCache(): void {
  delete sidebarCache.calendars;
  delete sidebarCache.subscriptions;
  delete sidebarCache.projectId;
}

function normalizeListResponse<T>(payload: unknown): T[] {
  if (Array.isArray(payload)) {
    return payload;
  }
  if (
    payload &&
    typeof payload === "object" &&
    Array.isArray((payload as { results?: unknown[] }).results)
  ) {
    return (payload as { results: T[] }).results;
  }
  return [];
}

export function useCalendarSidebarData(
  projectId?: number | string | null,
): UseCalendarSidebarResult {
  const [calendars, setCalendars] = useState<CalendarDTO[]>([]);
  const [subscriptions, setSubscriptions] = useState<CalendarSubscriptionDTO[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [error, setError] = useState<Error | null>(null);

  const { user } = useAuthStore();
  const currentUserId = user?.id;

  useEffect(() => {
    const cacheHit =
      sidebarCache.projectId === projectId &&
      Array.isArray(sidebarCache.calendars) &&
      Array.isArray(sidebarCache.subscriptions);

    if (cacheHit) {
      setCalendars(sidebarCache.calendars!);
      setSubscriptions(sidebarCache.subscriptions!);
      return;
    }

    setIsLoading(true);
    setError(null);

    Promise.all([
      CalendarAPI.listCalendars(projectId),
      CalendarAPI.listSubscriptions(),
    ])
      .then(async ([calRes, subRes]) => {
        let normalizedCalendars = normalizeListResponse<CalendarDTO>(calRes.data);
        const normalizedSubscriptions = normalizeListResponse<CalendarSubscriptionDTO>(
          subRes.data,
        );
        if (projectId != null && !findPersonalCalendar(normalizedCalendars)) {
          const { calendar } = await provisionPersonalCalendar(normalizedCalendars);
          if (!normalizedCalendars.some((item) => item.id === calendar.id)) {
            normalizedCalendars = [...normalizedCalendars, calendar];
          }
        }
        sidebarCache.projectId = projectId;
        sidebarCache.calendars = normalizedCalendars;
        sidebarCache.subscriptions = normalizedSubscriptions;
        setCalendars(normalizedCalendars);
        setSubscriptions(normalizedSubscriptions);
      })
      .catch((err: unknown) => {
        setError(
          err instanceof Error ? err : new Error("Failed to load calendar sidebar data"),
        );
      })
      .finally(() => {
        setIsLoading(false);
      });
  }, [projectId]);

  const items = useMemo<SidebarCalendarItem[]>(() => {
    if (!calendars.length && !subscriptions.length) {
      return [];
    }

    const byCalendarId = new Map<string, CalendarSubscriptionDTO>();
    subscriptions.forEach((sub) => {
      if (sub.calendar) {
        byCalendarId.set(sub.calendar.id, sub);
      }
    });

    return calendars.map((cal) => {
      const sub = byCalendarId.get(cal.id);
      const effectiveColor = sub?.color_override || cal.color;
      const isHidden = sub?.is_hidden ?? false;
      const subscriptionId = sub ? sub.id : null;
      const isMine =
        typeof currentUserId === "number"
          ? cal.owner?.id === currentUserId
          : false;

      return {
        calendarId: cal.id,
        subscriptionId,
        name: cal.name,
        color: effectiveColor,
        isHidden,
        isMine,
        isTeam: cal.project_id != null,
      };
    });
  }, [calendars, subscriptions, currentUserId]);

  const myCalendars = useMemo(
    () => items.filter((item) => !item.isTeam),
    [items],
  );
  const otherCalendars = useMemo(
    () => items.filter((item) => item.isTeam),
    [items],
  );

  const toggleVisibility = async (item: SidebarCalendarItem) => {
    if (!item.subscriptionId) {
      return;
    }

    const nextHidden = !item.isHidden;
    const previousSubscriptions = [...subscriptions];

    setSubscriptions((current) =>
      current.map((sub) =>
        sub.id === item.subscriptionId
          ? { ...sub, is_hidden: nextHidden }
          : sub,
      ),
    );

    try {
      await CalendarAPI.updateSubscription(item.subscriptionId, {
        is_hidden: nextHidden,
      });
    } catch (err) {
      setSubscriptions(previousSubscriptions);
      throw err;
    }
  };

  return {
    myCalendars,
    otherCalendars,
    isLoading,
    error,
    toggleVisibility,
  };
}
