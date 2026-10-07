import type { Meta, StoryObj } from "@storybook/react";
import React from "react";
import type { CalendarViewType } from "@/lib/api/calendarApi";
import { CalendarToolbar } from "@/components/calendar/CalendarToolbar";

const meta: Meta<typeof CalendarToolbar> = {
  title: "Calendar/CalendarToolbar",
  component: CalendarToolbar,
  tags: ["autodocs"],
  parameters: {
    layout: "fullscreen",
  },
  argTypes: {
    headerTitle: {
      control: "text",
      description: "The calendar header title",
    },
    currentView: {
      control: "select",
      options: ["day", "week", "month", "year", "agenda"],
      description: "Current calendar view type",
    },
  },
};

export default meta;
type Story = StoryObj<typeof CalendarToolbar>;

function ToolbarStory(args: {
  headerTitle: string;
  currentView: CalendarViewType;
}) {
  const [view, setView] = React.useState<CalendarViewType>(args.currentView);
  const [open, setOpen] = React.useState(false);
  const [showAllEvents, setShowAllEvents] = React.useState(false);
  const ref = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    setView(args.currentView);
  }, [args.currentView]);

  return (
    <div className="min-h-[280px] bg-[#f8fafd]">
      <CalendarToolbar
        headerTitle={args.headerTitle}
        currentView={view}
        viewSwitcherOpen={open}
        viewSwitcherRef={ref}
        onToggleViewSwitcher={() => setOpen((v) => !v)}
        onSelectView={(next) => {
          setView(next);
          setOpen(false);
        }}
        onToday={() => {}}
        onOffset={() => {}}
        showAllEvents={showAllEvents}
        onShowAllEventsChange={setShowAllEvents}
      />
    </div>
  );
}

export const Default: Story = {
  args: {
    headerTitle: "March 2026",
    currentView: "week",
  },
  render: (args) => <ToolbarStory {...args} />,
};

