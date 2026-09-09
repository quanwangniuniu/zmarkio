"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import { Check, X, Send, ExternalLink, Loader2 } from "lucide-react";
import toast from "react-hot-toast";
import type { NotificationItem } from "@/types/notifications";
import { TaskAPI } from "@/lib/api/taskApi";
import { budgetChangeType, isBudgetNotification } from "@/lib/budgetNotificationCopy";
import {
  activateProjectForNavigation,
  buildNotificationFullPageTarget,
  isBookingLinkInvite,
} from "@/lib/notificationRoutes";
import { bookingInvitePickLabel } from "@/lib/bookingInviteState";
import { buildUrl } from "@/lib/buildUrl";

interface DrawerActionBarProps {
  notification: NotificationItem;
  onActionComplete: () => void;
}

type ActionType = "approval" | "quick_reply" | "default";

/** Event types that previously showed invite-style Accept / Decline — now handled by DrawerInviteCard. */
const INVITE_EVENT_TYPES = new Set([
  "project_invite",
  "meeting_participant_added",
]);

function isBudgetReviewNotification(notification: NotificationItem): boolean {
  if (!isBudgetNotification(notification)) return false;
  const changeType = budgetChangeType(notification);
  return (
    notification.event_type === "budget_review_needed" ||
    changeType === "budget_submitted" ||
    changeType === "budget_forwarded"
  );
}

/**
 * Determine the action type for a notification.
 * Invite / assignment types are handled upstream by DrawerInviteCard.
 */
function getActionType(notification: NotificationItem): ActionType {
  const et = notification.event_type;
  const changeType = notification.metadata?.change_type as string | undefined;

  // Response feedback notifications — no action buttons
  if (notification.metadata?.is_response_feedback) return "default";

  // Invite events are handled by DrawerInviteCard — skip here
  if (INVITE_EVENT_TYPES.has(et)) return "default";
  if (et === "task_owner_changed") return "default";
  if (et === "task_assigned" && (changeType === "task_assignee" || changeType === "task_approver")) {
    return "default";
  }

  if (isBudgetReviewNotification(notification)) {
    return "approval";
  }

  // Existing task-approval workflow (legacy paths)
  if (et === "task_assigned" || et === "decision_review_needed") {
    return "approval";
  }

  if (
    et === "chat_new_message" ||
    et === "chat_new_conversation" ||
    et === "chat_mention" ||
    et === "task_comment_mention"
  ) {
    return "quick_reply";
  }

  return "default";
}

// Approval Actions Component
function ApprovalActions({
  notification,
  onComplete,
}: {
  notification: NotificationItem;
  onComplete: () => void;
}) {
  const [loading, setLoading] = useState<"approve" | "reject" | null>(null);

  const handleApproval = async (action: "approve" | "reject") => {
    // Task action routes are slug-only; prefer the slug, fall back to the raw id.
    const taskId = notification.related_object_slug ?? notification.related_object_id;
    if (!taskId) {
      toast.error("Invalid task ID");
      return;
    }

    setLoading(action);
    try {
      await TaskAPI.makeApproval(taskId, { action, comment: "" });
      toast.success(action === "approve" ? "Task approved!" : "Task rejected");
      onComplete();
    } catch (error) {
      console.error("Approval action failed:", error);
      toast.error(`Failed to ${action} task`);
    } finally {
      setLoading(null);
    }
  };

  return (
    <div className="flex gap-3">
      <button
        type="button"
        onClick={() => handleApproval("approve")}
        disabled={loading !== null}
        className="flex-1 inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-gradient-to-r from-[#3CCED7] to-[#A6E661] text-white font-medium shadow-sm transition hover:opacity-95 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {loading === "approve" ? (
          <Loader2 className="w-4 h-4 animate-spin" />
        ) : (
          <Check className="w-4 h-4" />
        )}
        Approve
      </button>
      <button
        type="button"
        onClick={() => handleApproval("reject")}
        disabled={loading !== null}
        className="flex-1 inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-red-600 text-white font-medium hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
      >
        {loading === "reject" ? (
          <Loader2 className="w-4 h-4 animate-spin" />
        ) : (
          <X className="w-4 h-4" />
        )}
        Reject
      </button>
    </div>
  );
}

function BudgetApprovalActions({
  notification,
  onComplete,
}: {
  notification: NotificationItem;
  onComplete: () => void;
}) {
  const [loading, setLoading] = useState<"approve" | "reject" | null>(null);
  const taskId = Number(notification.metadata?.task_id);

  const handleApproval = async (action: "approve" | "reject") => {
    if (!Number.isFinite(taskId)) {
      toast.error("Invalid budget task ID");
      return;
    }

    setLoading(action);
    try {
      await TaskAPI.makeApproval(taskId, { action, comment: "" });
      toast.success(action === "approve" ? "Budget approved!" : "Budget rejected");
      onComplete();
    } catch (error) {
      console.error("Budget approval action failed:", error);
      toast.error(`Failed to ${action} budget request`);
    } finally {
      setLoading(null);
    }
  };

  return (
    <div className="flex gap-3">
      <button
        type="button"
        onClick={() => handleApproval("approve")}
        disabled={loading !== null}
        className="flex-1 inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-gradient-to-r from-[#3CCED7] to-[#A6E661] text-white font-medium shadow-sm transition hover:opacity-95 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        {loading === "approve" ? (
          <Loader2 className="w-4 h-4 animate-spin" />
        ) : (
          <Check className="w-4 h-4" />
        )}
        Approve
      </button>
      <button
        type="button"
        onClick={() => handleApproval("reject")}
        disabled={loading !== null}
        className="flex-1 inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-lg bg-red-600 text-white font-medium hover:bg-red-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
      >
        {loading === "reject" ? (
          <Loader2 className="w-4 h-4 animate-spin" />
        ) : (
          <X className="w-4 h-4" />
        )}
        Reject
      </button>
    </div>
  );
}

// Quick Reply Component
function QuickReplyAction({
  notification,
  onComplete,
}: {
  notification: NotificationItem;
  onComplete: () => void;
}) {
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const router = useRouter();

  const handleSend = async () => {
    if (!message.trim()) return;

    setLoading(true);
    try {
      const target = buildNotificationFullPageTarget(notification);
      if (!target) {
        toast.error("Unable to open conversation");
        return;
      }

      onComplete();
      if (target.requiresProjectSwitch && target.projectId) {
        await activateProjectForNavigation(target.projectId);
      }
      router.push(buildUrl(target.href));
      toast.success("Redirecting to conversation...");
    } catch (error) {
      console.error("Reply failed:", error);
      toast.error("Failed to send reply");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex gap-2">
      <input
        type="text"
        value={message}
        onChange={(e) => setMessage(e.target.value)}
        placeholder="Type a quick reply..."
        className="flex-1 px-3 py-2 border border-gray-300 rounded-lg text-sm outline-none focus:border-[#3CCED7] focus:ring-2 focus:ring-[#3CCED7]/30"
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            handleSend();
          }
        }}
      />
      <button
        type="button"
        onClick={handleSend}
        disabled={loading || !message.trim()}
        className="px-4 py-2 rounded-lg bg-blue-600 text-white font-medium hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
      >
        {loading ? (
          <Loader2 className="w-4 h-4 animate-spin" />
        ) : (
          <Send className="w-4 h-4" />
        )}
      </button>
    </div>
  );
}

// Go to Full Page Link
function GoToFullPageLink({
  notification,
  onNavigate,
}: {
  notification: NotificationItem;
  onNavigate: () => void;
}) {
  const router = useRouter();
  const target = buildNotificationFullPageTarget(notification);

  // Hide button if user's access to the resource has been revoked
  if (notification.metadata?.revoked_access === true) return null;

  if (!target) return null;

  const bookingLabel = isBookingLinkInvite(notification)
    ? bookingInvitePickLabel(notification)
    : null;
  if (isBookingLinkInvite(notification) && !bookingLabel) return null;

  const handleClick = async () => {
    onNavigate();
    if (target.requiresProjectSwitch && target.projectId) {
      await activateProjectForNavigation(target.projectId);
    }
    router.push(buildUrl(target.href));
  };

  return (
    <button
      type="button"
      onClick={handleClick}
      className="w-full inline-flex items-center justify-center gap-2 rounded-lg bg-gradient-to-r from-[#3CCED7] to-[#A6E661] px-4 py-3 font-medium text-white shadow-sm transition hover:opacity-95"
    >
      <span>{bookingLabel ?? "Go to Full Page"}</span>
      <ExternalLink className="w-4 h-4" />
    </button>
  );
}

export default function DrawerActionBar({
  notification,
  onActionComplete,
}: DrawerActionBarProps) {
  const actionType = getActionType(notification);

  return (
    <div className="px-5 py-4 border-t border-gray-200 bg-gray-50 space-y-3 shrink-0">

      {/* ── Budget approval ── */}
      {actionType === "approval" && isBudgetReviewNotification(notification) && (
        <BudgetApprovalActions notification={notification} onComplete={onActionComplete} />
      )}

      {/* ── Existing task-workflow approval ── */}
      {actionType === "approval" &&
        notification.related_object_type === "task" &&
        !isBudgetReviewNotification(notification) && (
        <ApprovalActions notification={notification} onComplete={onActionComplete} />
      )}

      {/* ── Chat / mention quick reply ── */}
      {actionType === "quick_reply" && (
        <QuickReplyAction notification={notification} onComplete={onActionComplete} />
      )}

      {/* ── Always: Go to Full Page ── */}
      <GoToFullPageLink notification={notification} onNavigate={onActionComplete} />
    </div>
  );
}
