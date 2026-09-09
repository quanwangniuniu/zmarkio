import React, { useCallback, useMemo } from "react";
import { format } from "date-fns";
import {
  AlignLeft,
  Calendar as CalendarIcon,
  Clock,
  Pencil,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import toast from "react-hot-toast";
import {
  CalendarAPI,
  extractNavigationMetadata,
  extractUserDescription,
} from "@/lib/api/calendarApi";
import type {
  CalendarDTO,
  EventDTO,
  RecurringEditScope,
} from "@/lib/api/calendarApi";
import type { CalendarDialogMode, EventPanelPosition } from "@/components/calendar/types";
import ConfirmDialog from "@/components/common/ConfirmDialog";
import { isBookingEvent } from "@/lib/bookingEvent";
import {
  RecurringEditScopeField,
} from "@/components/calendar/RecurringEditScopeDialog";
import {
  buildRecurrencePayload,
  DEFAULT_REPEAT_STATE,
  formatRepeatSummary,
  repeatStateFromEvent,
  repeatStatesEqual,
  type RepeatFormState,
} from "@/components/calendar/recurrenceUtils";

type EventPanelDialogProps = {
  open: boolean;
  mode: CalendarDialogMode;
  onModeChange: (mode: CalendarDialogMode) => void;
  onOpenChange: (open: boolean) => void;
  start: Date | null;
  end: Date | null;
  event: EventDTO | null;
  calendars: CalendarDTO[];
  primaryCalendar?: CalendarDTO | null;
  preferredCalendarId?: string | null;
  onSave: (payload: { action: () => Promise<void> }) => Promise<void>;
  onDelete?: (event: EventDTO) => Promise<void>;
  onAskAgent?: (event: EventDTO) => void;
  position: EventPanelPosition | null;
};

export function EventPanelDialog({
  open,
  mode,
  onModeChange,
  onOpenChange,
  start,
  end,
  event,
  calendars,
  primaryCalendar = null,
  preferredCalendarId,
  onSave,
  onDelete,
  onAskAgent,
  position,
}: EventPanelDialogProps) {
  const mergedCalendars = useMemo(() => {
    if (!primaryCalendar) {
      return calendars;
    }
    if (calendars.some((c) => c.id === primaryCalendar.id)) {
      return calendars;
    }
    return [primaryCalendar, ...calendars];
  }, [calendars, primaryCalendar]);

  const resolveDefaultCalendarId = useCallback(
    (eventCalendarId?: string | null) => {
      const availableIds = new Set(mergedCalendars.map((cal) => cal.id));
      if (eventCalendarId && availableIds.has(eventCalendarId)) {
        return eventCalendarId;
      }
      if (preferredCalendarId && availableIds.has(preferredCalendarId)) {
        return preferredCalendarId;
      }
      if (
        mode === "create" &&
        primaryCalendar?.id &&
        availableIds.has(primaryCalendar.id)
      ) {
        return primaryCalendar.id;
      }
      return mergedCalendars[0]?.id || "";
    },
    [mergedCalendars, mode, preferredCalendarId, primaryCalendar],
  );

  const [title, setTitle] = React.useState(event?.title ?? "");
  const [description, setDescription] = React.useState(event?.description ?? "");
  const [localStart, setLocalStart] = React.useState<Date | null>(start);
  const [localEnd, setLocalEnd] = React.useState<Date | null>(end);
  const [editScope, setEditScope] = React.useState<RecurringEditScope>("this");
  const pinnedOriginalStartRef = React.useRef<string | null>(null);
  const initialRepeatRef = React.useRef<RepeatFormState>({ ...DEFAULT_REPEAT_STATE });
  const [showMore, setShowMore] = React.useState(false);
  const [repeatState, setRepeatState] = React.useState<RepeatFormState>({
    ...DEFAULT_REPEAT_STATE,
  });
  const [calendarId, setCalendarId] = React.useState<string>(
    resolveDefaultCalendarId(event?.calendar_id),
  );
  const [confirmCancelMeeting, setConfirmCancelMeeting] = React.useState(false);
  const formSeedRef = React.useRef<string | null>(null);
  const formSeedKey = [
    mode,
    event?.id ?? "create",
    start?.toISOString() ?? "",
    end?.toISOString() ?? "",
  ].join("|");

  React.useEffect(() => {
    const defaultCalendarId = resolveDefaultCalendarId(event?.calendar_id);

    // Reset only when the dialog target changes; preserve user input during
    // late calendar hydration.
    if (formSeedRef.current !== formSeedKey) {
      formSeedRef.current = formSeedKey;
      setTitle(event?.title ?? "");
      setDescription(event?.description ?? "");
      setCalendarId(defaultCalendarId);
      setLocalStart(start);
      setLocalEnd(end);
      const nextRepeat = repeatStateFromEvent(
        Boolean(event?.is_recurring),
        event?.recurrence_rule,
      );
      setRepeatState(nextRepeat);
      initialRepeatRef.current = nextRepeat;
      setEditScope("this");
      pinnedOriginalStartRef.current =
        event?.original_start ?? event?.start_datetime ?? null;
      setShowMore(false);
      setConfirmCancelMeeting(false);
      return;
    }

    setCalendarId((currentCalendarId) => {
      if (currentCalendarId && mergedCalendars.some((cal) => cal.id === currentCalendarId)) {
        return currentCalendarId;
      }
      return defaultCalendarId;
    });
  }, [
    mergedCalendars,
    end,
    event,
    formSeedKey,
    mode,
    resolveDefaultCalendarId,
    start,
  ]);

  React.useEffect(() => {
    if (mode === "edit" && event?.is_recurring && editScope !== "all") {
      setShowMore(false);
    }
  }, [editScope, event?.is_recurring, mode]);

  const canShowMoreOptions =
    mode === "create" || (mode === "edit" && !event?.is_recurring) ||
    (mode === "edit" && event?.is_recurring && editScope === "all");

  const recurrenceChanged =
    mode === "edit" &&
    event?.is_recurring &&
    !repeatStatesEqual(repeatState, initialRepeatRef.current);

  if (!open || !localStart || !localEnd || !position) {
    return null;
  }

  const backdrop = (
    <div
      className="fixed inset-0 z-40"
      aria-hidden
      onClick={() => onOpenChange(false)}
    />
  );

  const isDerivedEvent = Boolean(
    extractNavigationMetadata(event?.description || "")?.isDerived,
  );
  const bookingEvent = isBookingEvent(event);
  const canRemoveEvent = Boolean(onDelete) && !isDerivedEvent;

  const requestRemoveEvent = () => {
    setConfirmCancelMeeting(true);
  };

  const confirmRemoveEvent = async () => {
    if (!event || !onDelete) {
      return;
    }
    setConfirmCancelMeeting(false);
    await onDelete(event);
  };

  const removeConfirmDialog = canRemoveEvent ? (
    <ConfirmDialog
      isOpen={confirmCancelMeeting}
      type="danger"
      title={bookingEvent ? "Cancel this meeting?" : "Delete this event?"}
      message={
        bookingEvent
          ? "The other person will be told this slot is no longer held, and the time will become available again."
          : "This event will be removed from the calendar."
      }
      confirmText={bookingEvent ? "Cancel meeting" : "Delete"}
      cancelText={bookingEvent ? "Keep meeting" : "Keep event"}
      onConfirm={() => void confirmRemoveEvent()}
      onCancel={() => setConfirmCancelMeeting(false)}
    />
  ) : null;

  if (mode === "view" && event) {
    const calendarName =
      mergedCalendars.find((c) => c.id === event.calendar_id)?.name || "Calendar";
    const color =
      event.color ||
      mergedCalendars.find((c) => c.id === event.calendar_id)?.color ||
      "#1E88E5";

    return (
      <>
        {backdrop}
        {removeConfirmDialog}
        <div
          role="dialog"
          aria-label="View event"
          data-testid="calendar-event-dialog"
          className="fixed z-50 max-h-[calc(100dvh-2rem)] w-[calc(100vw-2rem)] max-w-[360px] overflow-y-auto rounded-2xl border bg-white shadow-xl animate-in slide-in-from-bottom-8 fade-in duration-300 sm:rounded-3xl"
          style={{ top: position.top, left: position.left }}
          onClick={(e) => e.stopPropagation()}
        >
          <div className="flex items-center justify-between px-4 pt-3">
            <div className="flex items-center gap-2">
              <span
                className="inline-block h-3 w-3 rounded-full"
                style={{ backgroundColor: color }}
              />
              <span className="text-sm font-semibold text-gray-900">
                {event.title || "(No title)"}
              </span>
            </div>
            <div className="flex items-center gap-2 text-gray-500">
              {canRemoveEvent && (
                <button
                  type="button"
                  className="rounded-full p-1 hover:bg-gray-100"
                  onClick={() => onModeChange("edit")}
                  aria-label="Edit event"
                >
                  <Pencil className="h-4 w-4" />
                </button>
              )}
              {!event.is_recurring && canRemoveEvent && !bookingEvent && (
                <button
                  type="button"
                  className="rounded-full p-1 hover:bg-gray-100"
                  onClick={requestRemoveEvent}
                  aria-label="Delete event"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              )}
              <button
                type="button"
                className="rounded-full p-1 hover:bg-gray-100"
                onClick={() => onOpenChange(false)}
                aria-label="Close"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>
          <div className="px-4 pb-4 pt-2 text-sm">
            <div className="mb-2 flex items-start gap-3 text-gray-700">
              <Clock className="mt-0.5 h-4 w-4 text-gray-500" />
              <div>
                <div>
                  {format(localStart, "EEEE, MMMM d")} •{" "}
                  {format(localStart, "h:mm a")} – {format(localEnd, "h:mm a")}
                </div>
              </div>
            </div>
            {(() => {
              const userDescription = extractUserDescription(event.description || "");
              return userDescription && (
                <div className="mb-2 flex items-start gap-3 text-gray-700">
                  <AlignLeft className="mt-0.5 h-4 w-4 text-gray-500" />
                  <p className="whitespace-pre-line text-sm">{userDescription}</p>
                </div>
              );
            })()}
            <div className="flex items-start gap-3 text-gray-700">
              <CalendarIcon className="mt-0.5 h-4 w-4 text-gray-500" />
              <span className="text-sm">{calendarName}</span>
            </div>
            {bookingEvent && (
              <p className="mt-2 text-xs text-gray-500" data-testid="calendar-booking-note">
                Booked meeting. Cancelling notifies the other person and frees
                this time.
              </p>
            )}
            {event.is_recurring && (
              <p className="mt-2 text-xs text-gray-500">
                This is a recurring event. When you edit it you can choose to
                change only this event, this and following events, or all events.
              </p>
            )}
            {bookingEvent && canRemoveEvent && (
              <div className="mt-3 border-t border-gray-200 pt-3">
                <button
                  type="button"
                  className="inline-flex w-full items-center justify-center rounded-full border border-red-300 bg-white px-4 py-1.5 text-sm font-medium text-red-600 hover:bg-red-50"
                  data-testid="calendar-cancel-meeting"
                  onClick={requestRemoveEvent}
                >
                  Cancel meeting
                </button>
              </div>
            )}
            {onAskAgent && (
              <div className="mt-3 border-t border-gray-200 pt-3">
                <button
                  type="button"
                  onClick={() => onAskAgent(event)}
                  className="inline-flex w-full items-center justify-center gap-1.5 rounded-full bg-violet-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-violet-700"
                >
                  <Sparkles className="h-3.5 w-3.5" />
                  Ask Agent about this event
                </button>
              </div>
            )}
          </div>
        </div>
      </>
    );
  }

  const formatForInput = (date: Date) => format(date, "yyyy-MM-dd'T'HH:mm");

  const buildFieldPayload = () => {
    const timezone =
      typeof Intl !== "undefined" && Intl.DateTimeFormat().resolvedOptions().timeZone
        ? Intl.DateTimeFormat().resolvedOptions().timeZone
        : "UTC";

    return {
      calendar_id: calendarId || event?.calendar_id,
      title: title.trim(),
      description: description || "",
      start_datetime: localStart.toISOString(),
      end_datetime: localEnd.toISOString(),
      timezone: event?.timezone || timezone,
    };
  };

  const buildRecurrenceWritePayload = () => {
    const recurrence = buildRecurrencePayload(repeatState);
    if (!recurrence) {
      return { is_recurring: false as const };
    }
    return { is_recurring: true as const, recurrence };
  };

  const handleSubmit = async () => {
    if (!calendarId) {
      toast.error("Please select a calendar");
      return;
    }
    if (!title.trim()) {
      toast.error("Title is required");
      return;
    }

    const timezone =
      typeof Intl !== "undefined" && Intl.DateTimeFormat().resolvedOptions().timeZone
        ? Intl.DateTimeFormat().resolvedOptions().timeZone
        : "UTC";

    if (mode === "create") {
      const recurrencePayload = buildRecurrenceWritePayload();
      await onSave({
        action: async () => {
          await CalendarAPI.createEvent({
            calendar_id: calendarId,
            title: title.trim(),
            description: description || "",
            start_datetime: localStart.toISOString(),
            end_datetime: localEnd.toISOString(),
            timezone,
            is_all_day: false,
            ...recurrencePayload,
          });
        },
      });
    } else if (mode === "edit" && event) {
      const fields = buildFieldPayload();
      const recurrenceChanged = !repeatStatesEqual(
        repeatState,
        initialRepeatRef.current,
      );

      if (recurrenceChanged) {
        if (editScope !== "all") {
          toast.error(
            'Changing the repeat rule applies to the entire series. Select "All events".',
          );
          return;
        }
        const recurrencePayload = buildRecurrenceWritePayload();
        await onSave({
          action: async () => {
            await CalendarAPI.updateEvent(
              event.id,
              { ...fields, ...recurrencePayload },
              event.etag,
            );
          },
        });
        return;
      }

      const payload: Partial<EventDTO> = fields;

      if (event.is_recurring) {
        const originalStart =
          pinnedOriginalStartRef.current ??
          event.original_start ??
          event.start_datetime;

        await onSave({
          action: async () => {
            if (editScope === "all") {
              await CalendarAPI.updateEvent(event.id, payload, event.etag);
            } else if (editScope === "future") {
              await CalendarAPI.splitEventSeries(event.id, originalStart, payload);
            } else {
              await CalendarAPI.updateEventInstance(
                event.id,
                originalStart,
                payload,
              );
            }
          },
        });
        return;
      }

      await onSave({
        action: async () => {
          await CalendarAPI.updateEvent(event.id, payload, event.etag);
        },
      });
    }
  };

  return (
    <>
      {backdrop}
      {removeConfirmDialog}
      <div
        role="dialog"
        aria-label={mode === "edit" ? "Edit event" : "Create event"}
        data-testid="calendar-event-dialog"
        className="fixed z-50 max-h-[calc(100dvh-2rem)] w-[calc(100vw-2rem)] max-w-[420px] overflow-y-auto rounded-2xl border bg-white shadow-xl animate-in slide-in-from-bottom-8 fade-in duration-300 sm:rounded-3xl"
        style={{ top: position.top, left: position.left }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex flex-col">
          <div className="px-4 pt-4 pb-2 sm:px-6">
            <input
              autoFocus
              className="w-full border-b border-gray-200 bg-inherit pb-1 text-xl font-semibold text-gray-900 outline-none focus:border-[#3CCED7]"
              placeholder="Add title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </div>

          <div className="px-4 pb-4 sm:px-6">
            <div className="mb-3 text-sm">
              <span className="inline-flex rounded-full bg-[#3CCED7]/10 px-3 py-1 text-xs font-medium text-[#3CCED7]">
                Event
              </span>
            </div>

            <div className="space-y-3 text-sm">
              <div className="flex items-start gap-4">
                <Clock className="mt-1 h-4 w-4 text-gray-500" />
                <div className="flex-1 space-y-1">
                  <p className="bg-inherit text-gray-900">
                    {format(localStart, "EEEE, MMMM d")}{" "}
                    <span className="text-gray-500">
                      • {format(localStart, "HH:mm")} - {format(localEnd, "HH:mm")}
                    </span>
                  </p>
                  <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                    <input
                      type="datetime-local"
                      className="w-full rounded-md border border-gray-300 bg-gray-50 px-2 py-1 text-sm text-gray-900 outline-none focus:border-[#3CCED7]"
                      value={formatForInput(localStart)}
                      onChange={(e) => {
                        const next = new Date(e.target.value);
                        if (!Number.isNaN(next.getTime())) {
                          setLocalStart(next);
                        }
                      }}
                    />
                    <input
                      type="datetime-local"
                      className="w-full rounded-md border border-gray-300 bg-gray-50 px-2 py-1 text-sm text-gray-900 outline-none focus:border-[#3CCED7]"
                      value={formatForInput(localEnd)}
                      onChange={(e) => {
                        const next = new Date(e.target.value);
                        if (!Number.isNaN(next.getTime())) {
                          setLocalEnd(next);
                        }
                      }}
                    />
                  </div>
                  <p className="text-xs text-gray-500">
                    Time zone • {formatRepeatSummary(repeatState, localStart)}
                  </p>
                </div>
              </div>

              {mode === "edit" && event?.is_recurring && (
                <RecurringEditScopeField
                  value={recurrenceChanged ? "all" : editScope}
                  onChange={setEditScope}
                  lockToAll={recurrenceChanged}
                  notice={
                    recurrenceChanged
                      ? "Changing the repeat rule applies to the entire series."
                      : undefined
                  }
                />
              )}

              {showMore && (
                <div
                  className="ml-8 space-y-3 rounded-lg border border-gray-200 bg-gray-50 p-3"
                  data-testid="calendar-repeat-options"
                >
                  <div>
                    <label className="text-xs font-medium text-gray-600">Repeat</label>
                    <select
                      className="mt-1 w-full rounded-md border border-gray-300 bg-white px-2 py-1 text-sm text-gray-900 outline-none focus:border-[#3CCED7]"
                      value={repeatState.preset}
                      onChange={(e) =>
                        setRepeatState((current) => ({
                          ...current,
                          preset: e.target.value as RepeatFormState["preset"],
                        }))
                      }
                      data-testid="calendar-repeat-preset"
                    >
                      <option value="none">Does not repeat</option>
                      <option value="daily">Daily</option>
                      <option value="weekly">Weekly</option>
                    </select>
                  </div>

                  {repeatState.preset !== "none" && (
                    <>
                      <div>
                        <label className="text-xs font-medium text-gray-600">
                          Every
                        </label>
                        <div className="mt-1 flex items-center gap-2">
                          <input
                            type="number"
                            min={1}
                            max={1000}
                            className="w-20 rounded-md border border-gray-300 bg-white px-2 py-1 text-sm text-gray-900 outline-none focus:border-[#3CCED7]"
                            value={repeatState.interval}
                            onChange={(e) =>
                              setRepeatState((current) => ({
                                ...current,
                                interval: Math.max(
                                  1,
                                  Number.parseInt(e.target.value, 10) || 1,
                                ),
                              }))
                            }
                            data-testid="calendar-repeat-interval"
                          />
                          <span className="text-sm text-gray-600">
                            {repeatState.preset === "daily"
                              ? repeatState.interval === 1
                                ? "day"
                                : "days"
                              : repeatState.interval === 1
                                ? "week"
                                : "weeks"}
                          </span>
                        </div>
                      </div>

                      <div>
                        <label className="text-xs font-medium text-gray-600">
                          Ends
                        </label>
                        <select
                          className="mt-1 w-full rounded-md border border-gray-300 bg-white px-2 py-1 text-sm text-gray-900 outline-none focus:border-[#3CCED7]"
                          value={repeatState.endCondition}
                          onChange={(e) =>
                            setRepeatState((current) => ({
                              ...current,
                              endCondition: e.target
                                .value as RepeatFormState["endCondition"],
                            }))
                          }
                          data-testid="calendar-repeat-end"
                        >
                          <option value="never">Never</option>
                          <option value="until">On date</option>
                          <option value="count">After</option>
                        </select>
                      </div>

                      {repeatState.endCondition === "until" && (
                        <input
                          type="date"
                          className="w-full rounded-md border border-gray-300 bg-white px-2 py-1 text-sm text-gray-900 outline-none focus:border-[#3CCED7]"
                          value={repeatState.untilDate}
                          onChange={(e) =>
                            setRepeatState((current) => ({
                              ...current,
                              untilDate: e.target.value,
                            }))
                          }
                          data-testid="calendar-repeat-until"
                        />
                      )}

                      {repeatState.endCondition === "count" && (
                        <div className="flex items-center gap-2">
                          <input
                            type="number"
                            min={1}
                            max={999}
                            className="w-20 rounded-md border border-gray-300 bg-white px-2 py-1 text-sm text-gray-900 outline-none focus:border-[#3CCED7]"
                            value={repeatState.occurrenceCount}
                            onChange={(e) =>
                              setRepeatState((current) => ({
                                ...current,
                                occurrenceCount: Math.max(
                                  1,
                                  Number.parseInt(e.target.value, 10) || 1,
                                ),
                              }))
                            }
                            data-testid="calendar-repeat-count"
                          />
                          <span className="text-sm text-gray-600">occurrences</span>
                        </div>
                      )}
                    </>
                  )}
                </div>
              )}

              <div className="flex items-start gap-4">
                <AlignLeft className="mt-1 h-4 w-4 text-gray-500" />
                <textarea
                  className="min-h-[72px] w-full resize-none rounded-md border border-gray-300 bg-gray-50 px-2 py-1 text-sm text-gray-900 outline-none focus:border-[#3CCED7]"
                  placeholder="Add description"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                />
              </div>

              {mode === "create" ? (
                <div className="flex items-center gap-4">
                  <CalendarIcon className="h-4 w-4 text-gray-500" />
                  <div className="flex-1">
                    <p className="text-xs text-gray-500">Calendar</p>
                    <select
                      className="mt-1 w-full rounded-md border border-gray-300 bg-gray-50 px-2 py-1 text-sm text-gray-900 outline-none focus:border-[#3CCED7]"
                      value={calendarId}
                      onChange={(e) => setCalendarId(e.target.value)}
                      data-testid="event-calendar"
                    >
                      {mergedCalendars.some((cal) => cal.project_id != null) && (
                        <optgroup label="Team">
                          {mergedCalendars
                            .filter((cal) => cal.project_id != null)
                            .map((cal) => (
                              <option key={cal.id} value={cal.id}>
                                {cal.name}
                              </option>
                            ))}
                        </optgroup>
                      )}
                      {mergedCalendars.some((cal) => cal.project_id == null) && (
                        <optgroup label="Personal">
                          {mergedCalendars
                            .filter((cal) => cal.project_id == null)
                            .map((cal) => (
                              <option key={cal.id} value={cal.id}>
                                {cal.name}
                              </option>
                            ))}
                        </optgroup>
                      )}
                    </select>
                  </div>
                </div>
              ) : (
                event && (
                  <div className="flex items-center gap-4">
                    <CalendarIcon className="h-4 w-4 text-gray-500" />
                    <div className="flex items-center gap-2 text-sm text-gray-900">
                      <span
                        className="inline-block h-2.5 w-2.5 rounded-full"
                        style={{
                          backgroundColor:
                            mergedCalendars.find((c) => c.id === event.calendar_id)?.color ||
                            "#1E88E5",
                        }}
                      />
                      <span>
                        {mergedCalendars.find((c) => c.id === event.calendar_id)?.name}
                      </span>
                    </div>
                  </div>
                )
              )}

            </div>
          </div>

          <div className="flex flex-col gap-3 border-t bg-inherit px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-6">
            {canShowMoreOptions ? (
              <button
                type="button"
                className="self-start text-sm font-medium text-[#3CCED7] hover:opacity-80"
                onClick={() => setShowMore((open) => !open)}
                data-testid="calendar-more-options"
              >
                {showMore ? "Fewer options" : "More options"}
              </button>
            ) : (
              <span className="self-start text-xs text-gray-500">
                Repeat settings apply to the entire series only.
              </span>
            )}
            <div className="flex flex-wrap items-center justify-end gap-2">
              {mode === "edit" && event && !event.is_recurring && canRemoveEvent && (
                <button
                  type="button"
                  className="rounded-md border border-red-300 bg-white px-3 py-1.5 text-sm font-medium text-red-600 hover:bg-red-50"
                  data-testid={bookingEvent ? "calendar-cancel-meeting" : "calendar-delete-event"}
                  onClick={requestRemoveEvent}
                >
                  {bookingEvent ? "Cancel meeting" : "Delete"}
                </button>
              )}
              <button
                type="button"
                className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 hover:bg-gray-50"
                onClick={() => onOpenChange(false)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="rounded-md bg-gradient-to-r from-[#3CCED7] to-[#A6E661] px-5 py-1.5 text-sm font-medium text-white transition-opacity hover:opacity-95"
                onClick={handleSubmit}
              >
                Save
              </button>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
