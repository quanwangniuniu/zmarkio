import type { Meta, StoryObj } from "@storybook/react";
import React, { useMemo } from "react";
import { addDays, format, startOfWeek } from "date-fns";
import type { CalendarViewType } from "@/lib/api/calendarApi";
import { CalendarToolbar } from "@/components/calendar/CalendarToolbar";
import { CalendarSidebarPanel } from "@/components/calendar/CalendarSidebarPanel";
import { CalendarViewRouter } from "@/components/calendar/CalendarViews";
import { EventPanelDialog } from "@/components/calendar/EventPanelDialog";
import type { EventPanelPosition } from "@/components/calendar/types";
import type { EventDTO } from "@/lib/api/calendarApi";
import {
  getSampleCalendars,
  getSampleEvents,
  getSampleSidebarMyCalendars,
  getSampleSidebarOtherCalendars,
} from "@/stories/calendar/calendarData";

const meta: Meta = {
  title: "Calendar/CalendarPage",
  tags: ["autodocs"],
  parameters: {
    layout: "fullscreen",
  },
};

export default meta;
type Story = StoryObj;

type CalendarPageStoryProps = {
  isLoading?: boolean;
  error?: Error | null;
  sidebarLoading?: boolean;
  sidebarError?: Error | null;
};

function CalendarPageStory(props: CalendarPageStoryProps = {}) {
  const {
    isLoading = false,
    error = null,
    sidebarLoading = false,
    sidebarError = null,
  } = props;
  const [currentView, setCurrentView] = React.useState<CalendarViewType>("week");
  const [currentDate, setCurrentDate] = React.useState<Date>(() => new Date());
  const [viewSwitcherOpen, setViewSwitcherOpen] = React.useState(false);
  const [showAllEvents, setShowAllEvents] = React.useState(false);
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [dialogPos, setDialogPos] = React.useState<EventPanelPosition | null>(
    null,
  );
  const [dialogStart, setDialogStart] = React.useState<Date | null>(null);
  const [dialogEnd, setDialogEnd] = React.useState<Date | null>(null);
  const [dialogEvent, setDialogEvent] = React.useState<EventDTO | null>(null);
  const [events, setEvents] = React.useState<EventDTO[]>(() =>
    getSampleEvents(currentDate),
  );
  const ref = React.useRef<HTMLDivElement>(null);

  const calendars = getSampleCalendars();

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

  const handleOffset = React.useCallback(
    (direction: "prev" | "next") => {
      const multiplier = direction === "next" ? 1 : -1;
      if (currentView === "day") {
        setCurrentDate((prev) => addDays(prev, 1 * multiplier));
      } else if (currentView === "week") {
        setCurrentDate((prev) => addDays(prev, 7 * multiplier));
      } else if (currentView === "month") {
        setCurrentDate((prev) => {
          const next = new Date(prev);
          next.setMonth(next.getMonth() + 1 * multiplier);
          return next;
        });
      } else if (currentView === "year") {
        setCurrentDate((prev) => {
          const next = new Date(prev);
          next.setFullYear(next.getFullYear() + 1 * multiplier);
          return next;
        });
      } else {
        setCurrentDate((prev) => addDays(prev, 7 * multiplier));
      }
    },
    [currentView],
  );

  React.useEffect(() => {
    setEvents(getSampleEvents(currentDate));
  }, [currentDate]);

  React.useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (
        ref.current &&
        !ref.current.contains(event.target as Node)
      ) {
        setViewSwitcherOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  return (
    <div className="flex min-h-screen flex-col bg-[#f8fafd]">
      <CalendarToolbar
        headerTitle={headerTitle}
        currentView={currentView}
        viewSwitcherOpen={viewSwitcherOpen}
        viewSwitcherRef={ref}
        onToggleViewSwitcher={() => setViewSwitcherOpen((o) => !o)}
        onSelectView={(view) => {
          setCurrentView(view);
          setViewSwitcherOpen(false);
        }}
        onToday={() => setCurrentDate(new Date())}
        onOffset={handleOffset}
        showAllEvents={showAllEvents}
        onShowAllEventsChange={setShowAllEvents}
      />

      <div className="flex flex-1 overflow-hidden">
        <div className="[&_aside]:!block">
          <CalendarSidebarPanel
          currentDate={currentDate}
          onDateChange={setCurrentDate}
          selectedCalendarId={getSampleSidebarMyCalendars()[0].calendarId}
          myCalendars={getSampleSidebarMyCalendars()}
          otherCalendars={getSampleSidebarOtherCalendars()}
          isLoading={sidebarLoading}
          error={sidebarError}
          onCalendarItemClick={() => {}}
          activeEventTypes={new Set(["decision", "task"])}
          onToggleActivityType={() => {}}
          />
        </div>

        <section className="mb-4 flex-1 overflow-auto rounded-3xl bg-white">
          <CalendarViewRouter
            currentView={currentView}
            currentDate={currentDate}
            events={events}
            calendars={calendars}
            isLoading={isLoading}
            error={error}
            onTimeSlotClick={(start, position) => {
              setDialogStart(start);
              setDialogEnd(new Date(start.getTime() + 60 * 60 * 1000));
              setDialogPos(position);
              setDialogEvent(null);
              setDialogOpen(true);
            }}
            onEventClick={(event, position) => {
              setDialogStart(new Date(event.start_datetime));
              setDialogEnd(new Date(event.end_datetime));
              setDialogPos(position);
              setDialogEvent(event);
              setDialogOpen(true);
            }}
            onEventTimeChange={async (event, start, end) => {
              setEvents((prev) =>
                prev.map((e) =>
                  e.id === event.id
                    ? {
                        ...e,
                        start_datetime: start.toISOString(),
                        end_datetime: end.toISOString(),
                      }
                    : e,
                ),
              );
            }}
            onDaySelect={() => setCurrentView("day")}
          />
        </section>

        <EventPanelDialog
          open={dialogOpen}
          mode="view"
          onModeChange={() => {}}
          onOpenChange={setDialogOpen}
          start={dialogStart}
          end={dialogEnd}
          event={dialogEvent}
          calendars={calendars}
          primaryCalendar={calendars.find((c) => c.is_primary) ?? null}
          preferredCalendarId={calendars[0].id}
          onSave={async () => {}}
          onDelete={async () => {}}
          position={dialogPos}
        />
      </div>
    </div>
  );
}

export const FullPage: Story = {
  render: () => <CalendarPageStory />,
};

export const Loading: Story = {
  render: () => (
    <CalendarPageStory
      isLoading={true}
      error={null}
      sidebarLoading={true}
      sidebarError={null}
    />
  ),
};

export const ErrorState: Story = {
  render: () => (
    <CalendarPageStory
      isLoading={false}
      error={new Error("Failed to load events.")}
      sidebarLoading={false}
      sidebarError={new Error("Failed to load calendars. Please refresh the page.")}
    />
  ),
};
