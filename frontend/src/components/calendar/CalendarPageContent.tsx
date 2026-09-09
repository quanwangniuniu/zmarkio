'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useBuildUrl } from "@/lib/buildUrl";
import { addDays, format, startOfWeek } from "date-fns";
import toast from "react-hot-toast";
import { CalendarAPI, extractNavigationMetadata } from "@/lib/api/calendarApi";
import type {
  CalendarDTO,
  CalendarViewType,
  EventDTO,
  RecurringEditScope,
} from "@/lib/api/calendarApi";
import {
  findPersonalCalendar,
  normalizeCalendarList,
  provisionPersonalCalendar,
} from "@/lib/ensurePersonalCalendar";
import { RecurringEditScopeDialog } from "@/components/calendar/RecurringEditScopeDialog";
import { googleCalendarApi } from "@/lib/api/googleCalendarApi";
import type { GoogleCalendarStatus } from "@/lib/api/googleCalendarApi";
import { GoogleCalendarConnectedBadge } from "@/components/google-calendar/GoogleCalendarConnectedBadge";
import { useCalendarView } from "@/hooks/useCalendarView";
import { CalendarToolbar } from "@/components/calendar/CalendarToolbar";
import { CalendarSidebarContainer } from "@/components/calendar/CalendarSidebarContainer";
import { CalendarViewRouter } from "@/components/calendar/CalendarViews";
import { EventDialogContainer } from "@/components/calendar/EventDialogContainer";
import type { CalendarDialogMode, EventPanelPosition } from "@/components/calendar/types";
import { List, Loader2, RefreshCw } from "lucide-react";
import { useProjectStore } from "@/lib/projectStore";
import {
  VIEW_LABELS,
  calendarAllEventsStorageKey,
  calendarFilterStorageKey,
  extractCalendarIdFromStoredValue,
  isAllEventsStoredValue,
  resolveVisibleCalendarSelection,
  sameCalendarIdList,
} from "@/components/calendar/utils";
import { calendarScope } from "@/lib/bookingLinkScope";
import { isBookingEvent } from "@/lib/bookingEvent";
import { openAgentSidePanel } from "@/lib/agentSidePanelStore";
import {
  clearCalendarSidebarCache,
} from "@/hooks/useCalendarSidebarData";

const ACTIVITY_FILTER_STORAGE_KEY = "calendar:activity-filter";

function loadActivityFilter(): Set<string> {
  if (typeof window === "undefined") {
    return new Set(["decision", "task"]);
  }
  try {
    const raw = window.localStorage.getItem(ACTIVITY_FILTER_STORAGE_KEY);
    if (!raw) {
      return new Set(["decision", "task"]);
    }
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) {
      return new Set(parsed.filter((v) => typeof v === "string"));
    }
  } catch {
    // Fall through to default.
  }
  return new Set(["decision", "task"]);
}

export default function CalendarPageContent() {
  const router = useRouter();
  const buildUrl = useBuildUrl();
  const activeProject = useProjectStore((state) => state.activeProject);
  const projectId = activeProject?.id ?? null;
  const [currentView, setCurrentView] = useState<CalendarViewType>("week");
  const [currentDate, setCurrentDate] = useState<Date>(new Date());
  const [visibleCalendarIds, setVisibleCalendarIds] = useState<string[] | undefined>(undefined);
  const [includeAllEvents, setIncludeAllEvents] = useState(false);
  const [hasLoadedCalendarFilter, setHasLoadedCalendarFilter] = useState(false);

  const [isDialogOpen, setIsDialogOpen] = useState(false);
  const [dialogMode, setDialogMode] = useState<CalendarDialogMode>("create");
  const [dialogStart, setDialogStart] = useState<Date | null>(null);
  const [dialogEnd, setDialogEnd] = useState<Date | null>(null);
  const [editingEvent, setEditingEvent] = useState<EventDTO | null>(null);
  const [panelPosition, setPanelPosition] = useState<EventPanelPosition | null>(null);
  const [viewSwitcherOpen, setViewSwitcherOpen] = useState(false);
  const [recurringDrag, setRecurringDrag] = useState<{
    event: EventDTO;
    start: Date;
    end: Date;
  } | null>(null);

  const [activeEventTypes, setActiveEventTypes] = useState<Set<string>>(() =>
    loadActivityFilter(),
  );
  const viewSwitcherRef = useRef<HTMLDivElement>(null);
  const [gcalStatus, setGcalStatus] = useState<GoogleCalendarStatus | null>(null);
  const [gcalSyncing, setGcalSyncing] = useState(false);
  const [primaryCalendar, setPrimaryCalendar] = useState<CalendarDTO | null>(null);
  const [accessibleCalendars, setAccessibleCalendars] = useState<CalendarDTO[]>([]);

  const refreshGcalStatus = useCallback(() => {
    googleCalendarApi
      .getStatus()
      .then((s) => setGcalStatus(s))
      .catch(() => setGcalStatus(null));
  }, []);

  useEffect(() => {
    refreshGcalStatus();
  }, [refreshGcalStatus]);

  useEffect(() => {
    let cancelled = false;
    CalendarAPI.listCalendars(projectId)
      .then(async (res) => {
        let list = normalizeCalendarList(res.data);
        if (projectId != null && !findPersonalCalendar(list)) {
          const { calendar, created } = await provisionPersonalCalendar(list);
          if (!list.some((item) => item.id === calendar.id)) {
            list = [...list, calendar];
          }
          if (created) {
            clearCalendarSidebarCache();
          }
        }
        if (cancelled) {
          return;
        }
        setAccessibleCalendars(list);
        setPrimaryCalendar(
          list.find((item) => item.is_primary && item.project_id == null)
            ?? list.find((item) => item.is_primary)
            ?? null,
        );
      })
      .catch(() => {
        if (!cancelled) {
          setAccessibleCalendars([]);
          setPrimaryCalendar(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  useEffect(() => {
    clearCalendarSidebarCache();
    setVisibleCalendarIds(undefined);
    setIncludeAllEvents(false);
    setHasLoadedCalendarFilter(false);
  }, [projectId]);

  useEffect(() => {
    const onVis = () => {
      if (document.visibilityState === "visible") {
        refreshGcalStatus();
      }
    };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, [refreshGcalStatus]);

  const handleAskAgentFromCalendar = useCallback(() => {
    const ctx = {
      type: "calendar" as const,
      calendarIds: visibleCalendarIds ?? [],
      currentView,
      currentDate: format(currentDate, "yyyy-MM-dd"),
      userTimezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    };
    sessionStorage.setItem("agent-calendar-context", JSON.stringify(ctx));
    sessionStorage.removeItem("agent-session-id");
    openAgentSidePanel();
  }, [visibleCalendarIds, currentView, currentDate]);

  const handleAskAgentFromEvent = useCallback(
    (event: EventDTO) => {
      const ctx = {
        type: "event" as const,
        eventId: event.id,
        eventTitle: event.title || "(No title)",
        calendarId: event.calendar_id,
        startDatetime: event.start_datetime,
        endDatetime: event.end_datetime,
        description: event.description ?? "",
        userTimezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      };
      sessionStorage.setItem("agent-calendar-context", JSON.stringify(ctx));
      sessionStorage.removeItem("agent-session-id");
      openAgentSidePanel();
    },
    [],
  );

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    window.localStorage.setItem(
      ACTIVITY_FILTER_STORAGE_KEY,
      JSON.stringify(Array.from(activeEventTypes)),
    );
  }, [activeEventTypes]);

  const toggleActivityType = useCallback((type: string) => {
    setActiveEventTypes((prev) => {
      const next = new Set(prev);
      if (next.has(type)) {
        next.delete(type);
      } else {
        next.add(type);
      }
      return next;
    });
  }, []);

  const selectedCalendarId = useMemo(
    () =>
      visibleCalendarIds && visibleCalendarIds.length === 1
        ? visibleCalendarIds[0]
        : null,
    [visibleCalendarIds],
  );
  const isPersonalSelected = useMemo(() => {
    if (!selectedCalendarId) {
      return false;
    }
    const selected = accessibleCalendars.find((cal) => cal.id === selectedCalendarId);
    return selected != null && calendarScope(selected) === "personal";
  }, [accessibleCalendars, selectedCalendarId]);
  const showAllEvents = includeAllEvents && isPersonalSelected;
  const viewCalendarIds = showAllEvents ? undefined : visibleCalendarIds;

  const { events, calendars, isLoading, error, refetch } = useCalendarView({
    viewType: currentView,
    currentDate,
    calendarIds: viewCalendarIds,
    activeEventTypes: Array.from(activeEventTypes),
    projectId,
  });

  const handleGcalSync = useCallback(async () => {
    setGcalSyncing(true);
    try {
      await googleCalendarApi.syncNow();
      toast.success("Synced with Google Calendar.");
      refetch();
      refreshGcalStatus();
    } catch {
      toast.error("Sync failed. Check your connection and try again.");
    } finally {
      setGcalSyncing(false);
    }
  }, [refetch, refreshGcalStatus]);

  useEffect(() => {
    const consumePending = () => {
      const pending = localStorage.getItem("calendar-events-updated");
      if (pending) {
        localStorage.removeItem("calendar-events-updated");
        refetch();
      }
    };

    consumePending();

    const handleRefresh = () => refetch();

    const handleStorage = (e: StorageEvent) => {
      if (e.key === "calendar-events-updated") {
        localStorage.removeItem("calendar-events-updated");
        refetch();
      }
    };

    const handlePageShow = (e: PageTransitionEvent) => {
      if (e.persisted) consumePending();
    };

    const handleVisibility = () => {
      if (document.visibilityState === "visible") {
        consumePending();
        refetch();
      }
    };

    window.addEventListener("agent:calendar-updated", handleRefresh);
    window.addEventListener("storage", handleStorage);
    window.addEventListener("pageshow", handlePageShow);
    document.addEventListener("visibilitychange", handleVisibility);

    return () => {
      window.removeEventListener("agent:calendar-updated", handleRefresh);
      window.removeEventListener("storage", handleStorage);
      window.removeEventListener("pageshow", handlePageShow);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, [refetch]);

  useEffect(() => {
    refetch();
  }, []);

  const handleVisibleCalendarsChange = useCallback(
    (calendarIds: string[] | undefined) => {
      if (calendarIds && calendarIds.length === 1) {
        setIncludeAllEvents(false);
      }
      setVisibleCalendarIds((current) =>
        sameCalendarIdList(current, calendarIds) ? current : calendarIds,
      );
    },
    [],
  );

  useEffect(() => {
    if (!includeAllEvents || !hasLoadedCalendarFilter || accessibleCalendars.length === 0) {
      return;
    }
    if (!isPersonalSelected) {
      setIncludeAllEvents(false);
    }
  }, [
    includeAllEvents,
    hasLoadedCalendarFilter,
    accessibleCalendars.length,
    isPersonalSelected,
  ]);

  useEffect(() => {
    if (typeof window === "undefined") {
      return;
    }
    const storageKey = calendarFilterStorageKey(projectId);
    const allEventsKey = calendarAllEventsStorageKey(projectId);
    const storedValue = window.localStorage.getItem(storageKey);
    if (isAllEventsStoredValue(storedValue)) {
      setIncludeAllEvents(true);
      window.localStorage.removeItem(storageKey);
    } else {
      setIncludeAllEvents(window.localStorage.getItem(allEventsKey) === "1");
      const storedCalendarId = extractCalendarIdFromStoredValue(storedValue);
      if (storedCalendarId) {
        setVisibleCalendarIds([storedCalendarId]);
      } else if (storedValue) {
        window.localStorage.removeItem(storageKey);
      }
    }
    setHasLoadedCalendarFilter(true);
  }, [projectId]);

  useEffect(() => {
    if (!hasLoadedCalendarFilter) {
      return;
    }
    const nextIds = resolveVisibleCalendarSelection(
      projectId,
      accessibleCalendars,
      visibleCalendarIds,
    );
    if (nextIds) {
      setVisibleCalendarIds(nextIds);
    }
  }, [projectId, hasLoadedCalendarFilter, accessibleCalendars, visibleCalendarIds]);

  useEffect(() => {
    if (!hasLoadedCalendarFilter || typeof window === "undefined") {
      return;
    }
    const storageKey = calendarFilterStorageKey(projectId);
    const allEventsKey = calendarAllEventsStorageKey(projectId);
    if (includeAllEvents) {
      window.localStorage.setItem(allEventsKey, "1");
    } else {
      window.localStorage.removeItem(allEventsKey);
    }
    if (visibleCalendarIds && visibleCalendarIds.length === 1) {
      window.localStorage.setItem(storageKey, visibleCalendarIds[0]);
      return;
    }
    window.localStorage.removeItem(storageKey);
  }, [hasLoadedCalendarFilter, visibleCalendarIds, includeAllEvents, projectId]);

  const headerTitle = useMemo(() => {
    if (currentView === "year") {
      return format(currentDate, "yyyy");
    }
    if (currentView === "month" || currentView === "agenda") {
      return format(currentDate, "MMMM yyyy");
    }
    if (currentView === "week") {
      const start = startOfWeek(currentDate, { weekStartsOn: 1 });
      const end = addDays(start, 6);
      const sameMonth = start.getMonth() === end.getMonth();
      const sameYear = start.getFullYear() === end.getFullYear();

      if (sameMonth && sameYear) {
        return `${format(start, "MMMM d")} - ${format(end, "d, yyyy")}`;
      }
      if (sameYear) {
        return `${format(start, "MMM d")} - ${format(end, "MMM d, yyyy")}`;
      }
      return `${format(start, "MMM d, yyyy")} - ${format(end, "MMM d, yyyy")}`;
    }
    return format(currentDate, "EEEE, MMMM d, yyyy");
  }, [currentView, currentDate]);

  const handleRecurringDragScope = useCallback(
    async (scope: RecurringEditScope) => {
      const pending = recurringDrag;
      setRecurringDrag(null);
      if (!pending) {
        return;
      }

      const { event, start, end } = pending;
      const payload = {
        start_datetime: start.toISOString(),
        end_datetime: end.toISOString(),
        timezone: event.timezone,
        calendar_id: event.calendar_id,
      };
      // Identify the occurrence by its original start, not the dragged target.
      const originalStart = event.original_start ?? event.start_datetime;

      try {
        if (scope === "all") {
          await CalendarAPI.updateEvent(event.id, payload, event.etag);
        } else if (scope === "future") {
          await CalendarAPI.splitEventSeries(event.id, originalStart, payload);
        } else {
          await CalendarAPI.updateEventInstance(event.id, originalStart, payload);
        }
        await refetch();
      } catch {
        toast.error("Failed to update event time");
      }
    },
    [recurringDrag, refetch],
  );

  const handleToday = () => {
    setCurrentDate(new Date());
  };

  const handleOffset = useCallback((direction: "prev" | "next") => {
    const multiplier = direction === "next" ? 1 : -1;

    if (currentView === "day") {
      setCurrentDate((prev) => addDays(prev, 1 * multiplier));
    } else if (currentView === "week") {
      setCurrentDate((prev) => addDays(prev, 7 * multiplier));
    } else if (currentView === "month") {
      const next = new Date(currentDate);
      next.setMonth(next.getMonth() + 1 * multiplier);
      setCurrentDate(next);
    } else if (currentView === "year") {
      const next = new Date(currentDate);
      next.setFullYear(next.getFullYear() + 1 * multiplier);
      setCurrentDate(next);
    } else {
      setCurrentDate((prev) => addDays(prev, 7 * multiplier));
    }
  }, [currentDate, currentView]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (!target) return;

      const tag = target.tagName.toLowerCase();
      const isTypingElement =
        tag === "input" ||
        tag === "textarea" ||
        target.getAttribute("contenteditable") === "true";
      if (isTypingElement) return;

      if (event.key === "t" || event.key === "T") {
        event.preventDefault();
        handleToday();
        return;
      }
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        handleOffset("prev");
        return;
      }
      if (event.key === "ArrowRight") {
        event.preventDefault();
        handleOffset("next");
        return;
      }
      if (event.key === "d" || event.key === "D") {
        event.preventDefault();
        setCurrentView("day");
        return;
      }
      if (event.key === "w" || event.key === "W") {
        event.preventDefault();
        setCurrentView("week");
        return;
      }
      if (event.key === "m" || event.key === "M") {
        event.preventDefault();
        setCurrentView("month");
        return;
      }
      if (event.key === "y" || event.key === "Y") {
        event.preventDefault();
        setCurrentView("year");
        return;
      }
      if (event.key === "a" || event.key === "A") {
        event.preventDefault();
        setCurrentView("agenda");
        return;
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [handleOffset]);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (
        viewSwitcherRef.current &&
        !viewSwitcherRef.current.contains(event.target as Node)
      ) {
        setViewSwitcherOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  return (
    <div className="flex-1 flex flex-col bg-white min-h-0 overflow-hidden" data-testid="calendar-root">
      <CalendarToolbar
        headerTitle={headerTitle}
        currentView={currentView}
        viewSwitcherOpen={viewSwitcherOpen}
        viewSwitcherRef={viewSwitcherRef}
        onToggleViewSwitcher={() => setViewSwitcherOpen((o) => !o)}
        onSelectView={(view) => {
          setCurrentView(view);
          setViewSwitcherOpen(false);
        }}
        onToday={handleToday}
        onOffset={handleOffset}
        onAskAgent={handleAskAgentFromCalendar}
        showAllEvents={showAllEvents}
        onShowAllEventsChange={isPersonalSelected ? setIncludeAllEvents : undefined}
      />

      {gcalStatus?.connected && (gcalStatus.needs_reconnect || gcalStatus.last_error_message) ? (
        <div className="mx-3 mt-2 flex shrink-0 flex-col gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-950 sm:mx-4 sm:flex-row sm:items-center sm:justify-between sm:gap-3 sm:px-4">
          <span className="min-w-0">
            {gcalStatus.last_error_message ||
              "Google Calendar authorization expired or was revoked. Reconnect in Integrations."}
          </span>
          <button
            type="button"
            onClick={() => router.push(buildUrl("/integrations"))}
            className="shrink-0 rounded-md bg-amber-700 px-3 py-1 text-xs font-medium text-white hover:bg-amber-800"
          >
            Open Integrations
          </button>
        </div>
      ) : null}

      {gcalStatus?.connected && !gcalStatus.needs_reconnect && !gcalStatus.last_error_message ? (
        <div className="mx-3 mt-2 flex shrink-0 flex-wrap items-center gap-2 sm:mx-4">
          <GoogleCalendarConnectedBadge googleEmail={gcalStatus.google_email} />
          <button
            type="button"
            onClick={handleGcalSync}
            disabled={gcalSyncing}
            className="inline-flex items-center gap-1.5 rounded-full border border-gray-400 bg-white px-4 py-1.5 text-sm font-medium text-gray-700 shadow-sm hover:bg-gray-50 disabled:opacity-50"
          >
            {gcalSyncing ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                Syncing…
              </>
            ) : (
              <>
                <RefreshCw className="h-3.5 w-3.5" aria-hidden />
                Sync with Google
              </>
            )}
          </button>
        </div>
      ) : null}

      <div className="flex min-h-0 flex-1 overflow-hidden">
        <CalendarSidebarContainer
          currentDate={currentDate}
          projectId={projectId}
          onVisibleCalendarsChange={handleVisibleCalendarsChange}
          onDateChange={setCurrentDate}
          selectedCalendarId={selectedCalendarId}
          activeEventTypes={activeEventTypes}
          onToggleActivityType={toggleActivityType}
        />

        <section className="min-w-0 flex-1 overflow-auto bg-white" data-testid="calendar-canvas">
          <CalendarViewRouter
            currentView={currentView}
            currentDate={currentDate}
            events={events}
            calendars={calendars}
            isLoading={isLoading}
            error={error}
            onTimeSlotClick={(start, position) => {
              const end = new Date(start);
              end.setHours(start.getHours() + 1);
              setDialogMode("create");
              setEditingEvent(null);
              setDialogStart(start);
              setDialogEnd(end);
              setPanelPosition(position);
              setIsDialogOpen(true);
            }}
            onEventClick={(event, position) => {
              const meta = extractNavigationMetadata(event.description || "");
              if (meta && meta.isDerived) {
                if (meta.decision_slug || meta.decision_id) {
                  router.push(buildUrl(`/decisions/${meta.decision_slug ?? meta.decision_id}`));
                  return;
                }
                if (meta.task_slug || meta.task_id) {
                  router.push(buildUrl(`/tasks/${meta.task_slug ?? meta.task_id}`));
                  return;
                }
              }
              setDialogMode("view");
              setEditingEvent(event);
              setDialogStart(new Date(event.start_datetime));
              setDialogEnd(new Date(event.end_datetime));
              setPanelPosition(position);
              setIsDialogOpen(true);
            }}
            onEventTimeChange={async (event, start, end) => {
              if (event.id.toString().startsWith("derived-")) {
                return;
              }
              // Recurring drags need a scope choice before we touch the series.
              if (event.is_recurring) {
                setRecurringDrag({ event, start, end });
                return;
              }
              try {
                await CalendarAPI.updateEvent(
                  event.id,
                  {
                    start_datetime: start.toISOString(),
                    end_datetime: end.toISOString(),
                    timezone: event.timezone,
                    calendar_id: event.calendar_id,
                  },
                  event.etag,
                );
                await refetch();
              } catch {
                toast.error("Failed to update event time");
              }
            }}
            onDaySelect={(day) => {
              setCurrentDate(day);
              setCurrentView("day");
            }}
          />

          {currentView !== "week" &&
            currentView !== "day" &&
            currentView !== "month" &&
            currentView !== "agenda" &&
            currentView !== "year" && (
            <div className="flex h-full flex-col items-center justify-center gap-3 text-gray-500">
              <List className="h-6 w-6" />
              <p className="text-sm">
                {VIEW_LABELS[currentView]} view layout will be implemented in
                later steps.
              </p>
            </div>
          )}
        </section>

        <EventDialogContainer
          key={editingEvent?.id ?? (isDialogOpen ? "create" : "closed")}
          open={isDialogOpen}
          mode={dialogMode}
          onModeChange={setDialogMode}
          onOpenChange={(open) => {
            setIsDialogOpen(open);
            if (!open) {
              setPanelPosition(null);
            }
          }}
          start={dialogStart}
          end={dialogEnd}
          event={editingEvent}
          calendars={calendars}
          primaryCalendar={primaryCalendar}
          preferredCalendarId={selectedCalendarId ?? primaryCalendar?.id ?? null}
          position={panelPosition}
          onAskAgent={handleAskAgentFromEvent}
          onSave={async (payload) => {
            try {
              await payload.action();
              await refetch();
              setIsDialogOpen(false);
            } catch (err: any) {
              toast.error("Failed to save event");
            }
          }}
          onDelete={async (eventToDelete) => {
            try {
              await CalendarAPI.deleteEvent(eventToDelete.id, eventToDelete.etag);
              await refetch();
              setIsDialogOpen(false);
              toast.success(
                isBookingEvent(eventToDelete)
                  ? "Meeting cancelled."
                  : "Event deleted.",
              );
            } catch (err: any) {
              toast.error(
                isBookingEvent(eventToDelete)
                  ? "Could not cancel this meeting."
                  : "Failed to delete event",
              );
            }
          }}
        />

        <RecurringEditScopeDialog
          open={recurringDrag !== null}
          title="Change recurring event"
          onCancel={() => setRecurringDrag(null)}
          onConfirm={handleRecurringDragScope}
        />
      </div>
    </div>
  );
}
