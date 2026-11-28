'use client';

import { useState, useCallback, useEffect, useRef } from 'react';
import { AlertCircle, Trash2, X } from 'lucide-react';
import { format } from 'date-fns';
import toast from 'react-hot-toast';
import type { NotificationItem } from '@/types/notifications';
import type { Message } from '@/types/chat';
import { useDrawerChat } from '@/hooks/useDrawerChat';
import { addReaction, setMessageReminder, revokeMessage, deleteMessage, hideMessage, forwardBatch } from '@/lib/api/chatApi';
import { useChatStore } from '@/lib/chatStore';
import { useChatData } from '@/hooks/useChatData';
import { useProjectMembers } from '@/hooks/useProjectMembers';
import { useChatWebSocket, type ChatWsEvent } from '@/hooks/useChatWebSocket';
import { useAuthStore } from '@/lib/authStore';
import { notificationsApi } from '@/lib/api/notificationsApi';
import { useNotificationStore } from '@/lib/notificationStore';
import DrawerChatHeader from './DrawerChatHeader';
import DrawerChatMessages, { type DrawerChatMessagesHandle } from './DrawerChatMessages';
import ChatComposer from '@/components/chat/ChatComposer';
import type { RichSendData } from '@/components/chat/ChatComposer';
import ReminderPickerSheet from '@/components/chat/ReminderPickerSheet';
import ForwardMessageSheet from '@/components/chat/ForwardMessageSheet';

interface DrawerChatViewProps {
  notification: NotificationItem;
  onClose: () => void;
}

/**
 * Chat-based drawer view for message notifications.
 * Renders a mini chat interface with recent messages and input.
 */
export default function DrawerChatView({
  notification,
  onClose,
}: DrawerChatViewProps) {
  // Extract chat info from notification metadata
  const chatId = notification.metadata?.chat_id as number | undefined;
  const messageId = notification.metadata?.message_id as number | undefined;
  const projectId = notification.metadata?.project_id as number | undefined;

  // Only highlight the message if this notification is still unread.
  // This ensures highlight/scroll only happens on the first view.
  const shouldHighlight = !notification.is_read;
  const triggerNotificationRefresh = useNotificationStore((state) => state.triggerRefresh);

  const {
    chat,
    messages,
    isLoading,
    isLoadingMessages,
    error,
    sendRich,
    isSending,
    highlightMessageId,
    currentUserId,
    hasMore,
    isLoadingMore,
    loadMoreMessages,
    removeLocalMessage,
  } = useDrawerChat({
    chatId: chatId ?? null,
    highlightMessageId: shouldHighlight ? messageId : null,
    enabled: !!chatId,
  });

  // Selection mode state
  const [isSelectMode, setIsSelectMode] = useState(false);
  const [selectedMessageIds, setSelectedMessageIds] = useState<number[]>([]);

  // Quote reply state
  const [quoteMessage, setQuoteMessage] = useState<Message | null>(null);

  // Reminder state
  const [showReminderPicker, setShowReminderPicker] = useState(false);
  const [reminderMessageId, setReminderMessageId] = useState<number | null>(null);

  // Forward state
  const [showForwardSheet, setShowForwardSheet] = useState(false);
  const [forwardMessageIds, setForwardMessageIds] = useState<number[]>([]);

  // Fetch chats and project members for forward sheet
  const chatsByProject = useChatStore((state) => state.chatsByProject);
  const projectChats = projectId ? (chatsByProject[projectId] || []) : [];
  const { members: projectMembers, isLoading: isLoadingMembers } = useProjectMembers(projectId || null);

  // Typing indicator state
  const [typingUserId, setTypingUserId] = useState<number | null>(null);
  const typingTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const user = useAuthStore((state) => state.user);
  const userId = user?.id ? Number(user.id) : null;

  // Ref for scrolling messages to bottom
  const messagesRef = useRef<DrawerChatMessagesHandle>(null);

  // Ref for the composer wrapper — used to detect height changes (e.g. formatting bar open/close)
  const composerWrapperRef = useRef<HTMLDivElement>(null);

  // When the composer grows (formatting bar opens), scroll messages to bottom
  // so the last message is not hidden behind the expanded composer.
  useEffect(() => {
    const el = composerWrapperRef.current;
    if (!el) return;
    const observer = new ResizeObserver(() => {
      messagesRef.current?.scrollToBottom('smooth');
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // WebSocket connection for real-time events
  const { connected, sendTypingStart, sendTypingStop } = useChatWebSocket(userId, {
    onPresenceUpdate: useCallback((event: ChatWsEvent) => {
      const presenceUserId = Number(event.user_id);
      if (!Number.isFinite(presenceUserId) || typeof event.is_online !== 'boolean') return;
      useChatStore.getState().updateUserPresence(presenceUserId, event.is_online, event.version);
    }, []),
    onPresenceSnapshot: useCallback((event: ChatWsEvent) => {
      if (!Array.isArray(event.users)) return;
      useChatStore.getState().setPresenceSnapshot(event.users);
    }, []),
    onOutboxAck: useCallback((event: ChatWsEvent) => {
      const committed = Array.isArray(event.committed) ? event.committed : [];
      void useChatStore.getState().reconcileOutboxAck(committed);
    }, []),
    onTypingIndicator: useCallback((event: ChatWsEvent) => {
      // Only handle typing events for this chat
      if (event.chat_id !== chatId) return;

      const otherUserId = event.user_id;
      const isTyping = event.is_typing;

      if (isTyping) {
        setTypingUserId(otherUserId ?? null);

        // Clear existing timeout
        if (typingTimeoutRef.current) {
          clearTimeout(typingTimeoutRef.current);
        }

        // Auto-clear after 5 seconds (in case we don't receive typing_stop)
        typingTimeoutRef.current = setTimeout(() => {
          setTypingUserId(null);
        }, 5000);
      } else {
        // User stopped typing
        if (otherUserId === typingUserId) {
          setTypingUserId(null);
          if (typingTimeoutRef.current) {
            clearTimeout(typingTimeoutRef.current);
            typingTimeoutRef.current = null;
          }
        }
      }
    }, [chatId, typingUserId]),
  });

  // Cleanup typing timeout on unmount
  useEffect(() => {
    return () => {
      if (typingTimeoutRef.current) {
        clearTimeout(typingTimeoutRef.current);
      }
    };
  }, []);

  // Typing handlers for MessageInput
  const handleTypingStart = useCallback(() => {
    if (chatId) {
      sendTypingStart(chatId);
    }
  }, [chatId, sendTypingStart]);

  const handleTypingStop = useCallback(() => {
    if (chatId) {
      sendTypingStop(chatId);
    }
  }, [chatId, sendTypingStop]);

  // Selection mode handlers
  const handleEnterSelectMode = (initialMessageId?: number) => {
    setIsSelectMode(true);
    if (initialMessageId) {
      setSelectedMessageIds([initialMessageId]);
    }
  };

  const handleCancelSelectMode = () => {
    setIsSelectMode(false);
    setSelectedMessageIds([]);
  };

  const handleToggleSelect = (messageId: number) => {
    setSelectedMessageIds((prev) =>
      prev.includes(messageId)
        ? prev.filter((id) => id !== messageId)
        : [...prev, messageId]
    );
  };

  // Message action handlers
  const handleEmojiReaction = useCallback(async (messageId: number, emoji: string) => {
    if (!chatId) return;
    try {
      const result = await addReaction(messageId, emoji);
      // Apply the server snapshot; its version stops it overwriting a newer broadcast
      if (result.message) {
        useChatStore.getState().applyReactionSnapshot(
          messageId, result.message.reactions ?? [], result.reactions_version, currentUserId ?? null,
        );
      }
    } catch (err: any) {
      console.error('Failed to add reaction:', err);
      toast.error('Failed to add reaction');
    }
  }, [chatId, currentUserId]);

  // Handle clicking on existing reaction (toggle)
  const handleReactionClick = useCallback(async (
    messageId: number,
    emoji: string,
    isReactedByMe: boolean
  ) => {
    // Same logic as handleEmojiReaction - toggle behavior is handled by backend
    await handleEmojiReaction(messageId, emoji);
  }, [handleEmojiReaction]);

  const handleQuoteReply = (message: Message) => {
    setQuoteMessage(message);
  };

  const handleClearQuote = () => {
    setQuoteMessage(null);
  };

  const handleForward = (messageId: number) => {
    setForwardMessageIds([messageId]);
    setShowForwardSheet(true);
  };

  const handleBulkForward = () => {
    if (selectedMessageIds.length === 0) return;
    setForwardMessageIds(selectedMessageIds);
    setShowForwardSheet(true);
  };

  const handleConfirmForward = async (targetChatIds: number[], targetUserIds: number[]) => {
    if (!chatId) return;

    try {
      await forwardBatch({
        source_chat_id: chatId,
        source_message_ids: forwardMessageIds,
        target_chat_ids: targetChatIds,
        target_user_ids: targetUserIds,
      });

      const messageText = forwardMessageIds.length === 1 ? 'Message' : `${forwardMessageIds.length} messages`;
      const targetText = (targetChatIds.length + targetUserIds.length) === 1
        ? '1 target'
        : `${targetChatIds.length + targetUserIds.length} targets`;

      toast.success(`${messageText} forwarded to ${targetText}`, { icon: '↪️' });

      setShowForwardSheet(false);
      setForwardMessageIds([]);

      // Exit select mode if in bulk forward
      if (isSelectMode) {
        setIsSelectMode(false);
        setSelectedMessageIds([]);
      }
    } catch (error: any) {
      console.error('Failed to forward messages:', error);
      const errorMsg = error?.response?.data?.error || 'Failed to forward messages';
      toast.error(errorMsg);
    }
  };

  const handleRemind = (messageId: number) => {
    setReminderMessageId(messageId);
    setShowReminderPicker(true);
  };

  const handleConfirmReminder = async (time: Date) => {
    if (!reminderMessageId) return;

    try {
      await setMessageReminder(reminderMessageId, time);
      setShowReminderPicker(false);
      toast.success(`Reminder set for ${format(time, 'MMM dd HH:mm')}`, { icon: '🔔' });
    } catch (error) {
      console.error('Failed to set reminder:', error);
      toast.error('Failed to set reminder');
    }
  };

  const handleRemoveNotification = useCallback(async () => {
    try {
      await notificationsApi.clear({ scope: 'ids', ids: [notification.id] });
      triggerNotificationRefresh();
      toast.success('Notification removed');
      onClose();
    } catch (err) {
      console.error('Failed to remove notification:', err);
      toast.error('Could not remove notification');
    }
  }, [notification.id, onClose, triggerNotificationRefresh]);

  const handleRevoke = async (messageId: number) => {
    try {
      const result = await revokeMessage(messageId);
      // Update message in store with revoke status
      if (result.message) {
        const { updateMessage, triggerFilesRefresh } = useChatStore.getState();
        updateMessage(messageId, {
          is_revoked: result.message.is_revoked,
          revoked_at: result.message.revoked_at,
        });
        triggerFilesRefresh();
      }
      toast.success('Message revoked', { icon: '↩️' });
    } catch (error: any) {
      console.error('Failed to revoke message:', error);
      const errorMsg = error?.response?.data?.error || 'Failed to revoke message';
      toast.error(errorMsg);
    }
  };

  const handleDelete = async (messageId: number) => {
    try {
      const message = messages.find(m => m.id === messageId);
      if (!message) return;

      const isOwnMessage = message.sender.id === currentUserId;

      if (isOwnMessage) {
        // Hard delete (sender deleting their own message)
        await deleteMessage(messageId);
        toast.success('Message deleted', { icon: '🗑️' });
      } else {
        // Hide (receiver hiding someone else's message)
        await hideMessage(messageId);
        toast.success('Message deleted', { icon: '🗑️' });
      }

      // Remove from local state (both hook and store)
      removeLocalMessage(messageId);
      if (chatId) {
        const { messages } = useChatStore.getState();
        const chatMessages = messages[chatId] || [];
        const updatedMessages = chatMessages.filter((msg) => msg.id !== messageId);
        useChatStore.getState().setMessages(chatId, updatedMessages);
      }
      useChatStore.getState().triggerFilesRefresh();
    } catch (error: any) {
      console.error('Failed to delete message:', error);
      const errorMsg = error?.response?.data?.error || 'Failed to delete message';
      toast.error(errorMsg);
    }
  };

  // Handle rich send (from ChatComposer)
  const handleSendRich = async (data: RichSendData) => {
    await sendRich(
      data.content,
      data.rich_body,
      data.mention_ids,
      data.attachment_ids,
      data.reply_to_id ?? quoteMessage?.id ?? null,
    );
    if (quoteMessage) setQuoteMessage(null);
    setTimeout(() => { messagesRef.current?.scrollToBottom('smooth'); }, 100);
  };

  // Error state - no chat ID in notification
  if (!chatId) {
    return (
      <div className="flex flex-col h-full">
        <DrawerChatHeader
          chat={null}
          onClose={onClose}
          isLoading={false}
          fallbackName="Conversation unavailable"
          fallbackSubtitle="Missing chat information"
        />
        <div className="flex-1 flex flex-col items-center justify-center p-8 text-gray-500">
          <AlertCircle className="w-12 h-12 text-gray-300 mb-3" />
          <p className="text-sm font-semibold text-gray-800">Conversation unavailable</p>
          <p className="mt-1 max-w-[260px] text-center text-xs text-gray-400">
            Chat information is missing from this notification.
          </p>
          <button
            type="button"
            onClick={handleRemoveNotification}
            className="mt-4 inline-flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-2 text-xs font-medium text-gray-600 transition-colors hover:bg-gray-50"
          >
            <Trash2 className="h-3.5 w-3.5" />
            Remove notification
          </button>
        </div>
      </div>
    );
  }

  // Error state - API error
  if (error && !isLoading) {
    return (
      <div className="flex flex-col h-full">
        <DrawerChatHeader
          chat={null}
          projectId={projectId}
          onClose={onClose}
          isLoading={false}
          fallbackName="Conversation unavailable"
          fallbackSubtitle="This chat may have been deleted"
        />
        <div className="flex-1 flex flex-col items-center justify-center p-8 text-gray-500">
          <AlertCircle className="w-12 h-12 text-red-300 mb-3" />
          <p className="text-sm font-semibold text-gray-800">Conversation unavailable</p>
          <p className="mt-1 max-w-[260px] text-center text-xs text-gray-400">
            This notification points to a chat you can no longer open.
          </p>
          <button
            type="button"
            onClick={handleRemoveNotification}
            className="mt-4 inline-flex items-center gap-1.5 rounded-lg border border-gray-200 px-3 py-2 text-xs font-medium text-gray-600 transition-colors hover:bg-gray-50"
          >
            <Trash2 className="h-3.5 w-3.5" />
            Remove notification
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="relative flex flex-col h-full">
      {/* Header */}
      <DrawerChatHeader
        chat={chat}
        projectId={projectId}
        onClose={onClose}
        isLoading={isLoading}
        isSelectMode={isSelectMode}
        selectedCount={selectedMessageIds.length}
        onCancelSelect={handleCancelSelectMode}
        onBulkForward={handleBulkForward}
        typingUserId={typingUserId}
      />

      {/* Messages area */}
      <DrawerChatMessages
        ref={messagesRef}
        messages={messages}
        currentUserId={currentUserId}
        highlightMessageId={highlightMessageId}
        isLoading={isLoadingMessages}
        hasMore={hasMore}
        isLoadingMore={isLoadingMore}
        onLoadMore={loadMoreMessages}
        isSelectMode={isSelectMode}
        selectedMessageIds={selectedMessageIds}
        onToggleSelect={handleToggleSelect}
        onEmojiReaction={handleEmojiReaction}
        onQuoteReply={handleQuoteReply}
        onForward={handleForward}
        onRemind={handleRemind}
        onRevoke={handleRevoke}
        onDelete={handleDelete}
        onEnterSelectMode={handleEnterSelectMode}
        onReactionClick={handleReactionClick}
      />

      {/* Quote reply preview */}
      {quoteMessage && (
        <div className="flex-shrink-0 flex items-start gap-2 px-3 py-2 bg-gray-50 border-t border-gray-200">
          <div className="flex-shrink-0 w-1 self-stretch rounded bg-gradient-to-b from-[#3CCED7] to-[#A6E661]" />
          <div className="flex-1 min-w-0">
            <p className="text-xs font-medium text-gray-600 truncate">
              {quoteMessage.sender.username}
            </p>
            <p className="text-sm text-gray-500 truncate">
              {quoteMessage.content || '[Attachment]'}
            </p>
          </div>
          <button
            onClick={handleClearQuote}
            className="flex-shrink-0 p-1 hover:bg-gray-200 rounded transition-colors"
          >
            <X className="w-4 h-4 text-gray-400" />
          </button>
        </div>
      )}

      {/* Input area */}
      <div ref={composerWrapperRef} className="flex-shrink-0 border-t border-[#3CCED7]/25 bg-white">
        <ChatComposer
          variant="drawer"
          onSendRich={handleSendRich}
          disabled={isSending || isLoading || isSelectMode}
          chatId={chatId}
          projectId={chat?.project_id ?? null}
          onTypingStart={handleTypingStart}
          onTypingStop={handleTypingStop}
          participants={chat?.participants ?? []}
        />
      </div>

      {/* Reminder Picker Sheet */}
      <ReminderPickerSheet
        open={showReminderPicker}
        onOpenChange={setShowReminderPicker}
        onConfirm={handleConfirmReminder}
      />

      {/* Forward Message Sheet */}
      <ForwardMessageSheet
        open={showForwardSheet}
        onOpenChange={setShowForwardSheet}
        onConfirm={handleConfirmForward}
        messageCount={forwardMessageIds.length}
        selectedProjectId={projectId || null}
        chats={projectChats}
        projectMembers={projectMembers}
        isLoading={isLoadingMembers}
      />
    </div>
  );
}
