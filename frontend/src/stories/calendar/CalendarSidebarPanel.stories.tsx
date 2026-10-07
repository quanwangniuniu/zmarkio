import type { Meta, StoryObj } from "@storybook/react";
import React from "react";
import { CalendarSidebarPanel } from "@/components/calendar/CalendarSidebarPanel";
import {
  getSampleSidebarMyCalendars,
  getSampleSidebarOtherCalendars,
} from "@/stories/calendar/calendarData";

const meta: Meta<typeof CalendarSidebarPanel> = {
  title: "Calendar/CalendarSidebarPanel",
  component: CalendarSidebarPanel,
  tags: ["autodocs"],
  parameters: {
    layout: "fullscreen",
  },
};

export default meta;
type Story = StoryObj<typeof CalendarSidebarPanel>;

const wrapperClass =
  "calendar-sidebar-story flex h-[560px] items-center justify-center bg-[#f8fafd]";
const asideClass =
  "[&_aside]:!block [&_aside]:w-80 [&_aside]:h-[500px] [&_aside]:rounded-2xl [&_aside]:border [&_aside]:border-gray-200 [&_aside]:bg-white [&_aside]:shadow-sm [&_aside]:overflow-hidden";

type SidebarStoryProps = {
  selectedCalendarId: string | null;
  myCalendars: ReturnType<typeof getSampleSidebarMyCalendars>;
  otherCalendars: ReturnType<typeof getSampleSidebarOtherCalendars>;
  isLoading: boolean;
  error: Error | null;
};

function SidebarStory({
  selectedCalendarId,
  myCalendars,
  otherCalendars,
  isLoading,
  error,
}: SidebarStoryProps) {
  const [currentDate, setCurrentDate] = React.useState(new Date());
  const [activeCalendarId, setActiveCalendarId] = React.useState<string | null>(
    selectedCalendarId,
  );
  const [activeEventTypes, setActiveEventTypes] = React.useState(
    () => new Set(["decision", "task"]),
  );

  return (
    <div className={wrapperClass}>
      <style>{`
        .calendar-sidebar-story [data-radix-scroll-area-root] {
          height: 250px !important;
        }
      `}</style>
      <div className={asideClass}>
        <CalendarSidebarPanel
          currentDate={currentDate}
          onDateChange={setCurrentDate}
          selectedCalendarId={activeCalendarId}
          myCalendars={myCalendars}
          otherCalendars={otherCalendars}
          isLoading={isLoading}
          error={error}
          onCalendarItemClick={(calendarId) => {
            setActiveCalendarId(calendarId);
          }}
          activeEventTypes={activeEventTypes}
          onToggleActivityType={(type) => {
            setActiveEventTypes((prev) => {
              const next = new Set(prev);
              if (next.has(type)) {
                next.delete(type);
              } else {
                next.add(type);
              }
              return next;
            });
          }}
        />
      </div>
    </div>
  );
}

export const Default: Story = {
  render: () => (
    <SidebarStory
      selectedCalendarId={getSampleSidebarMyCalendars()[0].calendarId}
      myCalendars={getSampleSidebarMyCalendars()}
      otherCalendars={getSampleSidebarOtherCalendars()}
      isLoading={false}
      error={null}
    />
  ),
};

export const Loading: Story = {
  render: () => (
    <SidebarStory
      selectedCalendarId={null}
      myCalendars={[]}
      otherCalendars={[]}
      isLoading={true}
      error={null}
    />
  ),
};

export const ErrorState: Story = {
  render: () => (
    <SidebarStory
      selectedCalendarId={null}
      myCalendars={getSampleSidebarMyCalendars()}
      otherCalendars={getSampleSidebarOtherCalendars()}
      isLoading={false}
      error={new Error("Failed to load calendars. Please refresh the page.")}
    />
  ),
};
