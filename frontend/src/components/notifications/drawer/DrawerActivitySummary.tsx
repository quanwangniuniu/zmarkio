"use client";

import React from "react";
import type { NotificationItem } from "@/types/notifications";
import {
  getDecisionPublishedSVODescription,
  getDecisionReviewSVODescription,
} from "@/lib/decisionNotificationCopy";
import {
  getBudgetSVODescription,
  isBudgetNotification,
} from "@/lib/budgetNotificationCopy";
import {
  getTaskChangeActivityDescription,
  getTaskStatusChangeSVODescription,
  isTaskDueDateEditNotification,
} from "@/lib/taskNotificationCopy";
import { isBookingLinkInvite } from "@/lib/notificationRoutes";

interface DrawerActivitySummaryProps {
  notification: NotificationItem;
}

// Small inline chip: avatar circle + bold name
function ActorChip({ name, avatar }: { name: string; avatar?: string | null }) {
  return (
    <span className="inline-flex items-center gap-1 align-middle">
      <span className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-[#3CCED7] to-[#A6E661] text-[10px] font-semibold text-white overflow-hidden">
        {avatar ? (
          <img src={avatar} alt={name} className="w-full h-full object-cover" />
        ) : (
          name.charAt(0).toUpperCase()
        )}
      </span>
      <span className="font-semibold text-gray-900">{name}</span>
    </span>
  );
}

// Returns a ReactNode description. `actor` is null for system/automated events.
function getActivityDescription(
  notification: NotificationItem,
  actor: React.ReactNode
): React.ReactNode {
  const { event_type, metadata, title, body } = notification;

  // Fallback plain name used only for cases that need a string (e.g. chat starter)
  const actorName =
    notification.actor_name ||
    (metadata?.actor_name as string) ||
    (metadata?.sender_name as string) ||
    "Someone";

  switch (event_type) {
    // Task events
    case "task_assigned":
      return <>{actor} assigned you to this task.</>;
    case "task_owner_changed": {
      const fieldChange = getTaskChangeActivityDescription(notification, actor);
      if (fieldChange) return fieldChange;
      return <>Task ownership was transferred to you by {actor}.</>;
    }
    case "task_status_changed":
      if (actor) {
        return getTaskStatusChangeSVODescription(notification, actor);
      }
      {
        const oldStatus = (metadata?.old_status as string) || "unknown";
        const newStatus = (metadata?.new_status as string) || "unknown";
        return (
          <>
            Task status changed from{" "}
            <span className="font-medium">{oldStatus.replace(/_/g, " ")}</span> to{" "}
            <span className="font-medium">{newStatus.replace(/_/g, " ")}</span>.
          </>
        );
      }
    case "task_deadline_soon":
      if (isTaskDueDateEditNotification(notification) && actor) {
        return getTaskChangeActivityDescription(notification, actor);
      }
      return <>This task is due soon. Don&apos;t forget to complete it!</>;
    case "task_overdue":
      return <>This task is now overdue. Please take action.</>;
    case "task_comment_mention":
      return <>{actor} mentioned you in a comment.</>;
    case "chat_mention":
      return <>{actor} mentioned you in a message.</>;
    case "task_anomaly":
      return <>An anomaly was detected in this task.</>;

    // Meeting events
    case "meeting_created":
      return <>{actor} created a new meeting.</>;
    case "meeting_updated":
      if (notification.metadata?.cancelled_by_host && notification.metadata?.can_rebook === false) {
        return <>{actor} cancelled your booking after changing availability.</>;
      }
      if (notification.metadata?.source === "booking_link") {
        return <>{actor} updated a booking.</>;
      }
      return <>{actor} updated the meeting details.</>;
    case "meeting_participant_added":
      if (isBookingLinkInvite(notification)) {
        if (notification.metadata?.cancelled_by_host) {
          return <>{actor} updated availability — pick a new time.</>;
        }
        return <>{actor} invited you to book a time.</>;
      }
      return <>You were added as a participant to this meeting.</>;
    case "meeting_participant_removed":
      return <>{actor} removed you from this meeting.</>;
    case "meeting_starting_soon":
      return <>This meeting is starting soon. Be ready to join!</>;
    case "meeting_agenda_changed":
      return <>{actor} updated the meeting agenda.</>;
    case "meeting_minutes_published":
      return <>Meeting minutes have been published.</>;

    // Decision events
    case "decision_review_needed":
      return actor
        ? getDecisionReviewSVODescription(notification, actor)
        : <>A decision requires your review.</>;
    case "decision_deadline":
      return <>Decision deadline is approaching.</>;
    case "decision_published":
      return actor
        ? getDecisionPublishedSVODescription(notification, actor)
        : <>A decision has been published.</>;

    // Budget events
    case "budget_review_needed":
    case "budget_approval_result":
    case "budget_pool_low":
    case "budget_escalation":
      if (isBudgetNotification(notification)) {
        return getBudgetSVODescription(notification, actor);
      }
      return <>Budget approval decision has been made.</>;

    // Chat events
    case "chat_new_message":
      return <>{actor} sent you a message.</>;
    case "chat_new_conversation": {
      const starter =
        notification.actor_name ||
        (metadata?.sender_name as string) ||
        (metadata?.actor_name as string) ||
        actorName;
      const starterChip = notification.actor_name ? (
        <ActorChip
          name={notification.actor_name}
          avatar={notification.actor_avatar}
        />
      ) : (
        <span className="font-semibold text-gray-900">{starter}</span>
      );
      const convTitle = (metadata?.conversation_title as string) || "";
      if (convTitle && convTitle !== "Direct message") {
        return <>{starterChip} started &ldquo;{convTitle}&rdquo;.</>;
      }
      return <>{starterChip} started a new conversation with you.</>;
    }

    // Collaboration events
    case "project_invite":
      return <>You&apos;ve been invited to join a project.</>;
    case "calendar_reminder":
      return <>Calendar reminder for an upcoming event.</>;
    case "doc_asset_update": {
      const mt = metadata as {
        meeting_id?: number;
        old_title?: string;
        new_title?: string;
      };
      const meetingTitle = mt?.new_title || mt?.old_title;
      if (meetingTitle) {
        return (
          <>
            The collaborative document for &ldquo;{meetingTitle}&rdquo; was edited.
          </>
        );
      }
      if (mt?.meeting_id != null) {
        return <>Meeting document was updated (meeting #{mt.meeting_id}).</>;
      }
      return <>A collaborative document was updated.</>;
    }

    // Approval / system events
    case "workflow_node":
      return <>Workflow automation update.</>;
    case "account_permission":
      if (metadata?.action === "removed_from_project") {
        return <>You were removed from this project.</>;
      }
      if (metadata?.action === "project_owner_transferred") {
        return <>{actor} transferred project ownership to you.</>;
      }
      return <>Your account permissions have changed.</>;
    case "billing_anomaly":
      return <>A billing anomaly was detected.</>;
    case "system":
      return <>{body || "System notification."}</>;

    default:
      return <>{body || title || "You have a new notification."}</>;
  }
}

// Render chat message bubble for chat notifications
function renderChatBubble(notification: NotificationItem) {
  const { event_type, metadata } = notification;

  if (
    event_type !== "chat_new_message" &&
    event_type !== "task_comment_mention" &&
    event_type !== "chat_mention"
  ) {
    return null;
  }

  const senderName =
    notification.actor_name ||
    (metadata?.sender_name as string) ||
    (metadata?.actor_name as string) ||
    "Unknown";
  const senderAvatar = notification.actor_avatar;
  const messagePreview = (metadata?.message_preview as string) || notification.body;

  if (!messagePreview) return null;

  return (
    <div className="mt-4 rounded-lg border border-[#3CCED7]/20 bg-gradient-to-r from-[#3CCED7]/6 to-[#A6E661]/6 p-3">
      <div className="flex items-start gap-2">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-[#3CCED7] to-[#A6E661] text-sm font-medium text-white overflow-hidden">
          {senderAvatar ? (
            <img
              src={senderAvatar}
              alt={senderName}
              className="w-full h-full object-cover"
            />
          ) : (
            senderName.charAt(0).toUpperCase()
          )}
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-gray-900">{senderName}</div>
          <div className="text-sm text-gray-600 mt-0.5 break-words">
            {messagePreview}
          </div>
        </div>
      </div>
    </div>
  );
}

export default function DrawerActivitySummary({
  notification,
}: DrawerActivitySummaryProps) {
  const actorNode = notification.actor_name ? (
    <ActorChip
      name={notification.actor_name}
      avatar={notification.actor_avatar}
    />
  ) : null;

  const description = getActivityDescription(notification, actorNode);

  return (
    <div className="px-5 py-4">
      {/* Drawer section heading — blend ~midpoint between brand teal #3CCED7 and lime #A6E661 → #71DA9C */}
      <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-[#71DA9C]">
        Activity Summary
      </div>

      {/* Title (bold) */}
      <p className="text-base font-bold text-gray-900 leading-relaxed">
        {notification.title}
      </p>
      {/* SVO Description with inline ActorChip (unbolded) */}
      <p className="text-sm text-gray-600 mt-1 leading-relaxed">{description}</p>

      {/* Chat bubble for message notifications */}
      {renderChatBubble(notification)}
    </div>
  );
}
