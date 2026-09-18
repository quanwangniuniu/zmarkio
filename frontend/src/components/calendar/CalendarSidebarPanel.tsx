import React from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { SidebarCalendarItem } from "@/hooks/useCalendarSidebarData";
import { MiniMonthCalendar } from "@/components/calendar/CalendarViews";

type CalendarSidebarPanelProps = {
  currentDate: Date;
  onDateChange: (next: Date) => void;
  selectedCalendarId: string | null;
  myCalendars: SidebarCalendarItem[];
  otherCalendars: SidebarCalendarItem[];
  isLoading: boolean;
  error: Error | null;
  onCalendarItemClick: (calendarId: string) => void;
  activeEventTypes: Set<string>;
  onToggleActivityType: (type: string) => void;
};

const ACTIVITY_TYPES: { id: string; label: string; color: string }[] = [
  { id: "decision", label: "Decisions", color: "#8B5CF6" },
  { id: "task", label: "Tasks", color: "#A6E661" },
];

export function CalendarSidebarPanel({
  currentDate,
  onDateChange,
  selectedCalendarId,
  myCalendars,
  otherCalendars,
  isLoading,
  error,
  onCalendarItemClick,
  activeEventTypes,
  onToggleActivityType,
}: CalendarSidebarPanelProps) {
  return (
    <aside
      className="hidden w-[260px] shrink-0 border-r border-gray-200 bg-white p-4 lg:block"
      data-testid="calendar-sidebar"
    >
      <MiniMonthCalendar currentDate={currentDate} onDateChange={onDateChange} />

      <ScrollArea className="h-[calc(100vh-260px)] pr-2">
        <div className="mb-4">
          <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
            Activity
          </h3>
          <ul className="space-y-1">
            {ACTIVITY_TYPES.map((item) => {
              const isActive = activeEventTypes.has(item.id);
              return (
                <li key={item.id}>
                  <button
                    type="button"
                    onClick={() => onToggleActivityType(item.id)}
                    data-testid={`calendar-activity-${item.id}`}
                    data-active={isActive ? "true" : "false"}
                    className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors ${
                      isActive
                        ? "bg-[#3CCED7]/10 text-[#3CCED7]"
                        : "text-gray-700 hover:bg-gray-50"
                    }`}
                  >
                    <span
                      className="h-3 w-3 rounded-sm border"
                      style={{
                        backgroundColor: isActive ? item.color : "transparent",
                        borderColor: item.color,
                      }}
                    />
                    <span className="flex-1 truncate">{item.label}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>

        {isLoading && (
          <p className="mb-2 text-xs text-gray-400">Loading calendars…</p>
        )}
        {error && (
          <p className="mb-2 text-xs text-red-500">
            Failed to load calendars. Please refresh the page.
          </p>
        )}

        {myCalendars.length > 0 && (
          <div className="mb-4">
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
              Personal
            </h3>
            <ul className="space-y-1">
              {myCalendars.map((item) => {
                const isSelected = selectedCalendarId === item.calendarId;
                return (
                  <li key={item.calendarId}>
                    <button
                      type="button"
                      onClick={() => onCalendarItemClick(item.calendarId)}
                      data-testid="calendar-sidebar-personal"
                      data-selected={isSelected ? "true" : "false"}
                      className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors ${
                        isSelected
                          ? "bg-[#3CCED7]/10 text-[#3CCED7] border-l-2 border-[#3CCED7]"
                          : "text-gray-700 hover:bg-gray-50"
                      }`}
                    >
                      <span
                        className="h-3 w-3 rounded-sm border"
                        style={{ backgroundColor: item.color }}
                      />
                      <span className="flex-1 truncate">{item.name}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        )}

        {otherCalendars.length > 0 && (
          <div className="mb-4">
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
              Team
            </h3>
            <ul className="space-y-1">
              {otherCalendars.map((item) => {
                const isSelected = selectedCalendarId === item.calendarId;
                return (
                  <li key={item.calendarId}>
                    <button
                      type="button"
                      onClick={() => onCalendarItemClick(item.calendarId)}
                      data-testid="calendar-sidebar-team"
                      data-selected={isSelected ? "true" : "false"}
                      className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors ${
                        isSelected
                          ? "bg-[#3CCED7]/10 text-[#3CCED7] border-l-2 border-[#3CCED7]"
                          : "text-gray-700 hover:bg-gray-50"
                      }`}
                    >
                      <span
                        className="h-3 w-3 rounded-sm border"
                        style={{ backgroundColor: item.color }}
                      />
                      <span className="flex-1 truncate">{item.name}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        )}
      </ScrollArea>
    </aside>
  );
}
