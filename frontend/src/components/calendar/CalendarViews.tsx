import React, { useMemo } from "react";
import {
  addDays,
  endOfMonth,
  endOfYear,
  format,
  isSameDay,
  isSameMonth,
  startOfDay,
  startOfMonth,
  startOfWeek,
  startOfYear,
} from "date-fns";
import type { CalendarDTO, EventDTO, CalendarViewType } from "@/lib/api/calendarApi";
import type { EventPanelPosition } from "@/components/calendar/types";
import { isBookingEvent } from "@/lib/bookingEvent";
import { computePanelPosition } from "@/components/calendar/utils";
import {
  layoutOverlappingEvents,
  overlapColumnStyle,
} from "@/components/calendar/overlapLayout";

const WEEKDAY_LABELS = [
  { key: "mon", label: "M" },
  { key: "tue", label: "T" },
  { key: "wed", label: "W" },
  { key: "thu", label: "T" },
  { key: "fri", label: "F" },
  { key: "sat", label: "S" },
  { key: "sun", label: "S" },
] as const;

function parseValidDate(value: unknown): Date | null {
  if (typeof value !== "string" || !value.trim()) {
    return null;
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export type WeekViewProps = {
  currentDate: Date;
  events: EventDTO[];
  calendars: CalendarDTO[];
  isLoading: boolean;
  error: Error | null;
  onTimeSlotClick: (start: Date, position: EventPanelPosition) => void;
  onEventClick: (event: EventDTO, position: EventPanelPosition) => void;
  onEventTimeChange: (event: EventDTO, start: Date, end: Date) => Promise<void>;
};

export function WeekView({
  currentDate,
  events,
  calendars,
  isLoading,
  error,
  onTimeSlotClick,
  onEventClick,
  onEventTimeChange,
}: WeekViewProps) {
  const start = startOfWeek(currentDate, { weekStartsOn: 1 });
  const days = useMemo(
    () => Array.from({ length: 7 }, (_, index) => addDays(start, index)),
    [start],
  );
  const hours = useMemo(
    () => Array.from({ length: 24 }, (_, index) => index),
    [],
  );

  const [dragState, setDragState] = React.useState<{
    eventId: string;
    mode: "move" | "resize";
    originY: number;
    originX: number;
    originalStart: Date;
    originalEnd: Date;
  } | null>(null);
  const [previewTimes, setPreviewTimes] = React.useState<
    Record<string, { start: Date; end: Date }>
  >({});
  const [suppressClick, setSuppressClick] = React.useState(false);

  const calendarColorById = useMemo(() => {
    const map = new Map<string, string>();
    calendars.forEach((cal) => map.set(cal.id, cal.color));
    return map;
  }, [calendars]);
  const eventById = useMemo(() => {
    const map = new Map<string, EventDTO>();
    events.forEach((ev) => map.set(ev.id, ev));
    return map;
  }, [events]);

  const handleMouseMove: React.MouseEventHandler<HTMLDivElement> = (e) => {
    if (!dragState) return;
    if ((e.buttons & 1) === 0) return;

    const pixelsPerMinute = 48 / 60;
    const stepMinutes = 30;
    const deltaY = e.clientY - dragState.originY;
    const rawMinutes = deltaY / pixelsPerMinute;
    const snappedMinutes = Math.round(rawMinutes / stepMinutes) * stepMinutes;

    let dayOffset = 0;
    if (dragState.mode === "move") {
      const gridElement = e.currentTarget as HTMLDivElement;
      const rect = gridElement.getBoundingClientRect();
      const totalWidth = rect.width - 60;
      const dayWidth = totalWidth / 7;
      const deltaX = e.clientX - dragState.originX;
      dayOffset = Math.round(deltaX / dayWidth);
    }

    if (snappedMinutes !== 0 || dayOffset !== 0) {
      setSuppressClick(true);
    }

    const totalMinutesOffset = snappedMinutes + dayOffset * 24 * 60;
    const newStart =
      dragState.mode === "move"
        ? new Date(dragState.originalStart.getTime() + totalMinutesOffset * 60000)
        : new Date(dragState.originalStart);
    let newEnd: Date;

    if (dragState.mode === "move") {
      newEnd = new Date(
        dragState.originalEnd.getTime() + totalMinutesOffset * 60000,
      );
    } else {
      const minDurationMinutes = 15;
      const candidateEnd = new Date(
        dragState.originalEnd.getTime() + snappedMinutes * 60000,
      );
      if (
        candidateEnd.getTime() - dragState.originalStart.getTime() <
        minDurationMinutes * 60000
      ) {
        newEnd = new Date(
          dragState.originalStart.getTime() + minDurationMinutes * 60000,
        );
      } else {
        newEnd = candidateEnd;
      }
    }

    setPreviewTimes((prev) => ({
      ...prev,
      [dragState.eventId]: { start: newStart, end: newEnd },
    }));
  };

  const finishDrag = async () => {
    if (!dragState) return;
    const preview = previewTimes[dragState.eventId];
    const baseEvent = eventById.get(dragState.eventId);
    setDragState(null);
    setPreviewTimes((prev) => {
      const next = { ...prev };
      delete next[dragState.eventId];
      return next;
    });
    if (!preview || !baseEvent) return;
    await onEventTimeChange(baseEvent, preview.start, preview.end);
  };

  return (
    <div
      className="flex h-full min-w-[680px] flex-col rounded-none bg-white shadow-sm sm:rounded-xl lg:min-w-0 lg:rounded-3xl"
      data-testid="calendar-week-view"
      aria-label="Week calendar view"
    >
      <div className="relative z-20 grid grid-cols-[60px_repeat(7,minmax(0,1fr))] bg-white text-xs font-medium text-gray-500">
        <div className="relative px-2 py-2 text-gray-400 before:absolute before:bottom-0 before:right-0 before:block before:h-px before:w-3 before:bg-gray-100 before:content-[''] after:absolute after:bottom-0 after:right-0 after:block after:h-4 after:w-px after:bg-gray-100 after:content-['']" />
        {days.map((day) => (
          <div
            key={day.toISOString()}
            className="relative flex flex-col items-center px-2 py-2 last:border-r-0 border-b after:absolute after:bottom-0 after:right-0 after:block after:h-4 after:w-px after:bg-gray-100 after:content-['']"
          >
            <span>{format(day, "EEE")}</span>
            <span className="mt-1 rounded-full py-0.5 text-sm font-semibold text-gray-800">
              {format(day, "d")}
            </span>
          </div>
        ))}
      </div>

      <div
        className="relative z-0 grid flex-1 grid-cols-[60px_repeat(7,minmax(0,1fr))] overflow-auto text-xs"
        onMouseMove={handleMouseMove}
        onMouseUp={() => void finishDrag()}
        onMouseLeave={() => void finishDrag()}
      >
        <div className="border-r bg-white flex flex-col">
          {hours.map((hour) => (
            <div
              key={hour}
              className="relative h-12 border-gray-100 px-2 text-[11px] text-gray-400 before:absolute before:bottom-0 before:right-0 before:block before:h-px before:w-3 before:bg-gray-100 before:content-['']"
            >
              <span
                className={`absolute right-3 z-10 text-right ${
                  hour === 0 ? "top-1" : "top-0 -translate-y-1/2"
                }`}
              >
                {format(new Date().setHours(hour, 0, 0, 0), "ha")}
              </span>
            </div>
          ))}
        </div>

        {days.map((day) => {
          const dayStart = startOfDay(day);
          const dayEnd = addDays(dayStart, 1);
          const dayEvents = events.filter((event) => {
            const evStart = new Date(event.start_datetime);
            const evEnd = new Date(event.end_datetime);
            return evEnd > dayStart && evStart < dayEnd;
          });

          return (
            <div key={day.toISOString()} className="relative border-r last:border-r-0">
              {hours.map((hour) => {
                const slotStart = new Date(dayStart);
                slotStart.setHours(hour, 0, 0, 0);
                return (
                  <button
                    key={hour}
                    type="button"
                    data-testid="calendar-week-slot"
                    aria-label={`Create event on ${format(
                      slotStart,
                      "EEEE, MMMM d 'at' h:mm a",
                    )}`}
                    className="h-12 w-full border-b border-gray-100 bg-white text-left hover:bg-[#3CCED7]/5 flex flex-col"
                    onClick={(e) => {
                      const rect = e.currentTarget.getBoundingClientRect();
                      const position = computePanelPosition(rect);
                      onTimeSlotClick(slotStart, position);
                    }}
                  />
                );
              })}

              {(() => {
                const positioned = dayEvents.map((event) => {
                  const preview = previewTimes[event.id];
                  const evStart = preview ? preview.start : new Date(event.start_datetime);
                  const evEnd = preview ? preview.end : new Date(event.end_datetime);
                  const clampedStart = evStart < dayStart ? dayStart : evStart;
                  const clampedEnd = evEnd > dayEnd ? dayEnd : evEnd;
                  const startMinutes =
                    (clampedStart.getTime() - dayStart.getTime()) / 60000;
                  const durationMinutes =
                    (clampedEnd.getTime() - clampedStart.getTime()) / 60000 || 30;
                  const pixelsPerMinute = 48 / 60;
                  return {
                    event,
                    evStart,
                    evEnd,
                    topPx: startMinutes * pixelsPerMinute,
                    heightPx: durationMinutes * pixelsPerMinute,
                    startMs: clampedStart.getTime(),
                    endMs: clampedEnd.getTime(),
                  };
                });
                const placements = layoutOverlappingEvents(
                  positioned.map((item) => ({
                    id: item.event.id,
                    startMs: item.startMs,
                    endMs: item.endMs,
                  })),
                );
                return positioned.map((item) => {
                  const { event, evStart, evEnd, topPx, heightPx } = item;
                  const backgroundColor =
                    event.color || calendarColorById.get(event.calendar_id || "") || "#1E88E5";
                  const columnStyle = overlapColumnStyle(placements.get(event.id));

                return (
                  <button
                    key={event.id + event.start_datetime}
                    type="button"
                    data-testid="calendar-event-card"
                    data-booking={isBookingEvent(event) ? "true" : "false"}
                    aria-label={`Open event ${event.title || "(No title)"}`}
                    onClick={(e) => {
                      if (suppressClick) {
                        e.preventDefault();
                        setSuppressClick(false);
                        return;
                      }
                      const rect = e.currentTarget.getBoundingClientRect();
                      const position = computePanelPosition(rect);
                      onEventClick(event, position);
                    }}
                    onMouseDown={(e) => {
                      const target = e.target as HTMLElement;
                      if (target.closest("[data-resize-handle='true']")) return;
                      if (event.is_recurring || e.button !== 0) return;
                      e.preventDefault();
                      e.stopPropagation();
                      setDragState({
                        eventId: event.id,
                        mode: "move",
                        originY: e.clientY,
                        originX: e.clientX,
                        originalStart: new Date(event.start_datetime),
                        originalEnd: new Date(event.end_datetime),
                      });
                    }}
                    className="absolute rounded-md px-1.5 py-0.5 pb-2 text-[11px] text-gray-900"
                    style={{
                      top: `${topPx}px`,
                      height: `${heightPx}px`,
                      left: columnStyle.left,
                      width: columnStyle.width,
                      borderLeft: `3px solid ${backgroundColor}`,
                      backgroundColor: `color-mix(in srgb, ${backgroundColor} 15%, white)`,
                    }}
                  >
                    <div className="truncate font-semibold">{event.title}</div>
                    <div className="truncate text-gray-600">
                      {format(evStart, "HH:mm")} - {format(evEnd, "HH:mm")}
                    </div>
                    {!event.is_recurring && (
                      <div
                        data-resize-handle="true"
                        className="absolute bottom-1 left-1/2 h-1 w-[80%] -translate-x-1/2 cursor-row-resize rounded-full bg-gray-300"
                        onMouseDown={(e) => {
                          if (e.button !== 0) return;
                          e.preventDefault();
                          e.stopPropagation();
                          setDragState({
                            eventId: event.id,
                            mode: "resize",
                            originY: e.clientY,
                            originX: e.clientX,
                            originalStart: new Date(event.start_datetime),
                            originalEnd: new Date(event.end_datetime),
                          });
                        }}
                      />
                    )}
                  </button>
                );
                });
              })()}

              {isLoading && <div className="pointer-events-none absolute inset-0 bg-white/40" />}
              {error && (
                <div className="pointer-events-none absolute inset-x-2 top-2 rounded bg-red-50 px-2 py-1 text-[10px] text-red-600">
                  Failed to load events.
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export type DayViewProps = WeekViewProps;

export function DayView({
  currentDate,
  events,
  calendars,
  isLoading,
  error,
  onTimeSlotClick,
  onEventClick,
  onEventTimeChange,
}: DayViewProps) {
  const dayStart = startOfDay(currentDate);
  const dayEnd = addDays(dayStart, 1);
  const hours = useMemo(() => Array.from({ length: 24 }, (_, index) => index), []);
  const [dragState, setDragState] = React.useState<{
    eventId: string;
    mode: "move" | "resize";
    originY: number;
    originalStart: Date;
    originalEnd: Date;
  } | null>(null);
  const [previewTimes, setPreviewTimes] = React.useState<
    Record<string, { start: Date; end: Date }>
  >({});
  const [suppressClick, setSuppressClick] = React.useState(false);

  const dayEvents = events.filter((event) => {
    const evStart = new Date(event.start_datetime);
    const evEnd = new Date(event.end_datetime);
    return evEnd > dayStart && evStart < dayEnd;
  });
  const calendarColorById = useMemo(() => {
    const map = new Map<string, string>();
    calendars.forEach((cal) => map.set(cal.id, cal.color));
    return map;
  }, [calendars]);
  const eventById = useMemo(() => {
    const map = new Map<string, EventDTO>();
    events.forEach((ev) => map.set(ev.id, ev));
    return map;
  }, [events]);

  const handleMouseMove: React.MouseEventHandler<HTMLDivElement> = (e) => {
    if (!dragState) return;
    if ((e.buttons & 1) === 0) return;
    const pixelsPerMinute = 48 / 60;
    const stepMinutes = 30;
    const deltaY = e.clientY - dragState.originY;
    const snappedMinutes = Math.round((deltaY / pixelsPerMinute) / stepMinutes) * stepMinutes;
    if (snappedMinutes !== 0) setSuppressClick(true);

    const newStart =
      dragState.mode === "move"
        ? new Date(dragState.originalStart.getTime() + snappedMinutes * 60000)
        : new Date(dragState.originalStart);
    let newEnd: Date;
    if (dragState.mode === "move") {
      newEnd = new Date(dragState.originalEnd.getTime() + snappedMinutes * 60000);
    } else {
      const minDurationMinutes = 15;
      const candidateEnd = new Date(dragState.originalEnd.getTime() + snappedMinutes * 60000);
      if (candidateEnd.getTime() - dragState.originalStart.getTime() < minDurationMinutes * 60000) {
        newEnd = new Date(dragState.originalStart.getTime() + minDurationMinutes * 60000);
      } else {
        newEnd = candidateEnd;
      }
    }
    setPreviewTimes((prev) => ({ ...prev, [dragState.eventId]: { start: newStart, end: newEnd } }));
  };

  const finishDrag = async () => {
    if (!dragState) return;
    const preview = previewTimes[dragState.eventId];
    const baseEvent = eventById.get(dragState.eventId);
    setDragState(null);
    setPreviewTimes((prev) => {
      const next = { ...prev };
      delete next[dragState.eventId];
      return next;
    });
    if (!preview || !baseEvent) return;
    await onEventTimeChange(baseEvent, preview.start, preview.end);
  };

  return (
    <div
      className="flex h-full min-w-[320px] flex-col rounded-none bg-white sm:rounded-xl"
      data-testid="calendar-day-view"
      aria-label="Day calendar view"
    >
      <div className="relative z-20 grid grid-cols-[60px_minmax(0,1fr)] bg-white text-xs font-medium text-gray-500">
        <div className="relative px-2 py-2 text-gray-400 before:absolute before:bottom-0 before:right-0 before:block before:h-px before:w-3 before:bg-gray-100 before:content-[''] after:absolute after:bottom-0 after:right-0 after:block after:h-2 after:w-px after:bg-gray-100 after:content-['']" />
        <div className="flex flex-col px-2 py-2 border-b">
          <span>{format(currentDate, "EEE")}</span>
          <span className="mt-1 rounded-full text-sm font-semibold text-gray-800">
            {format(currentDate, "d")}
          </span>
        </div>
      </div>
      <div
        className="relative z-0 grid flex-1 grid-cols-[60px_minmax(0,1fr)] overflow-auto text-xs"
        onMouseMove={handleMouseMove}
        onMouseUp={() => void finishDrag()}
        onMouseLeave={() => void finishDrag()}
      >
        <div className="border-r bg-white flex flex-col">
          {hours.map((hour) => (
            <div
              key={hour}
              className="relative h-12 border-gray-100 px-2 text-[11px] text-gray-400 before:absolute before:bottom-0 before:right-0 before:block before:h-px before:w-3 before:bg-gray-100 before:content-['']"
            >
              <span
                className={`absolute right-3 z-10 text-right ${
                  hour === 0 ? "top-1" : "top-0 -translate-y-1/2"
                }`}
              >
                {format(new Date().setHours(hour, 0, 0, 0), "ha")}
              </span>
            </div>
          ))}
        </div>
        <div className="relative flex flex-col">
          {hours.map((hour) => {
            const slotStart = new Date(dayStart);
            slotStart.setHours(hour, 0, 0, 0);
            return (
              <button
                key={hour}
                type="button"
                data-testid="calendar-day-slot"
                aria-label={`Create event on ${format(
                  slotStart,
                  "EEEE, MMMM d 'at' h:mm a",
                )}`}
                className="h-12 w-full border-b border-gray-100 bg-white text-left hover:bg-[#3CCED7]/5"
                onClick={(e) => {
                  const rect = e.currentTarget.getBoundingClientRect();
                  const position = computePanelPosition(rect, "day");
                  onTimeSlotClick(slotStart, position);
                }}
              />
            );
          })}

          {(() => {
            const positioned = dayEvents.map((event) => {
              const preview = previewTimes[event.id];
              const evStart = preview ? preview.start : new Date(event.start_datetime);
              const evEnd = preview ? preview.end : new Date(event.end_datetime);
              const clampedStart = evStart < dayStart ? dayStart : evStart;
              const clampedEnd = evEnd > dayEnd ? dayEnd : evEnd;
              const startMinutes = (clampedStart.getTime() - dayStart.getTime()) / 60000;
              const durationMinutes = (clampedEnd.getTime() - clampedStart.getTime()) / 60000 || 30;
              return {
                event,
                evStart,
                evEnd,
                topPercent: (startMinutes / (24 * 60)) * 100,
                heightPercent: (durationMinutes / (24 * 60)) * 100,
                startMs: clampedStart.getTime(),
                endMs: clampedEnd.getTime(),
              };
            });
            const placements = layoutOverlappingEvents(
              positioned.map((item) => ({
                id: item.event.id,
                startMs: item.startMs,
                endMs: item.endMs,
              })),
            );
            return positioned.map((item) => {
              const { event, evStart, evEnd, topPercent, heightPercent } = item;
              const backgroundColor =
                event.color || calendarColorById.get(event.calendar_id || "") || "#1E88E5";
              const columnStyle = overlapColumnStyle(placements.get(event.id));

            return (
              <button
                key={event.id + event.start_datetime}
                type="button"
                data-testid="calendar-event-card"
                data-booking={isBookingEvent(event) ? "true" : "false"}
                aria-label={`Open event ${event.title || "(No title)"}`}
                onClick={(e) => {
                  if (suppressClick) {
                    e.preventDefault();
                    setSuppressClick(false);
                    return;
                  }
                  onEventClick(event, computePanelPosition(null, "day"));
                }}
                onMouseDown={(e) => {
                  const target = e.target as HTMLElement;
                  if (target.closest("[data-resize-handle='true']")) return;
                  if (event.is_recurring || e.button !== 0) return;
                  e.preventDefault();
                  e.stopPropagation();
                  setDragState({
                    eventId: event.id,
                    mode: "move",
                    originY: e.clientY,
                    originalStart: new Date(event.start_datetime),
                    originalEnd: new Date(event.end_datetime),
                  });
                }}
                className="absolute rounded-md px-1.5 py-0.5 pb-2 text-[11px] text-gray-900"
                style={{
                  top: `${topPercent}%`,
                  height: `${heightPercent}%`,
                  left: columnStyle.left,
                  width: columnStyle.width,
                  borderLeft: `3px solid ${backgroundColor}`,
                  backgroundColor: `color-mix(in srgb, ${backgroundColor} 15%, white)`,
                }}
              >
                <div className="truncate font-semibold">{event.title}</div>
                <div className="truncate text-gray-600">
                  {format(evStart, "HH:mm")} - {format(evEnd, "HH:mm")}
                </div>
                {!event.is_recurring && (
                  <div
                    data-resize-handle="true"
                    className="absolute bottom-1 left-1/2 h-1 w-[90%] -translate-x-1/2 cursor-row-resize rounded-full bg-gray-300"
                    onMouseDown={(e) => {
                      if (e.button !== 0) return;
                      e.preventDefault();
                      e.stopPropagation();
                      setDragState({
                        eventId: event.id,
                        mode: "resize",
                        originY: e.clientY,
                        originalStart: new Date(event.start_datetime),
                        originalEnd: new Date(event.end_datetime),
                      });
                    }}
                  />
                )}
              </button>
            );
            });
          })()}

          {isLoading && <div className="pointer-events-none absolute inset-0 bg-white/40" />}
          {error && (
            <div className="pointer-events-none absolute inset-x-2 top-2 rounded bg-red-50 px-2 py-1 text-[10px] text-red-600">
              Failed to load events.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export type MonthViewProps = {
  currentDate: Date;
  events: EventDTO[];
  calendars: CalendarDTO[];
  isLoading: boolean;
  error: Error | null;
  onDaySelect: (day: Date) => void;
  onEventClick: (event: EventDTO) => void;
};

export function MonthView({
  currentDate,
  events,
  calendars,
  isLoading,
  error,
  onDaySelect,
  onEventClick,
}: MonthViewProps) {
  const startMonth = startOfMonth(currentDate);
  const gridStart = startOfWeek(startMonth, { weekStartsOn: 1 });
  const days = useMemo(
    () => Array.from({ length: 42 }, (_, index) => addDays(gridStart, index)),
    [gridStart],
  );
  const calendarColorById = useMemo(() => {
    const map = new Map<string, string>();
    calendars.forEach((cal) => map.set(cal.id, cal.color));
    return map;
  }, [calendars]);
  const eventsByDay = useMemo(() => {
    const result = new Map<string, EventDTO[]>();
    days.forEach((day) => result.set(format(day, "yyyy-MM-dd"), []));
    events.forEach((event) => {
      const evStart = new Date(event.start_datetime);
      const evEnd = new Date(event.end_datetime);
      days.forEach((day) => {
        const dayStart = startOfDay(day);
        const dayEnd = addDays(dayStart, 1);
        if (evEnd > dayStart && evStart < dayEnd) {
          result.get(format(day, "yyyy-MM-dd"))?.push(event);
        }
      });
    });
    return result;
  }, [days, events]);

  const today = new Date();

  return (
    <div
      className="flex h-full min-w-[560px] flex-col rounded-none bg-white sm:rounded-xl lg:min-w-0"
      data-testid="calendar-month-view"
      aria-label="Month calendar view"
    >
      <div className="flex items-center justify-between px-3 py-2 text-sm font-semibold text-gray-700 sm:px-4">
        {isLoading && <span className="text-xs text-gray-400">Loading…</span>}
        {error && <span className="text-xs text-red-500">Failed to load events.</span>}
      </div>
      <div className="grid flex-1 grid-rows-[auto_1fr]">
        <div className="grid grid-cols-7 bg-white text-[11px] font-medium text-gray-500">
          {WEEKDAY_LABELS.map((item) => (
            <div key={item.key} className="flex items-center justify-center py-1">
              {item.label}
            </div>
          ))}
        </div>
        <div className="grid flex-1 grid-cols-7 grid-rows-6 text-xs">
          {days.map((day) => {
            const inMonth = isSameMonth(day, startMonth);
            const isSelected = isSameDay(day, currentDate);
            const isToday = isSameDay(day, today);
            const dayEvents = eventsByDay.get(format(day, "yyyy-MM-dd")) || [];
            return (
              <button
                key={day.toISOString()}
                type="button"
                className="flex h-full min-h-20 flex-col border-b border-r bg-white px-1.5 py-1"
                onClick={() => onDaySelect(day)}
              >
                <div className="mb-1 flex items-center justify-center text-[11px]">
                  <span
                    className={`inline-flex h-6 w-6 items-center justify-center rounded-full ${
                      isSelected
                        ? "bg-[#3CCED7] text-white"
                        : isToday
                        ? "border border-[#3CCED7] text-[#3CCED7]"
                        : inMonth
                        ? "text-gray-800"
                        : "text-gray-300"
                    }`}
                  >
                    {format(day, "d")}
                  </span>
                </div>
                <div className="space-y-0.5">
                  {dayEvents.slice(0, 3).map((event) => {
                    const color =
                      event.color || calendarColorById.get(event.calendar_id || "") || "#1E88E5";
                    return (
                      <div
                        key={event.id + event.start_datetime}
                        className="flex cursor-pointer items-center gap-1 truncate rounded px-1 py-0.5 text-[11px] text-gray-900 hover:bg-gray-100"
                        onClick={(e) => {
                          e.stopPropagation();
                          onEventClick(event);
                        }}
                      >
                        <span className="h-2 w-2 rounded-full" style={{ backgroundColor: color }} />
                        <span className="truncate">{event.title}</span>
                      </div>
                    );
                  })}
                  {dayEvents.length > 3 && (
                    <div className="text-[10px] text-gray-500">+{dayEvents.length - 3} more</div>
                  )}
                </div>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

export type AgendaViewProps = {
  currentDate: Date;
  events: EventDTO[];
  calendars: CalendarDTO[];
  isLoading: boolean;
  error: Error | null;
  onEventClick: (event: EventDTO, position: EventPanelPosition) => void;
};

export function AgendaView({
  currentDate,
  events,
  calendars,
  isLoading,
  error,
  onEventClick,
}: AgendaViewProps) {
  const calendarColorById = useMemo(() => {
    const map = new Map<string, string>();
    calendars.forEach((cal) => map.set(cal.id, cal.color));
    return map;
  }, [calendars]);
  const eventsByDate = useMemo(() => {
    const grouped = new Map<string, EventDTO[]>();
    [...events]
      .filter((event) => parseValidDate(event.start_datetime))
      .sort((a, b) => {
        const aTime = parseValidDate(a.start_datetime)?.getTime() ?? 0;
        const bTime = parseValidDate(b.start_datetime)?.getTime() ?? 0;
        return aTime - bTime;
      })
      .forEach((event) => {
        const start = parseValidDate(event.start_datetime);
        if (!start) {
          return;
        }
        const key = format(start, "yyyy-MM-dd");
        const bucket = grouped.get(key);
        if (bucket) {
          bucket.push(event);
        } else {
          grouped.set(key, [event]);
        }
      });
    return grouped;
  }, [events]);
  const dateKeys = Array.from(eventsByDate.keys());

  return (
    <div className="flex h-full flex-col rounded-none bg-white shadow-sm sm:rounded-xl">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b px-3 py-2 text-sm font-semibold text-gray-700 sm:px-4">
        <span className="min-w-0">
          {format(currentDate, "MMM d, yyyy")} – {format(addDays(currentDate, 7), "MMM d, yyyy")}
        </span>
        {isLoading && <span className="text-xs text-gray-400">Loading…</span>}
        {error && <span className="text-xs text-red-500">Failed to load events.</span>}
      </div>
      <div className="flex-1 overflow-auto">
        {dateKeys.length === 0 && !isLoading && !error && (
          <div className="flex h-full items-center justify-center text-sm text-gray-500">
            No events in this range.
          </div>
        )}
        {dateKeys.map((key) => {
          const day = new Date(key);
          const dayEvents = eventsByDate.get(key) || [];
          return (
            <div key={key} className="border-b px-3 py-3 text-sm sm:px-4">
              <div className="mb-2 font-semibold text-gray-800">{format(day, "EEE, MMM d")}</div>
              <ul className="space-y-1">
                {dayEvents.map((event) => {
                  const color =
                    event.color || calendarColorById.get(event.calendar_id || "") || "#1E88E5";
                  const start = parseValidDate(event.start_datetime);
                  const end = parseValidDate(event.end_datetime) ?? start;
                  if (!start || !end) {
                    return null;
                  }
                  return (
                    <li key={event.id + event.start_datetime}>
                      <button
                        type="button"
                        onClick={(e) => {
                          const rect = e.currentTarget.getBoundingClientRect();
                          const position = computePanelPosition(rect);
                          onEventClick(event, position);
                        }}
                        className="flex w-full items-center justify-between rounded px-2 py-1 text-left hover:bg-gray-50"
                      >
                        <div className="flex min-w-0 items-center gap-2">
                          <span className="h-2 w-2 rounded-full" style={{ backgroundColor: color }} />
                          <span className="shrink-0 text-xs text-gray-500">
                            {format(start, "HH:mm")} – {format(end, "HH:mm")}
                          </span>
                          <span className="truncate text-sm text-gray-900">{event.title}</span>
                        </div>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export type YearViewProps = {
  currentDate: Date;
  onDaySelect: (day: Date) => void;
};

export function YearView({ currentDate, onDaySelect }: YearViewProps) {
  const yearStart = startOfYear(currentDate);
  const yearEnd = endOfYear(currentDate);
  const months = useMemo(() => {
    void yearEnd;
    return Array.from({ length: 12 }, (_, index) => {
      const d = new Date(yearStart);
      d.setMonth(index, 1);
      return d;
    });
  }, [yearStart, yearEnd]);
  const today = new Date();

  return (
    <div className="flex h-full flex-col rounded-none bg-white sm:rounded-xl">
      <div className="grid flex-1 grid-cols-1 gap-3 overflow-auto p-3 text-[11px] sm:grid-cols-2 sm:gap-4 sm:p-4 xl:grid-cols-3">
        {months.map((monthDate) => {
          const startMonth = startOfMonth(monthDate);
          const gridStart = startOfWeek(startMonth, { weekStartsOn: 1 });
          const days = Array.from({ length: 42 }, (_, index) => addDays(gridStart, index));

          return (
            <div key={monthDate.toISOString()} className="rounded bg-white p-2">
              <div className="mb-1 text-center text-[11px] font-semibold text-gray-700">
                {format(monthDate, "MMMM")}
              </div>
              <div className="grid grid-cols-7 place-items-center gap-0.5 text-[10px] text-gray-400">
                {WEEKDAY_LABELS.map((item) => (
                  <div key={item.key} className="flex h-4 items-center justify-center">
                    {item.label}
                  </div>
                ))}
                {days.map((day) => {
                  const inMonth = isSameMonth(day, startMonth);
                  const isToday = isSameDay(day, today);
                  const className = `flex h-5 w-5 items-center justify-center rounded-full ${
                    !inMonth
                      ? "text-gray-300"
                      : isToday
                      ? "border border-[#3CCED7] bg-white text-[#3CCED7]"
                      : "text-gray-700 hover:bg-white"
                  }`;
                  return (
                    <button
                      key={day.toISOString()}
                      type="button"
                      className={className}
                      onClick={() => onDaySelect(day)}
                    >
                      {format(day, "d")}
                    </button>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export type MiniMonthCalendarProps = {
  currentDate: Date;
  onDateChange: (next: Date) => void;
};

export function MiniMonthCalendar({ currentDate, onDateChange }: MiniMonthCalendarProps) {
  const startMonth = startOfMonth(currentDate);
  const gridStart = startOfWeek(startMonth, { weekStartsOn: 1 });
  const days = useMemo(
    () => Array.from({ length: 42 }, (_, index) => addDays(gridStart, index)),
    [gridStart],
  );
  const today = new Date();

  return (
    <div className="mb-6 rounded-xl p-3">
      <div className="mb-2 flex items-center justify-between text-xs font-semibold text-gray-700">
        <span>{format(currentDate, "MMMM yyyy")}</span>
      </div>
      <div className="grid grid-cols-7 gap-1 text-[11px] text-gray-500">
        {WEEKDAY_LABELS.map((item) => (
          <div key={item.key} className="flex h-5 items-center justify-center">
            {item.label}
          </div>
        ))}
        {days.map((day) => {
          const inMonth = isSameMonth(day, startMonth);
          const isSelected = isSameDay(day, currentDate);
          const isToday = isSameDay(day, today);
          const className = `flex h-7 w-7 items-center justify-center rounded-full text-xs ${
            isSelected
              ? "bg-[#3CCED7]/20 text-[#3CCED7] font-semibold"
              : isToday
              ? "bg-[#3CCED7] text-white"
              : !inMonth
              ? "text-gray-300"
              : "text-gray-700 hover:bg-[#3CCED7]/10"
          }`;
          return (
            <button
              key={day.toISOString()}
              type="button"
              className={className}
              onClick={() => onDateChange(day)}
            >
              {format(day, "d")}
            </button>
          );
        })}
      </div>
    </div>
  );
}

type CalendarViewRouterProps = {
  currentView: CalendarViewType;
  currentDate: Date;
  events: EventDTO[];
  calendars: CalendarDTO[];
  isLoading: boolean;
  error: Error | null;
  onTimeSlotClick: (start: Date, position: EventPanelPosition) => void;
  onEventClick: (event: EventDTO, position: EventPanelPosition) => void;
  onEventTimeChange: (event: EventDTO, start: Date, end: Date) => Promise<void>;
  onDaySelect: (day: Date) => void;
};

export function CalendarViewRouter({
  currentView,
  currentDate,
  events,
  calendars,
  isLoading,
  error,
  onTimeSlotClick,
  onEventClick,
  onEventTimeChange,
  onDaySelect,
}: CalendarViewRouterProps) {
  if (currentView === "week") {
    return (
      <WeekView
        currentDate={currentDate}
        events={events}
        calendars={calendars}
        isLoading={isLoading}
        error={error}
        onTimeSlotClick={onTimeSlotClick}
        onEventClick={onEventClick}
        onEventTimeChange={onEventTimeChange}
      />
    );
  }
  if (currentView === "day") {
    return (
      <DayView
        currentDate={currentDate}
        events={events}
        calendars={calendars}
        isLoading={isLoading}
        error={error}
        onTimeSlotClick={onTimeSlotClick}
        onEventClick={onEventClick}
        onEventTimeChange={onEventTimeChange}
      />
    );
  }
  if (currentView === "month") {
    return (
      <MonthView
        currentDate={currentDate}
        events={events}
        calendars={calendars}
        isLoading={isLoading}
        error={error}
        onDaySelect={onDaySelect}
        onEventClick={(event) => onEventClick(event, { top: 120, left: 320 })}
      />
    );
  }
  if (currentView === "agenda") {
    return (
      <AgendaView
        currentDate={currentDate}
        events={events}
        calendars={calendars}
        isLoading={isLoading}
        error={error}
        onEventClick={onEventClick}
      />
    );
  }
  if (currentView === "year") {
    return <YearView currentDate={currentDate} onDaySelect={onDaySelect} />;
  }
  return null;
}
