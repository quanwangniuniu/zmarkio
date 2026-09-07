'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { buildWsUrl } from '@/lib/ws';
import { useAuthStore } from '@/lib/authStore';
import { useChatStore } from '@/lib/chatStore';
import type { MessageLinkPreview } from '@/types/chat';

// WebSocket message types (server -> client)
export type ChatWsEventType =
  | 'chat_message'
  | 'typing_indicator'
  | 'message_status_update'
  | 'reaction_update'
  | 'pin_update'
  | 'link_preview'
  | 'presence_update'
  | 'presence_snapshot'
  | 'in_app_notification'
  | 'user_session_revoked'
  | 'error'
  | 'outbox_ack'
  | 'pong'
  | string;

export interface ChatWsEvent<T = any> {
  type: ChatWsEventType;
  payload?: T;
  // Specific fields for different event types
  chat_id?: number;
  user_id?: number;
  is_online?: boolean;
  is_typing?: boolean;
  message?: any;
  message_id?: number;
  status?: string;
  reaction?: any;
  action?: 'pinned' | 'unpinned';
  preview?: MessageLinkPreview | null;
  pin?: any;
  notification?: any;
  timestamp?: string;
  version?: number | null;
  users?: Array<{ user_id: number; is_online: boolean; version?: number | null }>;
  committed?: Array<{ client_message_id: string; message_id: number }>;
}

export interface UseChatWebSocketHandlers {
  onChatMessage?: (e: ChatWsEvent) => void;
  onTypingIndicator?: (e: ChatWsEvent) => void;
  onMessageStatusUpdate?: (e: ChatWsEvent) => void;
  onReactionUpdate?: (e: ChatWsEvent) => void;
  onPinUpdate?: (e: ChatWsEvent) => void;
  onLinkPreview?: (e: ChatWsEvent) => void;
  onPresenceUpdate?: (e: ChatWsEvent) => void;
  onPresenceSnapshot?: (e: ChatWsEvent) => void;
  onInAppNotification?: (e: ChatWsEvent) => void;
  onError?: (e: ChatWsEvent) => void;
  onUnknownEvent?: (e: ChatWsEvent) => void;
  onOpen?: () => void;
  onOutboxAck?: (e: ChatWsEvent) => void;
  onClose?: (ev: CloseEvent) => void;
  onConnectionError?: (ev: Event) => void;
}

/**
 * WebSocket hook for real-time chat functionality.
 * Connects to /ws/chat/{user_id}/ and handles chat events.
 */
export function useChatWebSocket(
  userId: number | null | undefined,
  handlers: UseChatWebSocketHandlers = {}
) {
  const { token } = useAuthStore();
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const retryRef = useRef(0);
  const heartbeatIntervalRef = useRef<NodeJS.Timeout | null>(null);
  const shouldRun = useMemo(() => !!userId, [userId]);
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  useEffect(() => {
    if (!shouldRun) return;

    let stopped = false;

    const connect = () => {
      if (stopped) return;

      const url = buildWsUrl(`/ws/chat/${userId}/`, token ? { token } : undefined);
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
        retryRef.current = 0;

        const store = useChatStore.getState();
        const digest = store.getOutboxDigest();
        if (digest.length > 0) {
          ws.send(JSON.stringify({ type: 'outbox_digest', client_message_ids: digest }));
        }
        void store.flushOutbox();
        handlersRef.current.onOpen?.();

        // Start heartbeat every 30 seconds to keep connection alive
        heartbeatIntervalRef.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'heartbeat' }));
          }
        }, 30000);
      };

      ws.onmessage = (ev) => {
        try {
          const data: ChatWsEvent = JSON.parse(ev.data);

          switch (data.type) {
            case 'chat_message':
              handlersRef.current.onChatMessage?.(data);
              break;
            case 'typing_indicator':
              handlersRef.current.onTypingIndicator?.(data);
              break;
            case 'message_status_update':
              handlersRef.current.onMessageStatusUpdate?.(data);
              break;
            case 'reaction_update':
              handlersRef.current.onReactionUpdate?.(data);
              break;
            case 'pin_update':
              if (data.action === 'pinned') {
                const chatId = Number(data.chat_id);
                if (Number.isFinite(chatId)) {
                  useChatStore.getState().markChatPinUnseen(chatId);
                }
              }
              handlersRef.current.onPinUpdate?.(data);
              break;
            case 'link_preview': {
              // Attach centrally: a preview must land on the message whether or
              // not the chat window that owns it is currently mounted.
              const messageId = Number(data.message_id);
              if (Number.isFinite(messageId) && data.preview) {
                useChatStore.getState().applyLinkPreview(messageId, data.preview);
              }
              handlersRef.current.onLinkPreview?.(data);
              break;
            }
            case 'presence_update':
              handlersRef.current.onPresenceUpdate?.(data);
              break;
            case 'presence_snapshot':
              handlersRef.current.onPresenceSnapshot?.(data);
              break;
            case 'in_app_notification':
              handlersRef.current.onInAppNotification?.(data);
              break;
            case 'user_session_revoked':
              stopped = true;
              useAuthStore.getState().clearAuth();
              setConnected(false);
              try {
                ws.close(4001, data.type);
              } catch {}
              if (typeof window !== 'undefined' && window.location.pathname !== '/login') {
                window.location.href = '/login';
              }
              break;
            case 'error':
              handlersRef.current.onError?.(data);
              break;
            case 'outbox_ack':
              handlersRef.current.onOutboxAck?.(data);
              break;
            case 'pong':
              // Heartbeat response, ignore
              break;
            default:
              handlersRef.current.onUnknownEvent?.(data);
          }
        } catch (e) {
          console.warn('[ChatWS] message parse error', e);
        }
      };

      ws.onerror = (ev) => {
        console.error('[ChatWS] error', ev);
        handlersRef.current.onConnectionError?.(ev);
      };

      ws.onclose = (ev) => {
        // A superseded socket can finish closing after a replacement socket
        // has already been assigned to the shared ref (notably during React
        // Strict Mode's development-only effect replay). Never let that stale
        // close clear the live socket or its heartbeat.
        if (wsRef.current !== ws) return;

        console.warn('[ChatWS] close', { code: ev.code, reason: ev.reason });
        setConnected(false);
        handlersRef.current.onClose?.(ev);
        wsRef.current = null;

        // Clear heartbeat
        if (heartbeatIntervalRef.current) {
          clearInterval(heartbeatIntervalRef.current);
          heartbeatIntervalRef.current = null;
        }

        // Reconnect with exponential backoff
        if (!stopped) {
          const retry = Math.min(1000 * Math.pow(2, retryRef.current++), 10000);
          setTimeout(connect, retry);
        }
      };
    };

    connect();

    return () => {
      stopped = true;
      if (heartbeatIntervalRef.current) {
        clearInterval(heartbeatIntervalRef.current);
      }
      if (wsRef.current) {
        try {
          wsRef.current.close();
        } catch {}
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shouldRun, userId, token]);

  /**
   * Send typing start event
   */
  const sendTypingStart = (chatId: number) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({
          type: 'typing_start',
          chat_id: chatId,
        })
      );
    }
  };

  /**
   * Send typing stop event
   */
  const sendTypingStop = (chatId: number) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({
          type: 'typing_stop',
          chat_id: chatId,
        })
      );
    }
  };

  const sendOutboxDigest = (clientMessageIds: string[]) => {
    if (!clientMessageIds.length) return;
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({
          type: 'outbox_digest',
          client_message_ids: clientMessageIds,
        }),
      );
    }
  };

  return {
    connected,
    sendTypingStart,
    sendTypingStop,
    sendOutboxDigest,
  };
}
