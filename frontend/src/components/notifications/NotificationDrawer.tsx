"use client";

import React, { useEffect, useCallback } from "react";
import { useNotificationDrawer } from "./NotificationDrawerProvider";
import { notificationsApi } from "@/lib/api/notificationsApi";
import { useNotificationStore } from "@/lib/notificationStore";
import { NOTIFICATION_EVENT, type NotificationItem } from "@/types/notifications";
import DrawerHeader from "./drawer/DrawerHeader";
import DrawerActivitySummary from "./drawer/DrawerActivitySummary";
import DrawerObjectCard from "./drawer/DrawerObjectCard";
import DrawerInviteCard, { isInviteNotification } from "./drawer/DrawerInviteCard";
import DrawerWhatChanged from "./drawer/DrawerWhatChanged";
import DrawerNotificationCard, { isNotificationCard } from "./drawer/DrawerNotificationCard";
import DrawerActionBar from "./drawer/DrawerActionBar";
import DrawerChatView from "./drawer/DrawerChatView";

/**
 * Check if a notification is a chat/message notification that should render the chat view.
 */
function isChatNotification(notification: NotificationItem): boolean {
  return (
    notification.event_type === NOTIFICATION_EVENT.CHAT_NEW_MESSAGE ||
    notification.event_type === NOTIFICATION_EVENT.CHAT_NEW_CONVERSATION ||
    notification.event_type === NOTIFICATION_EVENT.CHAT_MENTION ||
    notification.event_type === NOTIFICATION_EVENT.CHAT_THREAD_REPLY ||
    notification.event_type === NOTIFICATION_EVENT.CHAT_REACTION
  );
}

export default function NotificationDrawer() {
  const { isOpen, notification, closeDrawer } = useNotificationDrawer();
  const { triggerRefresh } = useNotificationStore();

  // Handle escape key to close drawer
  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape" && isOpen) {
        closeDrawer();
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, closeDrawer]);

  // Prevent body scroll when drawer is open
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [isOpen]);

  // Mark notification as read when drawer opens
  const markAsRead = useCallback(async () => {
    if (!notification || notification.is_read) return;
    try {
      await notificationsApi.markRead({ ids: [notification.id] });
      // Trigger global refresh to update Header bell badge and page list
      triggerRefresh();
    } catch (error) {
      console.error("Failed to mark notification as read:", error);
    }
  }, [notification, triggerRefresh]);

  // Auto mark as read when drawer opens with an unread notification
  useEffect(() => {
    if (isOpen && notification && !notification.is_read) {
      markAsRead();
    }
  }, [isOpen, notification, markAsRead]);

  if (!isOpen || !notification) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-[9999]">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/40 transition-opacity"
        onClick={closeDrawer}
        aria-hidden="true"
      />

      {/* Drawer panel */}
      <div
        className="absolute right-0 top-0 h-full w-full max-w-md bg-white shadow-2xl flex flex-col rounded-l-xl animate-slide-in-right overflow-hidden"
        role="dialog"
        aria-modal="true"
        aria-labelledby="notification-drawer-title"
      >
        <div
          className="h-[3px] w-full shrink-0 bg-gradient-to-r from-[#3CCED7] to-[#A6E661]"
          aria-hidden
        />
        {/* Chat notifications get a special chat-based UI */}
        {isChatNotification(notification) ? (
          <DrawerChatView
            notification={notification}
            onClose={closeDrawer}
          />
        ) : (
          <>
            {/* Header */}
            <DrawerHeader
              notification={notification}
              onClose={closeDrawer}
            />

            {/* Scrollable content */}
            <div className="flex-1 overflow-y-auto">
              {/* Activity Summary (description) */}
              <DrawerActivitySummary notification={notification} />

              {/* Module card: Task / Meeting / Generic */}
              <DrawerObjectCard notification={notification} />

              {/* Type card — Invite (with Accept/Decline) */}
              {isInviteNotification(notification) && (
                <DrawerInviteCard
                  notification={notification}
                  onResponseComplete={(options) => {
                    triggerRefresh();
                    if (options?.navigated) {
                      closeDrawer();
                      return;
                    }
                    // Close drawer after a brief delay to allow user to see the response status
                    setTimeout(closeDrawer, 800);
                  }}
                />
              )}

              {/* Type card — Change (field-level diffs) */}
              {!isInviteNotification(notification) && (
                <DrawerWhatChanged notification={notification} />
              )}

              {/* Type card — Notification (reminders, alerts, announcements) */}
              {isNotificationCard(notification) && (
                <DrawerNotificationCard notification={notification} />
              )}
            </div>

            {/* Action Bar (sticky at bottom) */}
            <DrawerActionBar
              notification={notification}
              onActionComplete={closeDrawer}
            />
          </>
        )}
      </div>

      {/* Animation styles */}
      <style jsx>{`
        @keyframes slideInRight {
          from {
            transform: translateX(100%);
          }
          to {
            transform: translateX(0);
          }
        }
        .animate-slide-in-right {
          animation: slideInRight 0.3s ease-out forwards;
        }
      `}</style>
    </div>
  );
}
