import { nestedProjectPath } from "@/lib/projectNestedRoutes";
import { buildMessagesPath, translateLegacyMessagesActionUrl } from "@/lib/messages/messagesRoutes";
import { ProjectAPI } from "@/lib/api/projectApi";
import { useProjectStore } from "@/lib/projectStore";
import type { NotificationItem } from "@/types/notifications";
import { NOTIFICATION_EVENT } from "@/types/notifications";

export interface NotificationNavigationTarget {
  href: string;
  requiresProjectSwitch: boolean;
  projectId?: number | string;
}

export function parseProjectIdFromActionUrl(actionUrl: string): number | string | undefined {
  const legacyMatch = actionUrl.match(/\/projects\/([^/]+)/);
  if (legacyMatch) {
    const rawVal = legacyMatch[1];
    const projectId = Number(rawVal);
    if (Number.isFinite(projectId) && projectId > 0) return projectId;
    return rawVal;
  }

  return undefined;
}

/** A named guest being asked to book — not a host notice, not a real meeting. */
export function isBookingLinkInvite(notification: NotificationItem): boolean {
  const fromBooking =
    notification.related_object_type?.toLowerCase() === "booking_link" ||
    notification.metadata?.source === "booking_link";
  return fromBooking && notification.event_type === "meeting_participant_added";
}

/** `/book/<org>/<slug>` — the only page with a date/time picker. */
export function bookingInvitePath(notification: NotificationItem): string | null {
  const orgSlug = String(notification.metadata?.organization_slug || "").trim();
  const linkSlug = String(notification.metadata?.link_slug || "").trim();
  if (orgSlug && linkSlug) {
    return `/book/${encodeURIComponent(orgSlug)}/${encodeURIComponent(linkSlug)}`;
  }
  const fromAction = (notification.action_url || "").match(
    /^\/book\/([^/?#]+)\/([^/?#]+)/,
  );
  if (fromAction) {
    return `/book/${fromAction[1]}/${fromAction[2]}`;
  }
  return null;
}

export function extractNotificationProjectId(
  notification: NotificationItem
): number | string | undefined {
  const { metadata, action_url: actionUrl } = notification;
  const taskMeta = metadata?.task as Record<string, unknown> | undefined;
  const meetingMeta = metadata?.meeting as Record<string, unknown> | undefined;

  const raw =
    metadata?.project_id ??
    metadata?.project ??
    taskMeta?.project_id ??
    meetingMeta?.project_id;

  if (raw != null) {
    const projectId = Number(raw);
    if (Number.isFinite(projectId) && projectId > 0) return projectId;
    if (typeof raw === "string" && raw.trim()) return raw;
  }

  if (actionUrl) {
    return parseProjectIdFromActionUrl(actionUrl);
  }

  return undefined;
}

function translateLegacyActionUrl(actionUrl: string): NotificationNavigationTarget | null {
  const projectTask = actionUrl.match(/^\/projects\/([^/]+)\/tasks\/([^/]+)/);
  if (projectTask) {
    return { href: `/tasks/${projectTask[2]}`, requiresProjectSwitch: false };
  }

  const projectMeeting = actionUrl.match(/^\/projects\/([^/]+)\/meetings\/([^/]+)/);
  if (projectMeeting) {
    return {
      href: nestedProjectPath(projectMeeting[1], `/meetings/${projectMeeting[2]}`),
      requiresProjectSwitch: false,
    };
  }

  const projectDecision = actionUrl.match(/^\/projects\/([^/]+)\/decisions\/([^/]+)/);
  if (projectDecision) {
    return {
      href: nestedProjectPath(projectDecision[1], `/decisions/${projectDecision[2]}`),
      requiresProjectSwitch: false,
    };
  }

  const projectOnly = actionUrl.match(/^\/projects\/([^/]+)\/?$/);
  if (projectOnly) {
    const rawVal = projectOnly[1];
    const pid = Number(rawVal);
    return {
      href: "/overview",
      requiresProjectSwitch: true,
      projectId: Number.isFinite(pid) && pid > 0 ? pid : rawVal,
    };
  }

  if (actionUrl.startsWith("/") && !actionUrl.startsWith("//")) {
    const messagesHref = translateLegacyMessagesActionUrl(actionUrl);
    if (messagesHref) {
      return { href: messagesHref, requiresProjectSwitch: false };
    }
    try {
      const url = new URL(actionUrl, "http://local");
      url.searchParams.delete("project_id");
      url.searchParams.delete("projectId");
      url.searchParams.delete("chatId");
      const qs = url.searchParams.toString();
      const href = qs ? `${url.pathname}?${qs}` : url.pathname;
      return { href, requiresProjectSwitch: false };
    } catch {
      return { href: actionUrl, requiresProjectSwitch: false };
    }
  }

  return null;
}

export function buildNotificationFullPageTarget(
  notification: NotificationItem
): NotificationNavigationTarget | null {
  const {
    related_object_type,
    related_object_id,
    related_object_slug,
    metadata,
    action_url: actionUrl,
    event_type: eventType,
  } = notification;
  const objectType = related_object_type?.toLowerCase();

  if (
    eventType === NOTIFICATION_EVENT.CHAT_NEW_MESSAGE ||
    eventType === NOTIFICATION_EVENT.CHAT_NEW_CONVERSATION ||
    eventType === NOTIFICATION_EVENT.CHAT_MENTION
  ) {
    if (actionUrl) {
      const translated = translateLegacyActionUrl(actionUrl);
      if (translated) return translated;
      return { href: actionUrl, requiresProjectSwitch: false };
    }
    const chatSlug = metadata?.chat_slug as string | undefined;
    const chatId = metadata?.chat_id;
    const messageId = metadata?.message_id as number | undefined;
    if (chatSlug) {
      return {
        href: buildMessagesPath(chatSlug, { messageId: messageId ?? null }),
        requiresProjectSwitch: false,
      };
    }
    if (chatId) {
      return {
        href: `/messages?chatId=${chatId}`,
        requiresProjectSwitch: false,
      };
    }
    return { href: "/messages", requiresProjectSwitch: false };
  }

  const projectId = extractNotificationProjectId(notification);

  if (objectType === "project" && related_object_id) {
    const pid = Number(related_object_id);
    const resolvedProjectId = Number.isFinite(pid) && pid > 0 ? pid : String(related_object_id);
    return { href: "/overview", requiresProjectSwitch: true, projectId: resolvedProjectId };
  }

  if (objectType === "task" && related_object_id) {
    const taskKey = related_object_slug ?? related_object_id;
    return { href: `/tasks/${taskKey}`, requiresProjectSwitch: false };
  }

  if (objectType === "meeting" && related_object_id) {
    const meetingKey = related_object_slug ?? related_object_id;
    const href = projectId
      ? nestedProjectPath(projectId, `/meetings/${meetingKey}`)
      : `/meetings/${meetingKey}`;
    return { href, requiresProjectSwitch: false };
  }

  if (objectType === "decision" && related_object_id) {
    const decisionKey = related_object_slug ?? related_object_id;
    const href = projectId
      ? nestedProjectPath(projectId, `/decisions/${decisionKey}`)
      : `/decisions/${decisionKey}`;
    return { href, requiresProjectSwitch: false };
  }

  if (objectType === "booking_link") {
    // Named guest: the public page is where they pick a time. The owner
    // manager is empty for them. A host cancel without a re-invite is
    // notice-only — do not send them back to the picker.
    if (isBookingLinkInvite(notification)) {
      if (notification.metadata?.can_rebook === false) {
        return { href: "/calendar", requiresProjectSwitch: false };
      }
      const href = bookingInvitePath(notification);
      if (href) {
        return { href, requiresProjectSwitch: false };
      }
      return { href: "/calendar", requiresProjectSwitch: false };
    }
    return { href: "/calendar/booking-links", requiresProjectSwitch: false };
  }

  if (objectType === "budget_request") {
    const taskId = metadata?.task_slug ?? metadata?.task_id ?? related_object_slug ?? related_object_id;
    if (taskId) {
      return { href: `/tasks/${taskId}`, requiresProjectSwitch: false };
    }
  }

  if (actionUrl) {
    return translateLegacyActionUrl(actionUrl);
  }

  return null;
}

export async function activateProjectForNavigation(projectId: number | string): Promise<void> {
  const store = useProjectStore.getState();
  if (
    String(store.activeProject?.id) === String(projectId) ||
    store.activeProject?.slug === projectId
  )
    return;

  const fromStore = store.projects.find(
    (project) =>
      String(project.id) === String(projectId) || project.slug === projectId
  );
  if (fromStore) {
    store.setActiveProject(fromStore);
    return;
  }

  const project = await ProjectAPI.getProject(projectId);
  store.setActiveProject(project);
}
