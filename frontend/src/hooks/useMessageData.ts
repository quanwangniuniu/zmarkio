'use client';

import { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import { useChatStore, getChatSlugById, reorderMessagesBySequence } from '@/lib/chatStore';
import { useAuthStore } from '@/lib/authStore';
import { getMessages, sendMessage, markMessageAsRead, markChatAsRead } from '@/lib/api/chatApi';
import type { SendMessageRequest, Message } from '@/types/chat';
import type { TiptapJSONContent } from '@/types/comment';
import toast from 'react-hot-toast';

async function sendMessageWithOutbox(request: SendMessageRequest): Promise<Message> {
  const clientMessageId = crypto.randomUUID();
  const store = useChatStore.getState();
  store.enqueueOutbox({
    clientMessageId,
    chatId: request.chat_id,
    content: request.content,
    richBody: request.rich_body ?? null,
    attachmentIds: request.attachment_ids ?? [],
    mentionIds: request.mention_ids,
    replyToId: request.reply_to_id,
    parentMessageId: request.parent_message_id,
    status: 'pending',
    enqueuedAt: new Date().toISOString(),
  });
  store.markOutboxSending(clientMessageId);
  try {
    const message = await sendMessage({ ...request, client_message_id: clientMessageId });
    store.markOutboxSent(clientMessageId, message);
    return message;
  } catch (error) {
    store.markOutboxFailed(clientMessageId);
    throw error;
  }
}


// Empty array constant to avoid creating new references
const EMPTY_MESSAGES: Message[] = [];

interface UseMessageDataOptions {
  chatId?: number | null;
  autoFetch?: boolean;
  limit?: number;
}

export function useMessageData(options: UseMessageDataOptions = {}) {
  const { chatId, autoFetch = true, limit = 50 } = options;

  const activeChatIdRef = useRef<number | null | undefined>(chatId);
  activeChatIdRef.current = chatId;
  // Local state for messages (independent from store), scoped to the chat that loaded it.
  const [localMessageState, setLocalMessageState] = useState<{
    chatId: number | null;
    messages: Message[];
  }>({ chatId: null, messages: [] });
  const localMessages = useMemo(() => {
    if (!chatId || localMessageState.chatId !== chatId) return EMPTY_MESSAGES;
    return localMessageState.messages;
  }, [chatId, localMessageState]);

  // Get messages from store for real-time updates (WebSocket)
  const allMessages = useChatStore(state => state.messages);
  const storeMessages = useMemo(() => {
    if (!chatId) return EMPTY_MESSAGES;
    return allMessages[chatId] || EMPTY_MESSAGES;
  }, [chatId, allMessages]);

  // Merge local messages with store messages for display
  // Use Map for deduplication, store messages override local (more up-to-date from WebSocket)
  const currentMessages = useMemo(() => {
    if (!chatId) return EMPTY_MESSAGES;

    // Create a map of message IDs for deduplication
    const messageMap = new Map<number, Message>();

    // Add local messages first
    localMessages.forEach((msg) => {
      messageMap.set(msg.id, msg);
    });

    // Override/add with store messages (more up-to-date from WebSocket)
    storeMessages.forEach((msg) => {
      messageMap.set(msg.id, msg);
    });

    return reorderMessagesBySequence(Array.from(messageMap.values()));
  }, [chatId, storeMessages, localMessages]);

  const [isFetchingMessages, setIsFetchingMessages] = useState(false);
  const [isLoadingMoreMessages, setIsLoadingMoreMessages] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [hasMore, setHasMore] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const isLoadingMessages = isFetchingMessages || isLoadingMoreMessages;
  // True after the first successful fetch for the current chatId.
  // Guards jump-to-message from declaring "not found" before the initial load completes.
  const [hasFetchedInitially, setHasFetchedInitially] = useState(false);

  // Fetch messages for current chat
  const fetchMessages = useCallback(async (chatIdToFetch?: number) => {
    const targetChatId = chatIdToFetch || chatId;
    if (!targetChatId) return;

    const { setMessages } = useChatStore.getState();

    try {
      setIsFetchingMessages(true);
      setError(null);

      const response = await getMessages({
        chat_id: targetChatId,
        limit,
      });

      if (activeChatIdRef.current !== targetChatId) return;

      // Update both local state AND store
      setLocalMessageState({ chatId: targetChatId, messages: response.results });
      setMessages(targetChatId, response.results);

      // prev_cursor indicates there are older messages available
      setHasMore(!!response.prev_cursor || response.results.length === limit);

      // Mark the initial fetch as complete so jump-to-message logic can proceed
      setHasFetchedInitially(true);
    } catch (err: any) {
      if (activeChatIdRef.current !== targetChatId) return;
      const errorMsg = err?.response?.data?.detail || 'Failed to load messages';
      setError(errorMsg);
      console.error('Error fetching messages:', err);
      toast.error(errorMsg);
    } finally {
      if (activeChatIdRef.current === targetChatId) {
        setIsFetchingMessages(false);
      }
    }
  }, [chatId, limit]);

  // Load more (older) messages
  const loadMoreMessages = useCallback(async () => {
    const targetChatId = chatId;
    if (!targetChatId || !hasMore || isLoadingMessages) return;

    const { prependMessages } = useChatStore.getState();

    try {
      setIsLoadingMoreMessages(true);
      setError(null);

      // Get the oldest message's timestamp for cursor-based pagination
      const oldestMessage = currentMessages.length > 0 ? currentMessages[0] : null;
      const beforeTimestamp = oldestMessage?.created_at;

      const response = await getMessages({
        chat_id: targetChatId,
        before: beforeTimestamp, // Use timestamp instead of ID
        limit,
      });

      if (activeChatIdRef.current !== targetChatId) return;

      if (response.results.length > 0) {
        // Update both local state AND store
        setLocalMessageState((prev) => ({
          chatId: targetChatId,
          messages: [
            ...response.results,
            ...(prev.chatId === targetChatId ? prev.messages : []),
          ],
        }));
        prependMessages(targetChatId, response.results);
      }
      // Check if there are more messages (prev_cursor indicates more older messages)
      setHasMore(!!response.prev_cursor || response.results.length === limit);
    } catch (err: any) {
      if (activeChatIdRef.current !== targetChatId) return;
      const errorMsg = err?.response?.data?.detail || 'Failed to load more messages';
      setError(errorMsg);
      console.error('Error loading more messages:', err);
    } finally {
      if (activeChatIdRef.current === targetChatId) {
        setIsLoadingMoreMessages(false);
      }
    }
  }, [chatId, hasMore, isLoadingMessages, currentMessages, limit]);

  // Send new message
  const send = useCallback(async (content: string, replyToId?: number | null): Promise<Message | null> => {
    const targetChatId = chatId;
    if (!targetChatId || !content.trim()) return null;

    const { addMessage } = useChatStore.getState();

    try {
      setIsSending(true);
      setError(null);

      const data: SendMessageRequest = {
        chat_id: targetChatId,
        content: content.trim(),
        ...(replyToId ? { reply_to_id: replyToId } : {}),
      };

      const newMessage = await sendMessageWithOutbox(data);

      // Add to both local state AND store
      if (activeChatIdRef.current === targetChatId) {
        setLocalMessageState((prev) => {
          const messages = prev.chatId === targetChatId ? prev.messages : [];
          if (messages.some((m) => m.id === newMessage.id)) return prev;
          return { chatId: targetChatId, messages: [...messages, newMessage] };
        });
      }
      addMessage(targetChatId, newMessage);

      return newMessage;
    } catch (err: any) {
      if (activeChatIdRef.current !== targetChatId) return null;
      const errorMsg = err?.response?.data?.detail || 'Failed to send message';
      setError(errorMsg);
      console.error('Error sending message:', err);
      toast.error(errorMsg);
      return null;
    } finally {
      if (activeChatIdRef.current === targetChatId) {
        setIsSending(false);
      }
    }
  }, [chatId]);

  // Send message with attachments
  const sendWithAttachments = useCallback(async (
    content: string,
    attachmentIds: number[],
    replyToId?: number | null,
  ): Promise<Message | null> => {
    const targetChatId = chatId;
    if (!targetChatId) return null;
    // Must have content OR attachments
    if (!content.trim() && attachmentIds.length === 0) return null;

    const { addMessage } = useChatStore.getState();

    try {
      setIsSending(true);
      setError(null);

      const data: SendMessageRequest = {
        chat_id: targetChatId,
        content: content.trim() || '',
        attachment_ids: attachmentIds,
        ...(replyToId ? { reply_to_id: replyToId } : {}),
      };

      const newMessage = await sendMessageWithOutbox(data);

      // Add to both local state AND store
      if (activeChatIdRef.current === targetChatId) {
        setLocalMessageState((prev) => {
          const messages = prev.chatId === targetChatId ? prev.messages : [];
          if (messages.some((m) => m.id === newMessage.id)) return prev;
          return { chatId: targetChatId, messages: [...messages, newMessage] };
        });
      }
      addMessage(targetChatId, newMessage);

      return newMessage;
    } catch (err: any) {
      if (activeChatIdRef.current !== targetChatId) return null;
      const errorMsg = err?.response?.data?.detail || 'Failed to send message';
      setError(errorMsg);
      console.error('Error sending message with attachments:', err);
      toast.error(errorMsg);
      return null;
    } finally {
      if (activeChatIdRef.current === targetChatId) {
        setIsSending(false);
      }
    }
  }, [chatId]);

  // Send rich message (with Tiptap JSON body + mention IDs)
  const sendRich = useCallback(async (
    content: string,
    richBody: TiptapJSONContent,
    mentionIds: number[],
    attachmentIds?: number[],
    replyToId?: number | null,
  ): Promise<Message | null> => {
    const targetChatId = chatId;
    if (!targetChatId) return null;
    if (!content.trim() && (!attachmentIds || attachmentIds.length === 0)) return null;

    const { addMessage } = useChatStore.getState();

    try {
      setIsSending(true);
      setError(null);

      const data: SendMessageRequest = {
        chat_id: targetChatId,
        content: content.trim() || '',
        rich_body: richBody,
        mention_ids: mentionIds,
        ...(attachmentIds && attachmentIds.length > 0 ? { attachment_ids: attachmentIds } : {}),
        ...(replyToId ? { reply_to_id: replyToId } : {}),
      };

      const newMessage = await sendMessageWithOutbox(data);

      if (activeChatIdRef.current === targetChatId) {
        setLocalMessageState((prev) => {
          const messages = prev.chatId === targetChatId ? prev.messages : [];
          if (messages.some((m) => m.id === newMessage.id)) return prev;
          return { chatId: targetChatId, messages: [...messages, newMessage] };
        });
      }
      addMessage(targetChatId, newMessage);

      return newMessage;
    } catch (err: any) {
      if (activeChatIdRef.current !== targetChatId) return null;
      const errorMsg = err?.response?.data?.detail || 'Failed to send message';
      setError(errorMsg);
      console.error('Error sending rich message:', err);
      toast.error(errorMsg);
      return null;
    } finally {
      if (activeChatIdRef.current === targetChatId) {
        setIsSending(false);
      }
    }
  }, [chatId]);

  // Mark message as read
  const markAsRead = useCallback(async (messageId: number) => {
    const { updateMessage } = useChatStore.getState();
    
    try {
      const updatedMessage = await markMessageAsRead(messageId);
      updateMessage(messageId, updatedMessage);
    } catch (err: any) {
      console.error('Error marking message as read:', err);
      // Don't show toast for read errors (not critical)
    }
  }, []);

  // Remove a message from both local state and the store (for delete)
  const removeMessage = useCallback((messageId: number) => {
    setLocalMessageState((prev) => ({
      ...prev,
      messages: prev.messages.filter((m) => m.id !== messageId),
    }));
    useChatStore.getState().removeMessage(messageId);
  }, []);

  // Mark all messages in chat as read (uses efficient backend endpoint)
  const markAllAsRead = useCallback(async () => {
    if (!chatId) return;
    // Chat detail routes are slug-only; resolve the numeric id to its slug.
    const chatSlug = getChatSlugById(chatId);
    if (!chatSlug) return;

    try {
      await markChatAsRead(chatSlug);
    } catch (err: any) {
      console.error('[useMessageData] Error marking chat as read:', chatId, err);
      // Don't show toast for read errors (not critical)
    }
  }, [chatId]);

  // Auto-fetch messages when chat changes — only when authenticated
  const isAuthenticated = useAuthStore(state => state.isAuthenticated);

  // Clear local messages when chatId changes
  useEffect(() => {
    setLocalMessageState({ chatId: chatId ?? null, messages: [] });
    setIsFetchingMessages(false);
    setIsLoadingMoreMessages(false);
    setHasMore(true); // Reset pagination
    setHasFetchedInitially(false); // Reset until the first fetch for the new chat completes
  }, [chatId]);

  // Auto-fetch messages when chat changes
  useEffect(() => {
    if (autoFetch && chatId && isAuthenticated) {
      fetchMessages();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoFetch, chatId, isAuthenticated]); // Don't include fetchMessages to avoid infinite loop

  return {
    messages: currentMessages,
    isLoadingMessages,
    isLoadingMoreMessages,
    isSending,
    hasMore,
    hasFetchedInitially,
    error,
    fetchMessages,
    loadMoreMessages,
    send,
    sendWithAttachments,
    sendRich,
    removeMessage,
    markAsRead,
    markAllAsRead,
  };
}
