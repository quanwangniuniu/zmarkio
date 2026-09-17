import type { NotificationItem } from "@/types/notifications";
import {
  buildNotificationFullPageTarget,
  extractNotificationProjectId,
  parseProjectIdFromActionUrl,
} from "@/lib/notificationRoutes";

function makeNotification(
  overrides: Partial<NotificationItem> = {}
): NotificationItem {
  return {
    id: "1",
    category: "TASKS",
    event_type: "task_assigned",
    related_object_type: "task",
    related_object_id: "42",
    title: "Test",
    body: "",
    is_read: false,
    action_url: "",
    metadata: {},
    created_at: "2024-01-01T00:00:00Z",
    responded: false,
    response: "",
    ...overrides,
  };
}

describe("notificationRoutes", () => {
  it("parses project_id from legacy nested action URLs only", () => {
    expect(parseProjectIdFromActionUrl("/projects/5/meetings/9")).toBe(5);
    expect(parseProjectIdFromActionUrl("/meetings/9?project_id=5")).toBeUndefined();
  });

  it("extracts project_id from notification metadata", () => {
    const notification = makeNotification({
      metadata: { project_id: 7 },
      action_url: "/projects/99/tasks/1",
    });
    expect(extractNotificationProjectId(notification)).toBe(7);
  });

  it("maps task notifications to the v2 task detail route", () => {
    const target = buildNotificationFullPageTarget(
      makeNotification({
        related_object_type: "task",
        related_object_id: "42",
        metadata: { project_id: 1 },
        action_url: "/projects/1/tasks/42",
      })
    );
    expect(target).toEqual({
      href: "/tasks/42",
      requiresProjectSwitch: false,
    });
  });

  it("maps meeting notifications to the v2 meeting detail route", () => {
    const target = buildNotificationFullPageTarget(
      makeNotification({
        related_object_type: "meeting",
        related_object_id: "9",
        metadata: { project_id: 5 },
        action_url: "/projects/5/meetings/9",
      })
    );
    expect(target).toEqual({
      href: "/meetings/9",
      requiresProjectSwitch: false,
    });
  });

  it("maps decision notifications to the v2 decision detail route", () => {
    const target = buildNotificationFullPageTarget(
      makeNotification({
        related_object_type: "decision",
        related_object_id: "12",
        metadata: { project_id: 3 },
        action_url: "/projects/3/decisions/12",
      })
    );
    expect(target).toEqual({
      href: "/decisions/12",
      requiresProjectSwitch: false,
    });
  });

  it("maps project notifications to overview with project switch", () => {
    const target = buildNotificationFullPageTarget(
      makeNotification({
        related_object_type: "project",
        related_object_id: "8",
        action_url: "/projects/8",
      })
    );
    expect(target).toEqual({
      href: "/overview",
      requiresProjectSwitch: true,
      projectId: 8,
    });
  });

  it("translates legacy action_url when related_object_type is missing", () => {
    const target = buildNotificationFullPageTarget(
      makeNotification({
        related_object_type: "",
        related_object_id: "",
        action_url: "/projects/2/decisions/15",
      })
    );
    expect(target).toEqual({
      href: "/decisions/15",
      requiresProjectSwitch: false,
    });
  });
});

// Slug routing. Each resource uses `related_object_slug ?? related_object_id`,
// so when a slug is present the URL must use it and only fall back to the numeric id
// when there is none. Guards the recurring "list/notification dropped the slug → 404
// on the numeric id" regression class.
describe("notificationRoutes — slug routing", () => {
  it("uses the task slug in the URL when related_object_slug is present", () => {
    const target = buildNotificationFullPageTarget(
      makeNotification({
        related_object_type: "task",
        related_object_id: "42",
        related_object_slug: "design-homepage-banner",
      })
    );
    expect(target?.href).toBe("/tasks/design-homepage-banner");
  });

  it("falls back to the numeric task id only when there is no slug", () => {
    const target = buildNotificationFullPageTarget(
      makeNotification({
        related_object_type: "task",
        related_object_id: "42",
        related_object_slug: undefined,
      })
    );
    expect(target?.href).toBe("/tasks/42");
  });

  it("uses the meeting slug, not the numeric id", () => {
    const target = buildNotificationFullPageTarget(
      makeNotification({
        related_object_type: "meeting",
        related_object_id: "9",
        related_object_slug: "q2-client-sync",
        metadata: { project_id: 5 },
      })
    );
    expect(target?.href).toBe("/meetings/q2-client-sync");
    expect(target?.href).not.toContain("/meetings/9");
  });

  it("uses the decision slug, not the numeric id", () => {
    const target = buildNotificationFullPageTarget(
      makeNotification({
        related_object_type: "decision",
        related_object_id: "12",
        related_object_slug: "approve-budget-increase",
        metadata: { project_id: 3 },
      })
    );
    expect(target?.href).toBe("/decisions/approve-budget-increase");
    expect(target?.href).not.toContain("/decisions/12");
  });

  it("maps a booking-link invite to the public book page", () => {
    const target = buildNotificationFullPageTarget(
      makeNotification({
        related_object_type: "booking_link",
        related_object_id: "abc",
        event_type: "meeting_participant_added",
        action_url: "/calendar/booking-links",
        metadata: { organization_slug: "gmail", link_slug: "standup2" },
      })
    );
    expect(target).toEqual({
      href: "/book/gmail/standup2",
      requiresProjectSwitch: false,
    });
  });

  it("maps a host booking-link notice to the manager, not the public page", () => {
    const target = buildNotificationFullPageTarget(
      makeNotification({
        related_object_type: "booking_link",
        related_object_id: "abc",
        event_type: "meeting_created",
        action_url: "/calendar/booking-links",
        metadata: { organization_slug: "gmail", link_slug: "standup2" },
      })
    );
    expect(target?.href).toBe("/calendar/booking-links");
  });

  it("does not send a booking-link invitee to the empty manager page", () => {
    const target = buildNotificationFullPageTarget(
      makeNotification({
        related_object_type: "booking_link",
        related_object_id: "abc",
        event_type: "meeting_participant_added",
        action_url: "/calendar/booking-links",
        metadata: {},
      })
    );
    expect(target?.href).toBe("/calendar");
  });

  it("does not send a closed booking invite back to the picker", () => {
    const target = buildNotificationFullPageTarget(
      makeNotification({
        related_object_type: "booking_link",
        related_object_id: "abc",
        event_type: "meeting_participant_added",
        action_url: "/book/gmail/standup2",
        metadata: {
          organization_slug: "gmail",
          link_slug: "standup2",
          can_rebook: false,
        },
      })
    );
    expect(target?.href).toBe("/calendar");
  });

  it("recovers the book page from the invite action URL when metadata is empty", () => {
    const target = buildNotificationFullPageTarget(
      makeNotification({
        related_object_type: "booking_link",
        related_object_id: "abc",
        event_type: "meeting_participant_added",
        action_url: "/book/gmail/standup3",
        metadata: {},
      })
    );
    expect(target?.href).toBe("/book/gmail/standup3");
  });

  it("prefers task_slug from metadata for a budget_request notification", () => {
    const target = buildNotificationFullPageTarget(
      makeNotification({
        related_object_type: "budget_request",
        related_object_id: "5",
        metadata: { task_slug: "launch-campaign", task_id: 5 },
      })
    );
    expect(target?.href).toBe("/tasks/launch-campaign");
  });
});
