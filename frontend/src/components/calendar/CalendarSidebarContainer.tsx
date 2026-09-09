import React, { useCallback } from "react";
import { useCalendarSidebarData } from "@/hooks/useCalendarSidebarData";
import { CalendarSidebarPanel } from "@/components/calendar/CalendarSidebarPanel";

type CalendarSidebarContainerProps = {
  currentDate: Date;
  projectId?: number | string | null;
  onVisibleCalendarsChange: (calendarIds: string[] | undefined) => void;
  onDateChange: (next: Date) => void;
  selectedCalendarId: string | null;
  activeEventTypes: Set<string>;
  onToggleActivityType: (type: string) => void;
};

export function CalendarSidebarContainer({
  currentDate,
  projectId,
  onVisibleCalendarsChange,
  onDateChange,
  selectedCalendarId,
  activeEventTypes,
  onToggleActivityType,
}: CalendarSidebarContainerProps) {
  const { myCalendars, otherCalendars, isLoading, error } = useCalendarSidebarData(projectId);

  const handleCalendarItemClick = useCallback(
    (calendarId: string) => {
      if (selectedCalendarId === calendarId) {
        return;
      }
      onVisibleCalendarsChange([calendarId]);
    },
    [onVisibleCalendarsChange, selectedCalendarId],
  );

  React.useEffect(() => {
    if (!selectedCalendarId || isLoading || error) {
      return;
    }
    const allItems = [...myCalendars, ...otherCalendars];
    const hasSelectedCalendar = allItems.some(
      (item) => item.calendarId === selectedCalendarId,
    );
    if (!hasSelectedCalendar) {
      onVisibleCalendarsChange(undefined);
    }
  }, [
    selectedCalendarId,
    isLoading,
    error,
    myCalendars,
    otherCalendars,
    onVisibleCalendarsChange,
  ]);

  return (
    <CalendarSidebarPanel
      currentDate={currentDate}
      onDateChange={onDateChange}
      selectedCalendarId={selectedCalendarId}
      myCalendars={myCalendars}
      otherCalendars={otherCalendars}
      isLoading={isLoading}
      error={error}
      onCalendarItemClick={handleCalendarItemClick}
      activeEventTypes={activeEventTypes}
      onToggleActivityType={onToggleActivityType}
    />
  );
}
