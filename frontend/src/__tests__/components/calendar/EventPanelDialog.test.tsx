import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { EventPanelDialog } from "@/components/calendar/EventPanelDialog";
import { CalendarAPI } from "@/lib/api/calendarApi";

jest.mock("@/lib/api/calendarApi", () => ({
  CalendarAPI: {
    createEvent: jest.fn(),
    updateEvent: jest.fn(),
    updateEventInstance: jest.fn(),
    splitEventSeries: jest.fn(),
  },
  extractUserDescription: (value: string) => value,
  extractNavigationMetadata: () => null,
}));

const calendars = [
  {
    id: "cal-1",
    organization_id: "org-1",
    owner: {
      id: 1,
      email: "user@example.com",
      username: "user",
      full_name: "User",
    },
    name: "Primary",
    color: "#1E88E5",
    visibility: "private",
    timezone: "UTC",
    is_primary: true,
  },
];

const baseProps = {
  open: true,
  mode: "create" as const,
  onModeChange: jest.fn(),
  onOpenChange: jest.fn(),
  start: new Date("2026-06-30T09:00:00"),
  end: new Date("2026-06-30T10:00:00"),
  event: null,
  calendars,
  primaryCalendar: calendars[0],
  preferredCalendarId: "cal-1",
  onSave: jest.fn(async ({ action }: { action: () => Promise<void> }) => {
    await action();
  }),
  position: { top: 100, left: 100 },
};

describe("EventPanelDialog recurrence UI", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it("does not render disabled Task or Appointment tabs", () => {
    render(<EventPanelDialog {...baseProps} />);

    expect(screen.getByText("Event")).toBeInTheDocument();
    expect(screen.queryByText("Task")).not.toBeInTheDocument();
    expect(screen.queryByText("Appointment schedule")).not.toBeInTheDocument();
  });

  it("expands repeat options from More options and creates a weekly event", async () => {
    (CalendarAPI.createEvent as jest.Mock).mockResolvedValue({ id: "evt-1" });

    render(<EventPanelDialog {...baseProps} />);

    fireEvent.change(screen.getByPlaceholderText("Add title"), {
      target: { value: "Weekly standup" },
    });
    fireEvent.click(screen.getByTestId("calendar-more-options"));
    expect(screen.getByTestId("calendar-repeat-options")).toBeInTheDocument();

    fireEvent.change(screen.getByTestId("calendar-repeat-preset"), {
      target: { value: "weekly" },
    });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() => {
      expect(CalendarAPI.createEvent).toHaveBeenCalledWith(
        expect.objectContaining({
          title: "Weekly standup",
          is_recurring: true,
          recurrence: expect.objectContaining({
            frequency: "WEEKLY",
            interval: 1,
          }),
        }),
      );
    });
  });

  it("locks scope to all when editing a recurring event repeat rule", async () => {
    (CalendarAPI.updateEvent as jest.Mock).mockResolvedValue({ id: "evt-2" });

    render(
      <EventPanelDialog
        {...baseProps}
        mode="edit"
        event={{
          id: "evt-2",
          calendar_id: "cal-1",
          title: "Weekly sync",
          start_datetime: "2026-06-30T09:00:00.000Z",
          end_datetime: "2026-06-30T10:00:00.000Z",
          is_all_day: false,
          is_recurring: true,
          recurrence_rule: {
            frequency: "WEEKLY",
            interval: 1,
          },
        }}
      />,
    );

    fireEvent.click(screen.getByTestId("recurring-scope-option-all"));
    fireEvent.click(screen.getByTestId("calendar-more-options"));
    fireEvent.change(screen.getByTestId("calendar-repeat-interval"), {
      target: { value: "2" },
    });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() => {
      expect(CalendarAPI.updateEvent).toHaveBeenCalledWith(
        "evt-2",
        expect.objectContaining({
          is_recurring: true,
          recurrence: expect.objectContaining({
            frequency: "WEEKLY",
            interval: 2,
          }),
        }),
        undefined,
      );
    });
  });

  it("shows inline scope options and hides repeat fields when scope is this", () => {
    render(
      <EventPanelDialog
        {...baseProps}
        mode="edit"
        event={{
          id: "evt-3",
          calendar_id: "cal-1",
          title: "Daily sync",
          start_datetime: "2026-06-30T09:00:00.000Z",
          end_datetime: "2026-06-30T10:00:00.000Z",
          is_all_day: false,
          is_recurring: true,
          recurrence_rule: {
            frequency: "DAILY",
            interval: 1,
          },
        }}
      />,
    );

    expect(screen.getByTestId("recurring-scope-field")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "This event" })).toBeChecked();
    expect(screen.queryByTestId("calendar-more-options")).not.toBeInTheDocument();
    expect(screen.queryByTestId("calendar-repeat-options")).not.toBeInTheDocument();
  });

  it("updates only this occurrence when scope is this", async () => {
    (CalendarAPI.updateEventInstance as jest.Mock).mockResolvedValue({ id: "evt-4" });

    render(
      <EventPanelDialog
        {...baseProps}
        mode="edit"
        event={{
          id: "evt-4",
          calendar_id: "cal-1",
          title: "Daily sync",
          start_datetime: "2026-06-30T09:00:00.000Z",
          end_datetime: "2026-06-30T10:00:00.000Z",
          original_start: "2026-06-30T09:00:00.000Z",
          is_all_day: false,
          is_recurring: true,
          recurrence_rule: {
            frequency: "DAILY",
            interval: 1,
          },
        }}
      />,
    );

    fireEvent.change(screen.getByPlaceholderText("Add title"), {
      target: { value: "Only this one" },
    });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() => {
      expect(CalendarAPI.updateEventInstance).toHaveBeenCalledWith(
        "evt-4",
        "2026-06-30T09:00:00.000Z",
        expect.objectContaining({ title: "Only this one" }),
      );
    });
  });

  it("lets the host cancel a booked meeting from the event panel", async () => {
    const onDelete = jest.fn().mockResolvedValue(undefined);

    render(
      <EventPanelDialog
        {...baseProps}
        mode="view"
        onDelete={onDelete}
        event={{
          id: "booked-1",
          calendar_id: "cal-1",
          title: "Intro call",
          start_datetime: "2026-06-30T09:00:00.000Z",
          end_datetime: "2026-06-30T09:30:00.000Z",
          is_all_day: false,
          is_recurring: false,
          metadata: { source: "booking_link" },
        }}
      />,
    );

    expect(screen.getByTestId("calendar-booking-note")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("calendar-cancel-meeting"));
    const confirmTitle = screen.getByText("Cancel this meeting?");
    const confirmDialog = confirmTitle.closest(".fixed") as HTMLElement;
    fireEvent.click(within(confirmDialog).getByRole("button", { name: "Cancel meeting" }));

    await waitFor(() => {
      expect(onDelete).toHaveBeenCalledWith(
        expect.objectContaining({ id: "booked-1" }),
      );
    });
  });
});
