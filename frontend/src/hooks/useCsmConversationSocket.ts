'use client';

import { useEffect, useRef, useCallback } from 'react';
import { buildWsUrl } from '@/lib/ws';
import { useAuthStore } from '@/lib/authStore';
import { useCsmConversationStore } from '@/lib/csmConversationStore';
import type { CsmWsEvent } from '@/types/csmConversation';

export function useCsmConversationSocket(activeConversationId: number | null) {
  const user = useAuthStore((s) => s.user);
  const token = useAuthStore((s) => s.token);
  const wsRef = useRef<WebSocket | null>(null);
  const activeConvRef = useRef<number | null>(activeConversationId);

  const addMessage = useCsmConversationStore((s) => s.addMessage);
  const updateConversation = useCsmConversationStore((s) => s.updateConversation);
  const setTyping = useCsmConversationStore((s) => s.setTyping);
  const bumpGuidance = useCsmConversationStore((s) => s.bumpGuidance);

  // Keep ref in sync so the stable send function always sees latest conversation id
  useEffect(() => {
    activeConvRef.current = activeConversationId;
  }, [activeConversationId]);

  const send = useCallback((data: object) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  // Join/leave conversation groups when active conversation changes
  // send() silently drops if WS isn't open yet; ws.onopen re-sends via activeConvRef
  useEffect(() => {
    if (!activeConversationId) return;
    // If WS is already open, join immediately; otherwise onopen will handle it
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'join_conversation', conversation_id: activeConversationId }));
    }

    return () => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: 'leave_conversation', conversation_id: activeConversationId }));
      }
    };
  }, [activeConversationId]);

  // Establish WebSocket connection
  useEffect(() => {
    if (!user?.id || !token) return;

    const url = buildWsUrl(`/ws/csm/agent/${user.id}/`, { token });
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      // If there's already an active conversation, join it immediately
      if (activeConvRef.current) {
        ws.send(JSON.stringify({ type: 'join_conversation', conversation_id: activeConvRef.current }));
      }
    };

    ws.onmessage = (event) => {
      let parsed: CsmWsEvent;
      try {
        parsed = JSON.parse(event.data) as CsmWsEvent;
      } catch {
        return;
      }

      if (parsed.type === 'new_message') {
        addMessage(parsed.message.conversation, parsed.message);
      } else if (parsed.type === 'new_conversation') {
        // Prepend new conversation to the list. The backend already filters
        // by queue, but guard on the client side as well in case of a
        // selected-queue mismatch (e.g. stale cache).
        const conv = parsed.conversation;
        const { conversations, selectedQueueId } = useCsmConversationStore.getState();
        if (selectedQueueId != null && conv.queue !== selectedQueueId) {
          // Conversation belongs to a different queue than the active filter.
          return;
        }
        useCsmConversationStore.getState().setConversations([conv, ...conversations]);
      } else if (parsed.type === 'conversation_updated') {
        updateConversation(parsed.conversation);
      } else if (parsed.type === 'typing_indicator') {
        setTyping(parsed.conversation_id, parsed.user_id, parsed.is_typing);
        // Clear typing indicator after 3 seconds
        if (parsed.is_typing) {
          setTimeout(() => setTyping(parsed.conversation_id, parsed.user_id, false), 3000);
        }
      } else if (parsed.type === 'guidance_updated') {
        bumpGuidance(parsed.experience_group_ids);
      }
    };

    ws.onerror = (e) => {
      console.error('[CSM WS] error', e);
    };

    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [user?.id, token, addMessage, updateConversation, setTyping, bumpGuidance]);

  const sendTyping = useCallback(
    (isTyping: boolean) => {
      if (!activeConvRef.current) return;
      send({ type: isTyping ? 'typing_start' : 'typing_stop', conversation_id: activeConvRef.current });
    },
    [send]
  );

  return { sendTyping };
}
