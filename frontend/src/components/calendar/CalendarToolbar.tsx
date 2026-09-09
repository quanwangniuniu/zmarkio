"use client";

import React from "react";
import Link from "next/link";
import type { CalendarViewType } from "@/lib/api/calendarApi";
import { ChevronLeft, ChevronRight, Link2 } from "lucide-react";
import { VIEW_LABELS } from "@/components/calendar/utils";
import { useBuildUrl } from "@/lib/buildUrl";

type CalendarToolbarProps = {
  headerTitle: string;
  currentView: CalendarViewType;
  viewSwitcherOpen: boolean;
  viewSwitcherRef: React.RefObject<HTMLDivElement>;
  onToggleViewSwitcher: () => void;
  onSelectView: (view: CalendarViewType) => void;
  onToday: () => void;
  onOffset: (direction: "prev" | "next") => void;
  onAskAgent?: () => void;
  showAllEvents?: boolean;
  onShowAllEventsChange?: (checked: boolean) => void;
};

const VIEW_ORDER: CalendarViewType[] = ["day", "week", "month", "year", "agenda"];

export function CalendarToolbar({
  headerTitle,
  currentView,
  viewSwitcherRef,
  onSelectView,
  onToday,
  onOffset,
  onAskAgent,
  showAllEvents = false,
  onShowAllEventsChange,
}: CalendarToolbarProps) {
  const buildUrl = useBuildUrl();

  return (
    <header
      className="flex flex-col gap-2 border-b border-gray-200 bg-white px-3 py-2 sm:flex-row sm:items-center sm:justify-between sm:px-4 sm:py-2.5"
      data-testid="calendar-toolbar"
    >
      <div className="flex min-w-0 flex-wrap items-center gap-2 sm:flex-nowrap sm:gap-3">
        <button
          type="button"
          onClick={onToday}
          className="inline-flex shrink-0 items-center rounded-md border border-[#3CCED7] px-3 py-1.5 text-sm font-medium text-[#3CCED7] transition-colors hover:bg-[#3CCED7]/10"
          data-testid="calendar-today"
        >
          Today
        </button>
        <div className="flex shrink-0 items-center">
          <button
            type="button"
            onClick={() => onOffset("prev")}
            className="flex h-8 w-8 items-center justify-center rounded-md text-gray-600 hover:bg-gray-100"
            aria-label="Previous period"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={() => onOffset("next")}
            className="flex h-8 w-8 items-center justify-center rounded-md text-gray-600 hover:bg-gray-100"
            aria-label="Next period"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
        <span
          data-testid="calendar-header-title"
          className="min-w-0 flex-1 truncate text-sm font-semibold text-gray-900 sm:text-base"
        >
          {headerTitle}
        </span>
      </div>

      <div className="flex min-w-0 items-center gap-2 sm:gap-3" ref={viewSwitcherRef}>
        {onShowAllEventsChange && (
          <div
            className="inline-flex shrink-0 items-center gap-2 text-xs text-gray-600 sm:text-sm"
            data-testid="calendar-all-events-toggle"
          >
            <span className={showAllEvents ? "font-medium text-[#0E8A96]" : undefined}>
              All events
            </span>
            <button
              type="button"
              role="switch"
              aria-checked={showAllEvents}
              aria-label="Show all events"
              onClick={() => onShowAllEventsChange(!showAllEvents)}
              className={`relative inline-flex h-5 w-9 shrink-0 items-center rounded-full transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#3CCED7]/80 focus-visible:ring-offset-2 ${
                showAllEvents ? "bg-[#3CCED7]" : "bg-gray-300"
              }`}
            >
              <span
                className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow-sm transition-transform ${
                  showAllEvents ? "translate-x-4" : "translate-x-0.5"
                }`}
              />
            </button>
          </div>
        )}
        <Link
          href={buildUrl("/calendar/booking-links")}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-[#3CCED7] bg-[#3CCED7]/10 px-3 py-1.5 text-xs font-medium text-[#0E8A96] transition-colors hover:bg-[#3CCED7]/20 sm:text-sm"
          data-testid="calendar-create-booking-link"
        >
          <Link2 className="h-3.5 w-3.5" />
          Create Booking link
        </Link>
        {onAskAgent && (
          <button
            type="button"
            onClick={onAskAgent}
            className="inline-flex shrink-0 items-center rounded-md border border-violet-200 bg-violet-50 px-3 py-1.5 text-xs font-medium text-violet-800 transition-colors hover:bg-violet-100 sm:text-sm"
            data-testid="calendar-ask-agent"
          >
            Ask Agent
          </button>
        )}
        <nav
          className="flex min-w-0 flex-1 items-center gap-0.5 overflow-x-auto rounded-md border border-gray-200 bg-gray-50 p-0.5 sm:flex-none"
          data-testid="calendar-view-tabs"
          role="tablist"
        >
          {VIEW_ORDER.map((view) => {
            const isActive = currentView === view;
            return (
              <button
                key={view}
                type="button"
                role="tab"
                aria-selected={isActive}
                onClick={() => onSelectView(view)}
                data-testid={`calendar-view-${view}`}
                data-active={isActive ? "true" : "false"}
                className={`shrink-0 rounded px-3 py-1 text-xs font-medium transition-colors ${
                  isActive
                    ? "bg-white text-[#3CCED7] shadow-sm"
                    : "text-gray-600 hover:text-gray-900"
                }`}
              >
                {VIEW_LABELS[view]}
              </button>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
