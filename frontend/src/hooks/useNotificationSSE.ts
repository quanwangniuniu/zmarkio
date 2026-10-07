'use client';

/**
 * useNotificationSSE
 * ------------------
 * Establishes a persistent Server-Sent Events connection to
 * GET /api/notifications/stream/?token=<jwt>
 *
 * Responsibilities
 * ----------------
 * - Keeps the bell badge unread-count in sync via notificationStore.
 * - For chat-related events (chat_new_message, chat_new_conversation):
 *   also bumps chatStore.lastChatActivity so chat list components can
 *   refetch without polling.
 * - Page Visibility API:
 *     hidden  → closes the SSE connection (saves server goroutine / fd)
 *     visible → reopens and replays any missed events via Last-Event-ID
 * - Reconnects automatically on network error (exponential back-off up to
 *   30 s) and always uses the latest token from authStore.
 *
 * Usage
 * -----
 * Mount once at the app-shell level (e.g. ChatWidget or a top-level layout
 * component that is rendered while the user is logged in).
 */

import { useCallback, useEffect, useRef } from 'react';
import { getValidAccessToken, isAccessTokenExpiring } from '@/lib/api';
import { useAuthStore } from '@/lib/authStore';
import { useChatStore } from '@/lib/chatStore';
import { useNotificationStore } from '@/lib/notificationStore';
import { notificationsApi } from '@/lib/api/notificationsApi';

/** Event types that should also trigger a chat list refresh. */
const CHAT_EVENT_TYPES = new Set([
  'chat_new_message',
  'chat_new_conversation',
]);

const MIN_RETRY_MS = 3_000;
const MAX_RETRY_MS = 30_000;
const RETRY_JITTER_RATIO = 0.2;
const RECENT_EVENT_ID_LIMIT = 1_000;

function jitteredRetryDelay(baseMs: number): number {
  const spread = baseMs * RETRY_JITTER_RATIO;
  return Math.round(baseMs - spread + Math.random() * spread * 2);
}

export function useNotificationSSE(): void {
  const token = useAuthStore((state) => state.token);

  // Keep a stable reference to the current token so the event-source
  // teardown / reconnect path always uses the freshest value.
  const tokenRef = useRef<string | null>(null);
  tokenRef.current = token ?? null;

  // Track the Last-Event-ID for reconnect replay.
  const lastEventIdRef = useRef<string>('');
  const lastEventCreatedAtRef = useRef<string>('');
  const recentEventIdsRef = useRef<string[]>([]);
  const recentEventIdSetRef = useRef<Set<string>>(new Set());

  // Retry back-off state.
  const retryMsRef = useRef(MIN_RETRY_MS);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const stoppedRef = useRef(false);
  // Token this hook just refreshed to; connect with it even if it still looks
  // expiring, so a wrong expiry guess can't cause back-to-back refreshes.
  const justRefreshedTokenRef = useRef<string | null>(null);

  const refreshUnreadCount = useCallback(async () => {
    try {
      const { data } = await notificationsApi.list({ page_size: 1 });
      useNotificationStore.getState().setUnreadCount(data.unread_count);
    } catch {
      // non-critical – bell badge will sync on next successful call
    }
  }, []);

  useEffect(() => {
    const setConnectionStatus = useNotificationStore.getState().setConnectionStatus;

    if (!token) {
      setConnectionStatus('disconnected');
      return;
    }
    if (typeof window === 'undefined') return;

    stoppedRef.current = false;
    // Set when this effect run is torn down (unmount, or a new token re-ran it),
    // so a refresh that resolves afterwards doesn't act on the newer run's state.
    let disposed = false;

    const close = () => {
      if (esRef.current) {
        esRef.current.close();
        esRef.current = null;
      }
    };

    const connect = () => {
      if (stoppedRef.current) return;
      const currentToken = tokenRef.current;
      if (!currentToken) {
        setConnectionStatus('disconnected');
        return;
      }

      // EventSource can't report a 401, so refresh an expiring token before
      // connecting. Putting the new token in the store re-runs this effect
      // with it (it may have come from another tab, so no refresh event).
      const justRefreshed = currentToken === justRefreshedTokenRef.current;
      justRefreshedTokenRef.current = null;
      if (!justRefreshed && isAccessTokenExpiring(currentToken)) {
        void getValidAccessToken().then((freshToken) => {
          if (disposed) return;
          if (!freshToken) {
            scheduleReconnect();
            return;
          }
          justRefreshedTokenRef.current = freshToken;
          // Same token back (e.g. nothing to refresh with): the store won't
          // change, so connect directly and let the server decide.
          if (freshToken === currentToken) connect();
          else useAuthStore.getState().setToken(freshToken);
        });
        return;
      }

      close(); // ensure no stale connection
      setConnectionStatus(
        retryMsRef.current === MIN_RETRY_MS ? 'connecting' : 'reconnecting',
      );

      // Build URL – token goes in the query string because the native
      // EventSource API cannot set custom headers.
      const params = new URLSearchParams({ token: currentToken });
      if (lastEventIdRef.current) {
        params.set('lastEventId', lastEventIdRef.current);
        if (lastEventCreatedAtRef.current) {
          params.set('lastEventCreatedAt', lastEventCreatedAtRef.current);
        }
      }
      const url = `/api/notifications/stream/?${params.toString()}`;

      const es = new EventSource(url);
      esRef.current = es;

      es.onopen = () => {
        retryMsRef.current = MIN_RETRY_MS; // reset back-off on success
        setConnectionStatus('connected');
      };

      es.onmessage = (event: MessageEvent<string>) => {
        let payload: {
          type: string;
          data?: {
            created_at?: string;
            event_type?: string;
            metadata?: unknown;
          };
        };
        try {
          payload = JSON.parse(event.data) as typeof payload;
        } catch {
          console.warn('[NotificationSSE] malformed message — could not parse JSON:', event.data);
          return;
        }

        if (payload.type !== 'notification') {
          return;
        }

        const eventId = event.lastEventId;
        if (eventId && recentEventIdSetRef.current.has(eventId)) {
          return;
        }

        // Keep the UUID as the primary cursor and created_at as a fallback if
        // the notification row is deleted before the next reconnect.
        if (eventId) {
          lastEventIdRef.current = eventId;
          recentEventIdsRef.current.push(eventId);
          recentEventIdSetRef.current.add(eventId);
          if (recentEventIdsRef.current.length > RECENT_EVENT_ID_LIMIT) {
            const expiredId = recentEventIdsRef.current.shift();
            if (expiredId) recentEventIdSetRef.current.delete(expiredId);
          }
        }
        if (typeof payload.data?.created_at === 'string') {
          lastEventCreatedAtRef.current = payload.data.created_at;
        }

        const eventType = payload.data?.event_type ?? '';

        // Refresh the notification bell badge.
        useNotificationStore.getState().triggerRefresh();
        void refreshUnreadCount();

        // For chat events: signal chat components to refresh their lists.
        if (CHAT_EVENT_TYPES.has(eventType)) {
          useChatStore.getState().incrementGlobalUnreadCount();
          useChatStore.getState().triggerChatActivity();
        }

        // For @-mention notifications: flag the chat in the sidebar.
        if (eventType === 'chat_mention') {
          const chatId = (payload.data?.metadata as { chat_id?: number } | undefined)?.chat_id;
          if (typeof chatId === 'number') {
            useChatStore.getState().addMentionedChat(chatId);
          }
        }
      };

      // EventSource.onerror fires on any connection problem.
      // We close the EventSource here (cancelling the browser's built-in
      // auto-reconnect) and schedule our own reconnect with back-off so we
      // always reconnect with the freshest token.
      es.onerror = () => {
        if (stoppedRef.current) return;
        close();
        scheduleReconnect();
      };
    };

    const scheduleReconnect = () => {
      setConnectionStatus('reconnecting');

      const retryDelay = jitteredRetryDelay(retryMsRef.current);
      if (process.env.NODE_ENV === 'development') {
        console.warn(
          `[NotificationSSE] error – reconnecting in ${retryDelay}ms`,
        );
      }

      if (retryTimerRef.current) clearTimeout(retryTimerRef.current);
      retryTimerRef.current = setTimeout(() => {
        retryMsRef.current = Math.min(retryMsRef.current * 2, MAX_RETRY_MS);
        connect();
      }, retryDelay);
    };

    connect();

    // ── Page Visibility API ────────────────────────────────────────────
    // When the user hides the tab we close the SSE connection (saves one
    // server-side goroutine + Redis subscription per hidden tab).
    // When the tab becomes visible again we reconnect immediately, which
    // triggers the Last-Event-ID replay on the backend so no events are lost.
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'hidden') {
        if (retryTimerRef.current) {
          clearTimeout(retryTimerRef.current);
          retryTimerRef.current = null;
        }
        close();
        setConnectionStatus('disconnected');
      } else {
        // visible – reconnect and pick up any missed events
        connect();
        // Eagerly sync the unread count in case we missed notifications
        // while the tab was hidden.
        void refreshUnreadCount();
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      disposed = true;
      stoppedRef.current = true;
      if (retryTimerRef.current) {
        clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      close();
      setConnectionStatus('disconnected');
    };
  }, [token, refreshUnreadCount]);
}
