'use client';

import { useEffect, useState, useCallback, useMemo } from 'react';
import { useChatStore, getChatSlugById, reorderMessagesBySequence } from '@/lib/chatStore';
import { useAuthStore } from '@/lib/authStore';
import { getChat, getMessages, sendMessage, markChatAsRead } from '@/lib/api/chatApi';
import type { Chat, Message, SendMessageRequest } from '@/types/chat';
import type { TiptapJSONContent } from '@/types/comment';
import toast from 'react-hot-toast';

async function sendDrawerMessageWithOutbox(request: SendMessageRequest): Promise<Message> {
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

const DEFAULT_MESSAGE_LIMIT = 20;
const EMPTY_MESSAGES: Message[] = [];

interface UseDrawerChatOptions {
  chatId: number | null;
  highlightMessageId?: number | null;
  enabled?: boolean;
}

interface UseDrawerChatReturn {
  // Data
  chat: Chat | null;
  messages: Message[];
  isLoading: boolean;
  isLoadingMessages: boolean;
  error: string | null;

  // Sending
  sendMessage: (content: string, replyToId?: number | null) => Promise<Message | null>;
  sendWithAttachments: (content: string, attachmentIds: number[], replyToId?: number | null) => Promise<Message | null>;
  sendRich: (content: string, richBody: TiptapJSONContent, mentionIds: number[], attachmentIds?: number[], replyToId?: number | null) => Promise<Message | null>;
  isSending: boolean;

  // State
  highlightMessageId: number | null;
  currentUserId: number;

  // Pagination
  hasMore: boolean;
  isLoadingMore: boolean;
  loadMoreMessages: () => Promise<void>;

  // Message management
  removeLocalMessage: (messageId: number) => void;
}

/**
 * Independent hook for drawer chat functionality.
 * Does not interfere with the main chatStore currentChatId.
 * Listens to chatStore.messages for real-time updates from WebSocket.
 */
export function useDrawerChat({
  chatId,
  highlightMessageId: initialHighlightId,
  enabled = true,
}: UseDrawerChatOptions): UseDrawerChatReturn {
  const user = useAuthStore((state) => state.user);
  const currentUserId = user?.id ? Number(user.id) : 0;

  // Local state (independent from main chatStore)
  const [chat, setChat] = useState<Chat | null>(null);
  const [localMessages, setLocalMessages] = useState<Message[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingMessages, setIsLoadingMessages] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [highlightMessageId, setHighlightMessageId] = useState<number | null>(
    initialHighlightId ?? null
  );

  // Pagination state
  const [hasMore, setHasMore] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);

  // Listen to chatStore messages for real-time updates
  // Use a stable empty array reference to prevent infinite re-renders
  const storeMessages = useChatStore((state) =>
    chatId ? state.messages[chatId] ?? EMPTY_MESSAGES : EMPTY_MESSAGES
  );

  // Merge local messages with store messages for real-time updates
  // Store messages may contain new messages from WebSocket
  const messages = useMemo(() => {
    if (!chatId) return [];

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
  }, [chatId, localMessages, storeMessages]);

  // Fetch chat details
  const fetchChat = useCallback(async () => {
    if (!chatId) return;

    try {
      setIsLoading(true);
      setError(null);
      const chatData = await getChat(chatId);
      setChat(chatData);
    } catch (err: any) {
      const errorMsg = err?.response?.data?.detail || 'Failed to load chat';
      setError(errorMsg);
      console.error('Error fetching chat:', err);
    } finally {
      setIsLoading(false);
    }
  }, [chatId]);

  // Fetch messages
  const fetchMessages = useCallback(async () => {
    if (!chatId) return;

    try {
      setIsLoadingMessages(true);
      setError(null);

      const response = await getMessages({
        chat_id: chatId,
        limit: DEFAULT_MESSAGE_LIMIT,
      });

      setLocalMessages(response.results);
      // Check if there are more messages to load
      setHasMore(response.results.length === DEFAULT_MESSAGE_LIMIT);

      // Also update the store so WebSocket can track this chat
      const { setMessages } = useChatStore.getState();
      setMessages(chatId, response.results);
    } catch (err: any) {
      const errorMsg = err?.response?.data?.detail || 'Failed to load messages';
      setError(errorMsg);
      console.error('Error fetching messages:', err);
    } finally {
      setIsLoadingMessages(false);
    }
  }, [chatId]);

  // Mark chat as read
  const markAsRead = useCallback(async () => {
    if (!chatId) return;
    // Chat detail routes are slug-only; resolve the numeric id to its slug.
    const chatSlug = getChatSlugById(chatId);
    if (!chatSlug) return;

    try {
      await markChatAsRead(chatSlug);
    } catch (err: any) {
      console.error('Error marking chat as read:', err);
      // Don't show toast for read errors (not critical)
    }
  }, [chatId]);

  // Load more (older) messages
  const loadMoreMessages = useCallback(async () => {
    if (!chatId || isLoadingMore || !hasMore || localMessages.length === 0) return;

    try {
      setIsLoadingMore(true);
      const oldestMessage = localMessages[0];

      const response = await getMessages({
        chat_id: chatId,
        before: oldestMessage.created_at,
        limit: DEFAULT_MESSAGE_LIMIT,
      });

      if (response.results.length > 0) {
        // Prepend older messages to the list
        setLocalMessages((prev) => [...response.results, ...prev]);
      }
      // Check if there are more messages to load
      setHasMore(response.results.length === DEFAULT_MESSAGE_LIMIT);
    } catch (err: any) {
      console.error('Error loading more messages:', err);
      // Don't show toast for load more errors
    } finally {
      setIsLoadingMore(false);
    }
  }, [chatId, isLoadingMore, hasMore, localMessages]);

  // Remove a message from local state (for delete/hide operations)
  const removeLocalMessage = useCallback((messageId: number) => {
    setLocalMessages((prev) => prev.filter((msg) => msg.id !== messageId));
  }, []);

  // Send message
  const handleSendMessage = useCallback(
    async (content: string, replyToId?: number | null): Promise<Message | null> => {
      if (!chatId || !content.trim()) return null;

      try {
        setIsSending(true);
        setError(null);

        const data: SendMessageRequest = {
          chat_id: chatId,
          content: content.trim(),
          reply_to_id: replyToId ?? undefined,
        };

        const newMessage = await sendDrawerMessageWithOutbox(data);

        // Add to local messages
        setLocalMessages((prev) => {
          // Check if already exists
          if (prev.some((m) => m.id === newMessage.id)) return prev;
          return [...prev, newMessage];
        });

        // Also add to store for consistency
        const { addMessage } = useChatStore.getState();
        addMessage(chatId, newMessage, currentUserId);

        return newMessage;
      } catch (err: any) {
        const errorMsg = err?.response?.data?.detail || 'Failed to send message';
        setError(errorMsg);
        console.error('Error sending message:', err);
        toast.error(errorMsg);
        return null;
      } finally {
        setIsSending(false);
      }
    },
    [chatId, currentUserId]
  );

  // Send message with attachments
  const handleSendWithAttachments = useCallback(
    async (content: string, attachmentIds: number[], replyToId?: number | null): Promise<Message | null> => {
      if (!chatId) return null;
      if (!content.trim() && attachmentIds.length === 0) return null;

      try {
        setIsSending(true);
        setError(null);

        const data: SendMessageRequest = {
          chat_id: chatId,
          content: content.trim() || '',
          attachment_ids: attachmentIds,
          reply_to_id: replyToId ?? undefined,
        };

        const newMessage = await sendDrawerMessageWithOutbox(data);

        // Add to local messages
        setLocalMessages((prev) => {
          if (prev.some((m) => m.id === newMessage.id)) return prev;
          return [...prev, newMessage];
        });

        // Also add to store for consistency
        const { addMessage } = useChatStore.getState();
        addMessage(chatId, newMessage, currentUserId);

        return newMessage;
      } catch (err: any) {
        const errorMsg = err?.response?.data?.detail || 'Failed to send message';
        setError(errorMsg);
        console.error('Error sending message with attachments:', err);
        toast.error(errorMsg);
        return null;
      } finally {
        setIsSending(false);
      }
    },
    [chatId, currentUserId]
  );

  // Send rich message (Tiptap JSON + mentions)
  const handleSendRich = useCallback(
    async (
      content: string,
      richBody: TiptapJSONContent,
      mentionIds: number[],
      attachmentIds?: number[],
      replyToId?: number | null,
    ): Promise<Message | null> => {
      if (!chatId) return null;
      if (!content.trim() && (!attachmentIds || attachmentIds.length === 0)) return null;

      try {
        setIsSending(true);
        setError(null);

        const data: SendMessageRequest = {
          chat_id: chatId,
          content: content.trim() || '',
          rich_body: richBody,
          mention_ids: mentionIds,
          ...(attachmentIds && attachmentIds.length > 0 ? { attachment_ids: attachmentIds } : {}),
          ...(replyToId ? { reply_to_id: replyToId } : {}),
        };

        const newMessage = await sendDrawerMessageWithOutbox(data);

        setLocalMessages((prev) => {
          if (prev.some((m) => m.id === newMessage.id)) return prev;
          return [...prev, newMessage];
        });

        const { addMessage } = useChatStore.getState();
        addMessage(chatId, newMessage, currentUserId);

        return newMessage;
      } catch (err: any) {
        const errorMsg = err?.response?.data?.detail || 'Failed to send message';
        setError(errorMsg);
        console.error('Error sending rich message:', err);
        toast.error(errorMsg);
        return null;
      } finally {
        setIsSending(false);
      }
    },
    [chatId, currentUserId],
  );

  // Fetch data when chatId changes
  useEffect(() => {
    if (!enabled || !chatId) {
      setChat(null);
      setLocalMessages([]);
      setError(null);
      return;
    }

    // Reset highlight when chatId changes
    setHighlightMessageId(initialHighlightId ?? null);

    // Fetch chat and messages
    fetchChat();
    fetchMessages();

    // Mark as read after a short delay
    const readTimer = setTimeout(() => {
      markAsRead();
    }, 500);

    return () => {
      clearTimeout(readTimer);
    };
  }, [chatId, enabled, initialHighlightId, fetchChat, fetchMessages, markAsRead]);

  // Clear highlight after a few seconds
  useEffect(() => {
    if (!highlightMessageId) return;

    const timer = setTimeout(() => {
      setHighlightMessageId(null);
    }, 4000);

    return () => clearTimeout(timer);
  }, [highlightMessageId]);

  return {
    chat,
    messages,
    isLoading,
    isLoadingMessages,
    error,
    sendMessage: handleSendMessage,
    sendWithAttachments: handleSendWithAttachments,
    sendRich: handleSendRich,
    isSending,
    highlightMessageId,
    currentUserId,
    hasMore,
    isLoadingMore,
    loadMoreMessages,
    removeLocalMessage,
  };
}
