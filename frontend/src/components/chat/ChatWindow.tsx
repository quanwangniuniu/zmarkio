'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, CheckSquare, Forward, Info, Pin, X } from 'lucide-react';
import { useRouter, useSearchParams } from 'next/navigation';
import toast from 'react-hot-toast';
import { useAuthStore } from '@/lib/authStore';
import { useMessageData } from '@/hooks/useMessageData';
import { useForwardMessages } from '@/hooks/useForwardMessages';
import { useChatWebSocket, type ChatWsEvent } from '@/hooks/useChatWebSocket';
import { useChatStore } from '@/lib/chatStore';
import { editMessage, deleteMessage, addReaction, removeReaction, getMessage, getChat, pinMessage, unpinMessage, saveMessage, unsaveMessage, listPins, listSavedMessages, createScheduledMessage, listScheduledMessages, updateChatDetails, updateNotificationSettings } from '@/lib/api/chatApi';
import { isChannelManager } from '@/lib/chatPermissions';
import { buildMessagesPath, isMessageDeepLinkForChat } from '@/lib/messages/messagesRoutes';
import { useBuildUrl } from '@/lib/buildUrl';
import { limitName, MAX_CHANNEL_NAME_LENGTH } from '@/lib/messages/nameLimits';
import type { Chat, Message } from '@/types/chat';
import type { PinnedMessageRow, ScheduledMessageRow } from '@/lib/api/chatApi';
import MessageList from './MessageList';
import ChatComposer from './ChatComposer';
import type { RichSendData, SlashCommand } from './ChatComposer';
import ForwardMessagesDialog from './ForwardMessagesDialog';
import TypingIndicator from './TypingIndicator';
import ThreadPanel from './ThreadPanel';
import ChannelDetailsDrawer from './ChannelDetailsDrawer';
import ReminderPickerSheet from './ReminderPickerSheet';
import PinnedMessageBanner from './PinnedMessageBanner';
import PinnedMessagesDrawer from './PinnedMessagesDrawer';
import { useAnimatedPanel } from '@/hooks/useAnimatedPanel';

interface ChatWindowProps {
  chat: Chat;
  onBack: () => void;
  roleByUserId?: Record<number, string>;
  /**
   * Chat slug currently represented by the full-page URL. During a sidebar
   * switch the store can update one render before Next updates the pathname;
   * deep-link work must pause in that gap instead of resolving the old
   * messageId against the newly selected chat. Omitted by the chat widget.
   */
  routeChatSlug?: string | null;
  /**
   * When true, the back button hides on the md+ viewport. Use this for the
   * full-page Messages view where the sidebar is always visible on desktop.
   * Default false — preserves the floating-widget back behavior.
   */
  hideBackOnDesktop?: boolean;
  /** Set to { chatId, seq } to imperatively open the channel details panel for that chat. */
  openDetailsSignal?: { chatId: number; seq: number };
}

export default function ChatWindow({ chat, onBack, roleByUserId, routeChatSlug, hideBackOnDesktop, openDetailsSignal }: ChatWindowProps) {
  const router = useRouter();
  const buildUrl = useBuildUrl();
  const searchParams = useSearchParams();
  // Use selector for stable reference
  const user = useAuthStore(state => state.user);
  const currentUserId = user?.id ? Number(user.id) : null;
  const canManageChannel = isChannelManager(chat, currentUserId);
  const chatsByProject = useChatStore(state => state.chatsByProject);
  // Snapshot taken at click-time in setCurrentChat — immune to subsequent setChatsForProject resets
  const capturedUnreadCount = useChatStore(state => state.capturedUnreadCounts[chat.id] ?? 0);
  const [isSelectMode, setIsSelectMode] = useState(false);
  const [selectedMessageIds, setSelectedMessageIds] = useState<number[]>([]);
  const [isForwardDialogOpen, setIsForwardDialogOpen] = useState(false);
  const [showSwitchLoadingSkeleton, setShowSwitchLoadingSkeleton] = useState(false);
  const [firstUnreadMessageId, setFirstUnreadMessageId] = useState<number | null>(null);
  const [replyingTo, setReplyingTo] = useState<Message | null>(null);
  const [activeThreadMessage, setActiveThreadMessage] = useState<Message | null>(null);
  // When a user jumps to a thread reply (e.g. from a pinned/saved entry), this
  // is the reply id we want ThreadPanel to highlight + scroll to. Cleared by
  // ThreadPanel itself after the highlight fades.
  const [threadHighlightMessageId, setThreadHighlightMessageId] = useState<number | null>(null);
  const { panelState: pinsPanelState, openPanel: openPinsPanel, closePanel: closePinsPanel, resetPanel: resetPinsPanel } = useAnimatedPanel();
  const { panelState: detailsPanelState, openPanel: openDetailsPanel, closePanel: closeDetailsPanel } = useAnimatedPanel();
  const { panelState: threadPanelState, openPanel: openThreadPanel, closePanel: closeThreadPanel } = useAnimatedPanel();
  const showPinnedMessages = pinsPanelState !== 'closed';
  const showChannelDetails = detailsPanelState !== 'closed';
  const dismissThreadPanel = useCallback(() => {
    setActiveThreadMessage(null);
    setThreadHighlightMessageId(null);
  }, []);
  // Single owner of the details panel: open when signal targets this chat, close otherwise.
  // Watching both deps means it fires correctly whether chat navigation and signal
  // land in the same render or in separate ones.
  useEffect(() => {
    if (openDetailsSignal && openDetailsSignal.chatId === chat.id) {
      closePinsPanel();
      dismissThreadPanel();
      openDetailsPanel();
    } else if (!openDetailsSignal || openDetailsSignal.chatId !== chat.id) {
      closeDetailsPanel();
    }
  }, [chat.id, openDetailsSignal, closePinsPanel, dismissThreadPanel, openDetailsPanel, closeDetailsPanel]);

  // Thread panel: keep the message mounted through the close transition.
  const [threadPanelMessage, setThreadPanelMessage] = useState<Message | null>(null);
  useEffect(() => {
    if (activeThreadMessage) {
      setThreadPanelMessage(activeThreadMessage);
      openThreadPanel();
    } else {
      closeThreadPanel();
    }
  }, [activeThreadMessage, openThreadPanel, closeThreadPanel]);
  useEffect(() => {
    if (threadPanelState === 'closed') setThreadPanelMessage(null);
  }, [threadPanelState]);

  const [reminderMessageId, setReminderMessageId] = useState<number | null>(null);
  const [pins, setPins] = useState<PinnedMessageRow[]>([]);
  const [pinnedMessageIds, setPinnedMessageIds] = useState<Set<number>>(new Set());
  const [hasUnseenPin, setHasUnseenPin] = useState(false);
  const [pinRefreshKey, setPinRefreshKey] = useState(0);
  const pinRequestsInFlightRef = useRef<Set<number>>(new Set());
  const [savedMessageIds, setSavedMessageIds] = useState<Set<number>>(new Set());
  const [pendingScheduledCount, setPendingScheduledCount] = useState(0);
  const [lastScheduledMsg, setLastScheduledMsg] = useState<ScheduledMessageRow | null>(null);
  // First-time channel description banner: shown once per (user, channel) and
  // dismissed into localStorage so existing members never see it again.
  const [showDescriptionBanner, setShowDescriptionBanner] = useState(false);
  const unreadCapturedForChatRef = useRef<number | null>(null);

  // Reminder polling — check localStorage every 60 s and surface due reminders as toasts
  useEffect(() => {
    const check = () => {
      const key = 'ms_reminders';
      try {
        const all = JSON.parse(localStorage.getItem(key) || '[]') as Array<{ messageId: number; time: number; chatId: number }>;
        const now = Date.now();
        const due = all.filter((r) => r.time <= now);
        const remaining = all.filter((r) => r.time > now);
        if (due.length > 0) {
          localStorage.setItem(key, JSON.stringify(remaining));
          due.forEach((r) => {
            toast(`⏰ Reminder: message #${r.messageId}`, { duration: 8000 });
          });
        }
      } catch { /* ignore */ }
    };
    check(); // run immediately on mount
    const interval = setInterval(check, 60_000);
    return () => clearInterval(interval);
  }, []);

  // Refresh pending scheduled message count for badge display
  const refreshScheduledCount = useCallback(() => {
    listScheduledMessages(chat.id)
      .then((rows) => setPendingScheduledCount(rows.filter((r) => r.status === 'pending' || r.status === 'sending').length))
      .catch(() => {});
  }, [chat.id]);

  const pinSeenStorageKey = useMemo(
    () => `ms_chat_pins_seen:${currentUserId ?? 'anonymous'}:${chat.id}`,
    [chat.id, currentUserId],
  );

  const applyPins = useCallback((rows: PinnedMessageRow[]) => {
    const ordered = [...rows].sort((a, b) => {
      const timeDifference = Date.parse(b.created_at) - Date.parse(a.created_at);
      return timeDifference || b.id - a.id;
    });
    setPins(ordered);
    setPinnedMessageIds(new Set(ordered.map((pin) => pin.message.id)));

    if (ordered.length === 0) {
      setHasUnseenPin(false);
      useChatStore.getState().clearChatPinUnseen(chat.id);
      return;
    }

    try {
      const seen = JSON.parse(localStorage.getItem(pinSeenStorageKey) || '{}') as {
        id?: number;
        createdAt?: string;
      };
      const latest = ordered[0];
      const latestTime = Date.parse(latest.created_at);
      const seenTime = seen.createdAt ? Date.parse(seen.createdAt) : Number.NEGATIVE_INFINITY;
      const isUnseen = latestTime > seenTime || (latestTime === seenTime && latest.id > (seen.id ?? 0));
      setHasUnseenPin(isUnseen);
      if (isUnseen) {
        useChatStore.getState().markChatPinUnseen(chat.id);
      } else {
        useChatStore.getState().clearChatPinUnseen(chat.id);
      }
    } catch {
      setHasUnseenPin(true);
      useChatStore.getState().markChatPinUnseen(chat.id);
    }
  }, [chat.id, pinSeenStorageKey]);

  // Every pin_update event triggers a refetch, so a rapid pin/unpin sequence has
  // several of these in flight at once and they do not have to resolve in order.
  // Switching channels starts another one without cancelling the previous
  // channel's. Only the newest request may write, so a late reply cannot
  // reinstate a stale list or show one channel's pins on another.
  const pinRequestGenerationRef = useRef(0);

  const refreshPins = useCallback(async (showError = false) => {
    const generation = ++pinRequestGenerationRef.current;
    try {
      const rows = await listPins(chat.slug);
      if (generation !== pinRequestGenerationRef.current) return;
      applyPins(rows);
    } catch {
      if (generation !== pinRequestGenerationRef.current) return;
      if (showError) toast.error('Could not load pinned messages');
    }
  }, [applyPins, chat.slug]);

  const acknowledgePins = useCallback((latestPin: PinnedMessageRow | undefined = pins[0]) => {
    if (!latestPin) return;
    try {
      localStorage.setItem(pinSeenStorageKey, JSON.stringify({
        id: latestPin.id,
        createdAt: latestPin.created_at,
      }));
    } catch {
      // The banner can still be used when storage is disabled.
    }
    setHasUnseenPin(false);
    useChatStore.getState().clearChatPinUnseen(chat.id);
  }, [chat.id, pinSeenStorageKey, pins]);

  // Load channel highlights and private saved IDs whenever the active chat changes.
  useEffect(() => {
    pinRequestsInFlightRef.current.clear();
    setPins([]);
    setPinnedMessageIds(new Set());
    setHasUnseenPin(false);
    resetPinsPanel();
    setSavedMessageIds(new Set());
    setPendingScheduledCount(0);
    void refreshPins(true);
    listSavedMessages()
      .then((saved) => setSavedMessageIds(new Set(saved.map((s) => s.message.id))))
      .catch(() => {});
    refreshScheduledCount();
  }, [chat.id, refreshPins, refreshScheduledCount, resetPinsPanel]);

  const { forward, isForwarding } = useForwardMessages();

  const handleSocketChatMessage = useCallback((event: ChatWsEvent) => {
    if (!event.message) return;
    const rawChatId = event.message.chat_id ?? event.message.chat ?? event.chat_id;
    const messageChatId = typeof rawChatId === 'string' ? Number(rawChatId) : rawChatId;
    if (!messageChatId || Number.isNaN(messageChatId)) return;
    useChatStore.getState().addMessage(
      messageChatId,
      { ...event.message, chat_id: messageChatId },
      currentUserId ?? undefined
    );
  }, [currentUserId]);

  const handleSocketTypingIndicator = useCallback((event: ChatWsEvent) => {
    if (event.chat_id !== chat.id || !event.user_id || event.user_id === currentUserId) return;
    const { setTypingUser, clearTypingUser } = useChatStore.getState();
    if (event.is_typing) {
      setTypingUser(event.chat_id, event.user_id);
    } else {
      clearTypingUser(event.chat_id, event.user_id);
    }
  }, [chat.id, currentUserId]);

  const handleSocketMessageStatusUpdate = useCallback((event: ChatWsEvent) => {
    const messageId = Number(event.message_id);
    if (!Number.isFinite(messageId) || !event.message?.statuses) return;
    useChatStore.getState().updateMessage(messageId, {
      statuses: event.message.statuses,
    });
  }, []);

  const handleSocketReactionUpdate = useCallback((event: ChatWsEvent) => {
    const r = event.reaction;
    if (!r) return;
    if (Array.isArray(r.reactions)) {
      useChatStore.getState().applyReactionSnapshot(r.message_id, r.reactions, r.reactions_version, currentUserId);
    } else {
      useChatStore.getState().applyReactionUpdate(r.message_id, r.emoji, r.action, r.user, currentUserId);
    }
  }, [currentUserId]);

  const handleSocketPinUpdate = useCallback((event: ChatWsEvent) => {
    if (event.chat_id !== chat.id) return;
    void refreshPins(false);
    setPinRefreshKey((previous) => previous + 1);
  }, [chat.id, refreshPins]);

  const handleSocketPresenceUpdate = useCallback((event: ChatWsEvent) => {
    const userId = Number(event.user_id);
    if (!Number.isFinite(userId) || typeof event.is_online !== 'boolean') return;
    useChatStore.getState().updateUserPresence(userId, event.is_online, event.version);
  }, []);

  const handleSocketPresenceSnapshot = useCallback((event: ChatWsEvent) => {
    if (!Array.isArray(event.users)) return;
    useChatStore.getState().setPresenceSnapshot(event.users);
  }, []);

  const handleOutboxAck = useCallback((event: ChatWsEvent) => {
    const committed = Array.isArray(event.committed) ? event.committed : [];
    void useChatStore.getState().reconcileOutboxAck(committed);
  }, []);

  const { sendTypingStart, sendTypingStop } = useChatWebSocket(currentUserId, {
    onChatMessage: handleSocketChatMessage,
    onTypingIndicator: handleSocketTypingIndicator,
    onMessageStatusUpdate: handleSocketMessageStatusUpdate,
    onReactionUpdate: handleSocketReactionUpdate,
    onPinUpdate: handleSocketPinUpdate,
    onPresenceUpdate: handleSocketPresenceUpdate,
    onPresenceSnapshot: handleSocketPresenceSnapshot,
    onOutboxAck: handleOutboxAck,
    onOpen: () => void refreshPins(false),
  });

  const {
    messages,
    isLoadingMessages,
    isLoadingMoreMessages,
    isSending,
    hasMore,
    hasFetchedInitially,
    sendRich,
    loadMoreMessages,
    markAllAsRead,
  } = useMessageData({ chatId: chat.id, autoFetch: true });
  
  // Track last message count to detect new messages
  const lastMessageCountRef = useRef<number>(0);
  const lastReadChatIdRef = useRef<number | null>(null);
  const markAsReadTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const jumpLoadAttemptsRef = useRef(0);
  const jumpedToMessageRef = useRef<string | null>(null);
  const resolvingMissingTargetRef = useRef<string | null>(null);
  const previousChatIdRef = useRef<number | null>(null);

  const chatProjectId = useMemo(() => {
    const rawProjectId = (chat as any).project_id ?? (chat as any).project;
    const parsed = Number(rawProjectId);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  }, [chat]);

  const availableTargetChats = useMemo(() => {
    if (!chatProjectId) return [];
    return (chatsByProject[chatProjectId] || []).filter((c) => c.id !== chat.id);
  }, [chatProjectId, chatsByProject, chat.id]);

  const mentionParticipants = useMemo(
    () => (chat.participants ?? []).filter((participant) => participant.user),
    [chat.participants],
  );

  useEffect(() => {
    setIsSelectMode(false);
    setSelectedMessageIds([]);
    setIsForwardDialogOpen(false);
    setFirstUnreadMessageId(null);
    setReplyingTo(null);
    setActiveThreadMessage(null);
    unreadCapturedForChatRef.current = null;
    jumpLoadAttemptsRef.current = 0;
    jumpedToMessageRef.current = null;
    resolvingMissingTargetRef.current = null;
  }, [chat.id]);

  // Show the channel-description banner once per (user, channel) — channels
  // only (DMs don't have descriptions), and only when a description exists.
  // Dismissed state lives in localStorage so existing members don't re-see it.
  useEffect(() => {
    setShowDescriptionBanner(false);
    if (chat.type !== 'group') return;
    const desc = chat.description?.trim();
    if (!desc) return;
    if (!currentUserId) return;
    try {
      const key = `mj:chat:descSeen:${currentUserId}:${chat.id}`;
      if (typeof window !== 'undefined' && window.localStorage.getItem(key)) return;
      setShowDescriptionBanner(true);
    } catch {
      // localStorage can throw in private mode — just show the banner.
      setShowDescriptionBanner(true);
    }
  }, [chat.id, chat.type, chat.description, currentUserId]);

  const dismissDescriptionBanner = useCallback(() => {
    setShowDescriptionBanner(false);
    if (!currentUserId) return;
    try {
      window.localStorage.setItem(
        `mj:chat:descSeen:${currentUserId}:${chat.id}`,
        '1',
      );
    } catch {
      // ignore — banner just won't persist as dismissed for this session
    }
  }, [chat.id, currentUserId]);

  useEffect(() => {
    const previousChatId = previousChatIdRef.current;
    if (previousChatId !== null && previousChatId !== chat.id) {
      setShowSwitchLoadingSkeleton(true);
    }
    previousChatIdRef.current = chat.id;
  }, [chat.id]);

  useEffect(() => {
    if (!isLoadingMessages) {
      setShowSwitchLoadingSkeleton(false);
    }
  }, [isLoadingMessages]);

  const targetMessageId = useMemo(() => {
    const raw = searchParams.get('messageId');
    const parsed = raw ? Number(raw) : NaN;
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  }, [searchParams]);

  const targetChatId = useMemo(() => {
    const raw = searchParams.get('chatId');
    const parsed = raw ? Number(raw) : NaN;
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  }, [searchParams]);

  const targetFileId = useMemo(() => {
    const raw = searchParams.get('fileId');
    const parsed = raw ? Number(raw) : NaN;
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  }, [searchParams]);

  const targetJumpId = useMemo(() => {
    return searchParams.get('jumpId') || null;
  }, [searchParams]);

  // When the URL carries `threadMessageId`, it means the user jumped to a thread
  // reply from a different chat (saved/files/pinned cross-chat). `messageId` is
  // the parent root; this is the reply to highlight inside the ThreadPanel.
  const targetThreadMessageId = useMemo(() => {
    const raw = searchParams.get('threadMessageId');
    const parsed = raw ? Number(raw) : NaN;
    return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
  }, [searchParams]);

  const routeMatchesActiveChat = isMessageDeepLinkForChat(routeChatSlug, chat.slug);

  const targetJumpKey = useMemo(() => {
    if (!targetMessageId) return null;
    return targetJumpId ?? `${chat.id}:${targetMessageId}:${targetFileId ?? 'message'}`;
  }, [chat.id, targetFileId, targetJumpId, targetMessageId]);

  const hasTargetMessage = useMemo(() => {
    if (!targetMessageId) return false;
    return messages.some((message) => Number(message.id) === targetMessageId);
  }, [messages, targetMessageId]);

  const jumpTarget = useMemo(() => {
    if (!targetMessageId || !targetJumpKey || !hasTargetMessage) return null;
    if (!routeMatchesActiveChat) return null;
    if (targetChatId && targetChatId !== chat.id) return null;
    if (!hasFetchedInitially || isLoadingMessages || showSwitchLoadingSkeleton) return null;
    return {
      messageId: targetMessageId,
      attachmentId: targetFileId ?? undefined,
      requestId: targetJumpKey,
    };
  }, [
    chat.id,
    hasFetchedInitially,
    hasTargetMessage,
    isLoadingMessages,
    routeMatchesActiveChat,
    showSwitchLoadingSkeleton,
    targetChatId,
    targetFileId,
    targetJumpKey,
    targetMessageId,
  ]);

  useEffect(() => {
    jumpLoadAttemptsRef.current = 0;
    jumpedToMessageRef.current = null;
    resolvingMissingTargetRef.current = null;
  }, [chat.id, targetMessageId, targetFileId, targetJumpId]);

  useEffect(() => {
    // If URL includes a messageId, load older history one page at a time until
    // the message exists in the rendered list, then ask MessageList to focus it.
    if (!targetMessageId) return;
    // setCurrentChat updates synchronously, while router.replace completes on a
    // later render. Ignore the old route's target during that transition.
    if (!routeMatchesActiveChat) return;
    if (targetChatId && targetChatId !== chat.id) return;
    if (isLoadingMessages) return; // wait for initial load to complete
    if (showSwitchLoadingSkeleton) return; // wait until MessageList is rendering messages, not the switch skeleton

    // Don't declare "not found" until the initial fetch for this chat has completed.
    // Without this guard, stale store messages (from a previous load) can cause a false
    // "not found" result in the brief window before isFetchingMessages turns true.
    if (!hasFetchedInitially) return;

    const jumpKey = targetJumpKey ?? `${chat.id}:${targetMessageId}:${targetFileId ?? 'message'}`;

    if (hasTargetMessage) {
      if (jumpedToMessageRef.current === jumpKey) return;
      jumpedToMessageRef.current = jumpKey;
      return;
    }

    if (isLoadingMoreMessages) return;

    // Load real history pages first so deep links keep surrounding context.
    // Falling back to a single-message lookup can create a discontinuous list
    // where the target sits beside unrelated latest messages.
    if (hasMore && jumpLoadAttemptsRef.current < 20) {
      jumpLoadAttemptsRef.current += 1;
      void loadMoreMessages();
      return;
    }

    if (resolvingMissingTargetRef.current === jumpKey) return;
    resolvingMissingTargetRef.current = jumpKey;

    let cancelled = false;
    const clearStaleTarget = () => {
      if (cancelled) return;
      if (jumpedToMessageRef.current === jumpKey) return;
      jumpedToMessageRef.current = jumpKey; // prevent repeated toasts on re-renders
      toast.error('Message not found — it may have been deleted.');
      const params = new URLSearchParams(searchParams.toString());
      params.delete('messageId');
      params.delete('threadMessageId');
      params.delete('fileId');
      params.delete('jumpId');
      router.replace(params.size ? `?${params.toString()}` : window.location.pathname);
    };

    // The target can be a thread reply from Files. Thread replies are not rendered in
    // the main timeline, so resolve old links to the root message before warning.
    void getMessage(targetMessageId)
      .then((target) => {
        if (cancelled) return;
        if (target.is_revoked) {
          clearStaleTarget();
          return;
        }

        if (target.parent_message_id) {
          router.replace(
            buildUrl(buildMessagesPath(chat.slug, {
              messageId: target.parent_message_id,
              threadMessageId: targetMessageId,
            })),
            { scroll: false },
          );
          return;
        }

        const rawTargetChatId = target.chat_id ?? target.chat;
        const messageChatId = typeof rawTargetChatId === 'string' ? Number(rawTargetChatId) : rawTargetChatId;
        if (Number(messageChatId) !== Number(chat.id)) {
          clearStaleTarget();
          return;
        }

        useChatStore.getState().prependMessages(chat.id, [
          { ...target, chat_id: chat.id },
        ]);
      })
      .catch(clearStaleTarget);

    return () => {
      cancelled = true;
    };
  }, [
    buildUrl,
    chat.id,
    chat.slug,
    hasFetchedInitially,
    hasMore,
    hasTargetMessage,
    isLoadingMessages,
    isLoadingMoreMessages,
    loadMoreMessages,
    messages,
    router,
    routeMatchesActiveChat,
    searchParams,
    showSwitchLoadingSkeleton,
    targetChatId,
    targetFileId,
    targetJumpKey,
    targetJumpId,
    targetMessageId,
  ]);
  
  // When the URL carries `threadMessageId`, open the thread for the message at
  // `messageId` (the parent) and highlight the reply inside it. Drives the
  // cross-chat "Jump to message" from Saved/Files/Pinned for thread replies.
  useEffect(() => {
    if (!targetThreadMessageId || !targetMessageId) return;
    if (!routeMatchesActiveChat) return;
    if (activeThreadMessage?.id === targetMessageId) {
      // Thread already open for the right parent — just set the highlight.
      if (threadHighlightMessageId !== targetThreadMessageId) {
        setThreadHighlightMessageId(targetThreadMessageId);
      }
      return;
    }
    let cancelled = false;
    void getMessage(targetMessageId)
      .then((parent) => {
        if (cancelled) return;
        closePinsPanel();
        closeDetailsPanel();
        setActiveThreadMessage(parent);
        setThreadHighlightMessageId(targetThreadMessageId);
        // Drop `threadMessageId` from the URL so closing the thread (or any
        // subsequent re-render) doesn't auto-re-open it.
        const params = new URLSearchParams(searchParams.toString());
        params.delete('threadMessageId');
        router.replace(params.size ? `?${params.toString()}` : window.location.pathname);
      })
      .catch(() => { /* fall through — at worst the parent jump still happens */ });

    return () => { cancelled = true; };
  }, [
    activeThreadMessage?.id,
    chat.slug,
    closeDetailsPanel,
    closePinsPanel,
    router,
    routeMatchesActiveChat,
    searchParams,
    targetMessageId,
    targetThreadMessageId,
    threadHighlightMessageId,
  ]);

  // Mark messages as read when viewing - both on open AND when new messages arrive
  useEffect(() => {
    if (!chat.id || messages.length === 0) return;

    const isOpeningChat = lastReadChatIdRef.current !== chat.id;
    if (isOpeningChat) lastReadChatIdRef.current = chat.id;

    // Get store actions
    const { updateChat, updateUnreadCount, fetchGlobalUnreadCount } = useChatStore.getState();

    // Always keep local unread count at 0 while viewing (optimistic update)
    updateChat(chat.id, { unread_count: 0 });
    updateUnreadCount(chat.id, 0);

    // Debounce the API call to avoid too many requests when messages stream in
    if (markAsReadTimeoutRef.current) {
      clearTimeout(markAsReadTimeoutRef.current);
    }

    // Always call the backend on first open, even if the message list came from
    // cache and its count did not grow. After that, only call when new messages arrive.
    if (isOpeningChat || messages.length > lastMessageCountRef.current) {
      markAsReadTimeoutRef.current = setTimeout(() => {
        markAllAsRead().then(() => {
          // Refetch global unread count from backend to ensure accuracy
          fetchGlobalUnreadCount();
          // Intentionally NOT clearing firstUnreadMessageId here — the divider
          // stays visible until the user closes and reopens the conversation.
        });
      }, 500); // Debounce 500ms
    }

    lastMessageCountRef.current = messages.length;
    
    // Cleanup timeout on unmount
    return () => {
      if (markAsReadTimeoutRef.current) {
        clearTimeout(markAsReadTimeoutRef.current);
      }
    };
  }, [chat.id, messages.length, markAllAsRead]);
  
  // When a fresh positive capturedUnreadCount arrives (e.g. setChatsForProject resolves
  // after a race, or account switch) and the divider is not currently showing, unlock
  // the capture ref so the effect below can re-run.  Must be defined BEFORE the
  // capture effect so it runs first within the same React commit.
  useEffect(() => {
    if (firstUnreadMessageId !== null) return; // Divider already visible — don't disturb it
    if (capturedUnreadCount > 0) {
      unreadCapturedForChatRef.current = null; // Unlock → let the capture effect fire
    }
  }, [chat.id, capturedUnreadCount, firstUnreadMessageId]);

  // Capture the first unread message once per chat open, before markAllAsRead fires.
  // Uses capturedUnreadCount (snapshotted at click-time in setCurrentChat, then
  // refreshed by setChatsForProject) rather than chat.unread_count.
  useEffect(() => {
    if (unreadCapturedForChatRef.current === chat.id) return;
    if (messages.length === 0) return;
    if (capturedUnreadCount > 0 && capturedUnreadCount <= messages.length) {
      const firstUnread = messages[messages.length - capturedUnreadCount];
      if (firstUnread) setFirstUnreadMessageId(firstUnread.id);
    }
    // Only lock the ref once we've seen real data (non-zero count, or confirmed 0
    // by having messages loaded — the unlock effect above will re-open it if needed).
    // Only lock once messages are actually loaded. If we locked on capturedUnreadCount > 0
    // alone (messages still empty), the effect would return early on the messages.length
    // guard above and then the ref would be locked before we could ever capture.
    if (messages.length > 0) {
      unreadCapturedForChatRef.current = chat.id;
    }
  }, [chat.id, capturedUnreadCount, messages]);

  const handleEditMessage = async (messageId: number, newContent: string) => {
    const { updateMessage } = useChatStore.getState();
    const original = messages.find((m) => m.id === messageId);
    updateMessage(messageId, { content: newContent, rich_body: null, mentioned_user_ids: [], is_edited: true });
    try {
      const updated = await editMessage(messageId, newContent, null, []);
      updateMessage(messageId, {
        content: updated.content,
        rich_body: updated.rich_body ?? null,
        mentioned_user_ids: updated.mentioned_user_ids ?? [],
        is_edited: updated.is_edited ?? true,
      });
    } catch {
      updateMessage(messageId, {
        content: original?.content ?? newContent,
        rich_body: original?.rich_body ?? null,
        mentioned_user_ids: original?.mentioned_user_ids ?? [],
        is_edited: original?.is_edited ?? false,
      });
    }
  };

  const handleDeleteMessage = async (messageId: number) => {
    const original = messages.find((message) => message.id === messageId);
    const { updateMessage, triggerFilesRefresh } = useChatStore.getState();
    updateMessage(messageId, {
      is_deleted: true,
      deleted_at: new Date().toISOString(),
      content: '',
      rich_body: null,
      attachments: [],
      has_attachments: false,
      attachment_count: 0,
      reactions: [],
      mentioned_user_ids: [],
      missing_forwarded_attachments: [],
      reply_to: null,
      forwarded_from: null,
      is_edited: false,
      can_revoke: false,
    });
    try {
      const response = await deleteMessage(messageId);
      updateMessage(messageId, response.message);
      // Refresh Files tab after the backend confirms attachments should no longer be listed.
      triggerFilesRefresh();
    } catch {
      if (original) updateMessage(messageId, original);
    }
  };

  const handleReactionAdd = useCallback(async (messageId: number, emoji: string) => {
    if (!currentUserId) return;
    const { applyReactionUpdate, applyReactionSnapshot } = useChatStore.getState();
    const actor = { id: currentUserId, username: user?.username ?? '' };
    applyReactionUpdate(messageId, emoji, 'added', actor, currentUserId);
    try {
      const response = await addReaction(messageId, emoji);
      applyReactionSnapshot(messageId, response.message.reactions ?? [], response.reactions_version, currentUserId);
    } catch {
      applyReactionUpdate(messageId, emoji, 'removed', actor, currentUserId);
    }
  }, [currentUserId, user]);

  const handleReactionRemove = useCallback(async (messageId: number, emoji: string) => {
    if (!currentUserId) return;
    const { applyReactionUpdate, applyReactionSnapshot } = useChatStore.getState();
    const actor = { id: currentUserId, username: user?.username ?? '' };
    applyReactionUpdate(messageId, emoji, 'removed', actor, currentUserId);
    try {
      const response = await removeReaction(messageId, emoji);
      applyReactionSnapshot(messageId, response.message.reactions ?? [], response.reactions_version, currentUserId);
    } catch {
      applyReactionUpdate(messageId, emoji, 'added', actor, currentUserId);
    }
  }, [currentUserId, user]);

  const handleQuoteReply = useCallback((message: Message) => {
    setReplyingTo(message);
  }, []);

  const handleForwardSingle = useCallback((messageId: number) => {
    setIsSelectMode(true);
    setSelectedMessageIds([messageId]);
    setIsForwardDialogOpen(true);
  }, []);

  const handleSendRich = async (data: RichSendData) => {
    await sendRich(
      data.content,
      data.rich_body,
      data.mention_ids,
      data.attachment_ids,
      data.reply_to_id,
    );
    setReplyingTo(null);
  };

  const toggleSelectMode = () => {
    if (isSelectMode) {
      setIsSelectMode(false);
      setSelectedMessageIds([]);
      setIsForwardDialogOpen(false);
      return;
    }
    setIsSelectMode(true);
  };

  const handleToggleSelectMessage = (messageId: number) => {
    if (!isSelectMode) return;
    setSelectedMessageIds((prev) =>
      prev.includes(messageId)
        ? prev.filter((id) => id !== messageId)
        : [...prev, messageId]
    );
  };

  const handleOpenForwardDialog = () => {
    if (selectedMessageIds.length === 0 || isForwarding) return;
    setIsForwardDialogOpen(true);
  };

  const handleForwardSubmit = async (targetChatIds: number[], targetUserIds: number[]) => {
    if (!chatProjectId || selectedMessageIds.length === 0) return;

    const response = await forward({
      source_chat_id: chat.id,
      source_message_ids: selectedMessageIds,
      target_chat_ids: targetChatIds,
      target_user_ids: targetUserIds,
    });

    if (!response) return;

    setIsForwardDialogOpen(false);
    setSelectedMessageIds([]);
    setIsSelectMode(false);
  };

  // Get the other participant (not current user) for private chats
  const getOtherParticipant = () => {
    if (chat.type === 'group' || !chat.participants) return null;
    // Ensure numeric comparison to avoid type mismatch (user.id can be string | number | undefined)
    if (currentUserId === null) return null;
    return chat.participants.find(p => p.user.id !== currentUserId);
  };

  const otherParticipant = getOtherParticipant();
  const otherParticipantRole =
    chat.type === 'private' && otherParticipant?.user?.id
      ? roleByUserId?.[otherParticipant.user.id]
      : undefined;
  const chatName = chat.type === 'group' 
    ? limitName(chat.name || 'Group Chat', MAX_CHANNEL_NAME_LENGTH)
    : (otherParticipant?.user?.username || 'Chat');
  const handleOpenThread = useCallback((message: Message) => {
    closePinsPanel();
    closeDetailsPanel();
    setActiveThreadMessage((prev) => (prev?.id === message.id ? null : message));
  }, [closeDetailsPanel, closePinsPanel]);

  const handleChatUpdated = useCallback((updated: Chat) => {
    const updates: Partial<Chat> = {
      name: updated.name,
      topic: updated.topic,
      description: updated.description,
      visibility: updated.visibility,
    };
    if (updated.participants) {
      updates.participants = updated.participants;
    }
    useChatStore.getState().updateChat(updated.id, updates);
  }, []);

  const handlePinMessage = useCallback(async (messageId: number) => {
    if (pinRequestsInFlightRef.current.has(messageId)) return;
    pinRequestsInFlightRef.current.add(messageId);
    const alreadyPinned = pinnedMessageIds.has(messageId);
    try {
      if (alreadyPinned) {
        await unpinMessage(chat.slug, messageId);
        applyPins(pins.filter((pin) => pin.message.id !== messageId));
        setPinRefreshKey((prev) => prev + 1);
        toast.success('Message unpinned');
      } else {
        const createdPin = await pinMessage(chat.slug, messageId);
        applyPins([createdPin, ...pins.filter((pin) => pin.message.id !== messageId)]);
        // The manager who performed the action has already seen this pin. Other
        // members receive an unread pin_update through the chat WebSocket.
        acknowledgePins(createdPin);
        setPinRefreshKey((prev) => prev + 1);
        toast.success('Message pinned');
      }
    } catch {
      toast.error(alreadyPinned ? 'Failed to unpin message' : 'Failed to pin message');
    } finally {
      pinRequestsInFlightRef.current.delete(messageId);
    }
  }, [acknowledgePins, applyPins, chat.slug, pinnedMessageIds, pins]);

  const handleOpenPinnedMessages = useCallback(() => {
    acknowledgePins();
    closeDetailsPanel();
    dismissThreadPanel();
    openPinsPanel();
  }, [acknowledgePins, closeDetailsPanel, dismissThreadPanel, openPinsPanel]);

  const [descClosing, setDescClosing] = useState(false);
  const descCloseTimerRef = useRef<number | null>(null);

  const closeDescriptionBanner = useCallback(() => {
    if (descCloseTimerRef.current !== null) return;
    setDescClosing(true);
    descCloseTimerRef.current = window.setTimeout(() => {
      descCloseTimerRef.current = null;
      dismissDescriptionBanner();
      setDescClosing(false);
    }, 200);
  }, [dismissDescriptionBanner]);

  const handleJumpToPinnedMessage = useCallback((messageId: number, parentMessageId?: number | null) => {
    acknowledgePins();
    closeDetailsPanel();
    closePinsPanel();
    dismissThreadPanel();
    router.replace(
      buildUrl(buildMessagesPath(chat.slug, {
        messageId: parentMessageId ?? messageId,
        threadMessageId: parentMessageId ? messageId : null,
        jumpId: String(Date.now()),
      })),
      { scroll: false },
    );
  }, [acknowledgePins, buildUrl, chat.slug, router, closeDetailsPanel, closePinsPanel, dismissThreadPanel]);

  const handleSaveMessage = useCallback(async (messageId: number) => {
    const alreadySaved = savedMessageIds.has(messageId);
    try {
      if (alreadySaved) {
        // Find the saved record id — not stored locally; just refresh the list
        await listSavedMessages().then((saved) => {
          const record = saved.find((s) => s.message.id === messageId);
          if (record) return unsaveMessage(record.id);
        });
        setSavedMessageIds((prev) => { const next = new Set(prev); next.delete(messageId); return next; });
        toast.success('Removed from saved');
      } else {
        await saveMessage(messageId);
        setSavedMessageIds((prev) => new Set([...prev, messageId]));
        toast.success('Saved for later');
      }
    } catch {
      toast.error(alreadySaved ? 'Failed to remove saved' : 'Failed to save message');
    }
  }, [savedMessageIds]);

  const handleRemindMessage = useCallback((messageId: number) => {
    setReminderMessageId(messageId);
  }, []);

  const handleReminderConfirm = useCallback((time: Date) => {
    if (!reminderMessageId) return;
    // Store reminder in localStorage — a polling effect surfaces it as a toast
    const key = 'ms_reminders';
    try {
      const existing = JSON.parse(localStorage.getItem(key) || '[]') as Array<{ messageId: number; time: number; chatId: number }>;
      existing.push({ messageId: reminderMessageId, time: time.getTime(), chatId: chat.id });
      localStorage.setItem(key, JSON.stringify(existing));
      toast.success(`Reminder set for ${time.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`);
    } catch {
      toast.error('Failed to set reminder');
    }
    setReminderMessageId(null);
  }, [reminderMessageId, chat.id]);

  const handleScheduleSend = useCallback(async (data: RichSendData, scheduledAt: Date) => {
    if (!chatProjectId) return;
    try {
      const newMsg = await createScheduledMessage({
        chat_id: chat.id,
        content: data.content,
        rich_body: data.rich_body as object,
        mention_ids: data.mention_ids,
        attachment_ids: data.attachment_ids ?? [],
        reply_to_id: data.reply_to_id ?? null,
        scheduled_at: scheduledAt.toISOString(),
      });
      toast.success(`Message scheduled for ${scheduledAt.toLocaleString()}`);
      setLastScheduledMsg(newMsg);
      refreshScheduledCount();
    } catch (err: unknown) {
      // Surface the API validation message when available, otherwise fall back.
      const apiData = (err as any)?.response?.data;
      const apiMsg: string | undefined =
        typeof apiData?.detail === 'string' ? apiData.detail :
        typeof apiData?.scheduled_at?.[0] === 'string' ? apiData.scheduled_at[0] :
        typeof apiData?.non_field_errors?.[0] === 'string' ? apiData.non_field_errors[0] :
        typeof apiData === 'string' ? apiData :
        undefined;
      toast.error(apiMsg ?? 'Failed to schedule message');
    }
  }, [chat.id, chatProjectId, refreshScheduledCount]);

  const handleTypingStart = useCallback(() => {
    sendTypingStart(chat.id);
  }, [chat.id, sendTypingStart]);

  const handleTypingStop = useCallback(() => {
    sendTypingStop(chat.id);
  }, [chat.id, sendTypingStop]);

  // Slash commands wired for this chat
  const slashCommands = useMemo<SlashCommand[]>(() => [
    {
      name: 'topic',
      description: 'Set the channel topic  /topic <new topic>',
      onExecute: (args, clearEditor) => {
        if (!args) { toast.error('Usage: /topic <new topic>'); return; }
        updateChatDetails(chat.id, { topic: args })
          .then((updated) => {
            useChatStore.getState().updateChat(chat.id, { topic: updated.topic });
            toast.success('Topic updated');
            clearEditor();
          })
          .catch(() => toast.error('Failed to update topic'));
      },
    },
    {
      name: 'mute',
      description: 'Mute notifications for this channel',
      onExecute: (_args, clearEditor) => {
        updateNotificationSettings(chat.id, { is_muted: true })
          .then(() => { toast.success('Channel muted'); clearEditor(); })
          .catch(() => toast.error('Failed to mute channel'));
      },
    },
    {
      name: 'unmute',
      description: 'Unmute notifications for this channel',
      onExecute: (_args, clearEditor) => {
        updateNotificationSettings(chat.id, { is_muted: false })
          .then(() => { toast.success('Channel unmuted'); clearEditor(); })
          .catch(() => toast.error('Failed to unmute channel'));
      },
    },
  ], [chat.id, chatProjectId]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Chat Header */}
      <div className="flex flex-shrink-0 items-center gap-2 border-b border-gray-200 bg-white px-3 py-2 sm:gap-3 sm:px-4 sm:py-3">
        <button
          onClick={onBack}
          className={[
            'rounded p-1 transition-colors hover:bg-gray-100',
            hideBackOnDesktop ? 'md:hidden' : '',
          ].join(' ')}
          aria-label="Back to chat list"
          title="Back to chat list"
        >
          <ArrowLeft className="h-5 w-5 text-gray-600" />
        </button>
        
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 min-w-0">
            <h3
              className="max-w-[min(42rem,55vw)] truncate font-semibold text-gray-900"
              title={chat.type === 'group' ? chat.name || 'Group Chat' : chatName}
            >
              {chatName}
            </h3>
            {chat.type === 'private' && otherParticipantRole && (
              <span className="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded flex-shrink-0">
                {otherParticipantRole}
              </span>
            )}
          </div>
          {chat.type === 'group' && chat.participants && (
            <p className="truncate text-xs leading-tight text-gray-400" title={chat.topic || undefined}>
              {chat.participants.length} member{chat.participants.length !== 1 ? 's' : ''}
              {chat.topic ? ` · ${chat.topic}` : ''}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          {chat.type === 'group' && (
            <button
              type="button"
              onClick={() => {
                if (showPinnedMessages) {
                  closePinsPanel();
                } else {
                  handleOpenPinnedMessages();
                }
              }}
              className={[
                'relative inline-flex items-center gap-1 rounded-md border px-2 py-1.5 text-xs font-medium transition sm:px-2.5',
                showPinnedMessages
                  ? 'border-teal-300 bg-teal-50 text-teal-700 shadow-sm'
                  : pins.length > 0
                    ? 'border-teal-200 bg-teal-50/60 text-teal-700 shadow-sm hover:bg-teal-50'
                    : 'border-gray-300 text-gray-700 hover:bg-gray-50',
              ].join(' ')}
              aria-label={`Pinned messages${pins.length ? `, ${pins.length}` : ''}${hasUnseenPin ? ', new pin' : ''}`}
              data-testid="pinned-messages-button"
            >
              <Pin className="h-3.5 w-3.5" />
              <span className="hidden min-[420px]:inline">Pins</span>
              {pins.length > 0 && (
                <span className="rounded-full bg-teal-100 px-1.5 text-[10px] font-semibold text-teal-700">
                  {pins.length}
                </span>
              )}
              {hasUnseenPin && (
                <span className="absolute -right-1 -top-1 flex h-2.5 w-2.5" aria-hidden="true">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-teal-400 opacity-75" />
                  <span className="relative inline-flex h-2.5 w-2.5 rounded-full border border-white bg-teal-500" />
                </span>
              )}
            </button>
          )}

          {/* Channel details toggle */}
          <button
            onClick={() => {
              closePinsPanel();
              dismissThreadPanel();
              if (showChannelDetails) {
                closeDetailsPanel();
              } else {
                openDetailsPanel();
              }
            }}
            className={[
              'inline-flex items-center gap-1 rounded-md border px-2 py-1.5 text-xs font-medium transition sm:px-2.5',
              showChannelDetails
                ? 'border-teal-300 bg-teal-50 text-teal-700'
                : 'border-gray-300 text-gray-700 hover:bg-gray-50',
            ].join(' ')}
            aria-label="Channel details"
          >
            <Info className="w-3.5 h-3.5" />
            <span className="hidden min-[380px]:inline">Details</span>
          </button>

          {!isSelectMode ? (
            <button
              onClick={toggleSelectMode}
              className="inline-flex items-center gap-1 rounded-md border border-gray-300 px-2 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 sm:px-2.5"
              aria-label="Select messages"
            >
              <CheckSquare className="w-3.5 h-3.5" />
              <span className="hidden min-[380px]:inline">Select</span>
            </button>
          ) : (
            <>
              <span className="hidden text-xs text-gray-600 min-[380px]:inline">
                {selectedMessageIds.length} selected
              </span>
              <button
                onClick={handleOpenForwardDialog}
                disabled={selectedMessageIds.length === 0 || isForwarding}
                className="inline-flex items-center gap-1 rounded-md bg-[#3CCED7] px-2 py-1.5 text-xs font-medium text-white hover:bg-[#2AB5BD] disabled:cursor-not-allowed disabled:opacity-50 sm:px-2.5"
                aria-label="Forward selected messages"
              >
                <Forward className="w-3.5 h-3.5" />
                <span className="hidden min-[380px]:inline">Forward</span>
              </button>
              <button
                onClick={toggleSelectMode}
                className="inline-flex items-center gap-1 rounded-md border border-gray-300 px-2 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 sm:px-2.5"
                aria-label="Cancel message selection"
              >
                <X className="w-3.5 h-3.5" />
                <span className="hidden min-[380px]:inline">Cancel</span>
              </button>
            </>
          )}
        </div>
      </div>

      {/* Main area: timeline + optional thread panel side-by-side */}
      <div className="flex min-h-0 flex-1 overflow-hidden">
        {/* Timeline column */}
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          {(pins[0] || (showDescriptionBanner && chat.description)) && (
            <div className="relative z-10 shrink-0 divide-y divide-gray-200 border-b border-gray-200 bg-white shadow-[0_4px_16px_rgba(16,24,40,0.07)]" data-testid="channel-notices">
              {pins[0] && (
                <PinnedMessageBanner
                  key={pins[0].id}
                  latestPin={pins[0]}
                  pinCount={pins.length}
                  isNew={hasUnseenPin}
                  onJumpToMessage={handleJumpToPinnedMessage}
                  onViewAll={handleOpenPinnedMessages}
                />
              )}
              {showDescriptionBanner && chat.description && (
                <section className={[
                  'relative flex items-center gap-2.5 bg-gradient-to-r from-teal-50/80 via-teal-50/40 to-white px-4 py-2',
                  descClosing ? 'pin-banner-exit' : 'pin-banner-enter',
                ].join(' ')}>
                  <span className="pin-grad-chip flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-white">
                    <Info className="h-3 w-3" />
                  </span>
                  <p className="min-w-0 flex-1 truncate text-[13px]" title={chat.description}>
                    <span className="font-semibold text-teal-700">Welcome to #{chat.name || 'this channel'}:</span>{' '}
                    <span className="text-gray-800">{chat.description}</span>
                  </p>
                  <button
                    type="button"
                    onClick={closeDescriptionBanner}
                    className="rounded-md p-1 text-gray-400 transition hover:bg-gray-100 hover:text-gray-600"
                    aria-label="Dismiss"
                    title="Dismiss"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </section>
              )}
            </div>
          )}
          <div className="flex-1 overflow-hidden">
            <MessageList
              key={`${chat.id}:${currentUserId ?? 'anonymous'}`}
              chatSlug={chat.slug}
              messages={messages}
              currentUserId={currentUserId ?? 0}
              onLoadMore={loadMoreMessages}
              hasMore={hasMore}
              isLoading={isLoadingMessages}
              isLoadingMoreMessages={isLoadingMoreMessages}
              showSwitchLoadingSkeleton={showSwitchLoadingSkeleton}
              roleByUserId={roleByUserId}
              isGroupChat={chat.type === 'group'}
              isSelectMode={isSelectMode}
              selectedMessageIds={selectedMessageIds}
              onToggleSelectMessage={handleToggleSelectMessage}
              firstUnreadMessageId={firstUnreadMessageId}
              onEditMessage={handleEditMessage}
              onDeleteMessage={handleDeleteMessage}
              onReactionAdd={handleReactionAdd}
              onReactionRemove={handleReactionRemove}
              onQuoteReply={handleQuoteReply}
              onForwardSingle={handleForwardSingle}
              onEnterSelectMode={toggleSelectMode}
              onOpenThread={handleOpenThread}
              activeThreadMessageId={activeThreadMessage?.id ?? null}
              jumpTarget={jumpTarget}
              onPinMessage={canManageChannel ? handlePinMessage : undefined}
              onSaveMessage={handleSaveMessage}
              onRemindMessage={handleRemindMessage}
              pinnedMessageIds={pinnedMessageIds}
              savedMessageIds={savedMessageIds}
            />
          </div>

          {/* Typing indicator */}
          <TypingIndicator chat={chat} currentUserId={currentUserId} />

          {/* Message Composer */}
          <div className="flex-shrink-0">
            <ChatComposer
              onSendRich={handleSendRich}
              onScheduleSend={chatProjectId ? handleScheduleSend : undefined}
              scheduledCount={pendingScheduledCount}
              slashCommands={slashCommands}
              disabled={isSending || isSelectMode || isForwarding}
              chatId={chat.id}
              projectId={chatProjectId}
              onTypingStart={handleTypingStart}
              onTypingStop={handleTypingStop}
              replyingTo={replyingTo}
              onClearReply={() => setReplyingTo(null)}
              participants={mentionParticipants}
            />
          </div>
        </div>

        {/* Thread panel */}
        {threadPanelMessage && threadPanelState !== 'closed' && (
          <div
            className={[
              'hidden shrink-0 overflow-hidden transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] motion-reduce:transition-none md:flex md:flex-col md:shadow-[-12px_0_28px_rgba(16,24,40,0.08)]',
              threadPanelState === 'open' ? 'w-80 opacity-100 xl:w-96' : 'w-0 opacity-0',
            ].join(' ')}
            aria-hidden={threadPanelState !== 'open'}
          >
            <div className="h-full w-80 shrink-0 xl:w-96">
              <ThreadPanel
                rootMessage={threadPanelMessage}
                participants={mentionParticipants}
                currentUserId={currentUserId ?? undefined}
                onClose={() => {
                  setActiveThreadMessage(null);
                  setThreadHighlightMessageId(null);
                }}
                onForwardMessage={handleForwardSingle}
                onPinMessage={canManageChannel ? handlePinMessage : undefined}
                onSaveMessage={handleSaveMessage}
                pinnedMessageIds={pinnedMessageIds}
                savedMessageIds={savedMessageIds}
                highlightMessageId={threadHighlightMessageId}
                onHighlightCleared={() => setThreadHighlightMessageId(null)}
              />
            </div>
          </div>
        )}

        {/* Dedicated channel pins drawer */}
        {showPinnedMessages && (
          <div
            className={[
              'fixed inset-0 z-50 flex min-h-0 flex-col bg-white transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] motion-reduce:transition-none',
              'md:static md:inset-auto md:z-auto md:shrink-0 md:overflow-hidden md:shadow-[-12px_0_28px_rgba(16,24,40,0.08)]',
              pinsPanelState === 'open'
                ? 'translate-x-0 opacity-100 md:w-80 xl:w-96'
                : 'pointer-events-none translate-x-full opacity-0 md:w-0 md:translate-x-0',
            ].join(' ')}
            aria-hidden={pinsPanelState !== 'open'}
          >
            <PinnedMessagesDrawer
              pins={pins}
              canManageChannel={canManageChannel}
              onClose={closePinsPanel}
              onJumpToMessage={handleJumpToPinnedMessage}
              onUnpin={handlePinMessage}
            />
          </div>
        )}

        {/* Channel details drawer */}
        {showChannelDetails && (
          <div
            className={[
              'fixed inset-0 z-50 flex min-h-0 flex-col bg-white transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] motion-reduce:transition-none',
              'md:static md:inset-auto md:z-auto md:shrink-0 md:overflow-hidden md:shadow-[-12px_0_28px_rgba(16,24,40,0.08)]',
              detailsPanelState === 'open'
                ? 'translate-x-0 opacity-100 md:w-72 xl:w-80'
                : 'pointer-events-none translate-x-full opacity-0 md:w-0 md:translate-x-0',
            ].join(' ')}
            aria-hidden={detailsPanelState !== 'open'}
          >
            <ChannelDetailsDrawer
              chat={chat}
              currentUserId={currentUserId ?? 0}
              onClose={closeDetailsPanel}
              onChatUpdated={handleChatUpdated}
              onLeft={(chatId) => {
                // Drop the channel from the store (clears currentChatId if active)
                // and return to the list view.
                useChatStore.getState().removeChat(chatId);
                closeDetailsPanel();
                onBack();
              }}
              lastScheduledMsg={lastScheduledMsg}
              pinRefreshKey={pinRefreshKey}
              onPinRemoved={(messageId) => {
                applyPins(pins.filter((pin) => pin.message.id !== messageId));
                setPinRefreshKey((prev) => prev + 1);
              }}
              onJumpToMessage={handleJumpToPinnedMessage}
            />
          </div>
        )}
      </div>

      <ForwardMessagesDialog
        isOpen={isForwardDialogOpen}
        onClose={() => setIsForwardDialogOpen(false)}
        projectId={chatProjectId ? String(chatProjectId) : ''}
        availableChats={availableTargetChats}
        currentUserId={currentUserId ?? 0}
        selectedMessageCount={selectedMessageIds.length}
        isForwarding={isForwarding}
        onSubmit={handleForwardSubmit}
      />


      <ReminderPickerSheet
        open={reminderMessageId !== null}
        onOpenChange={(open) => { if (!open) setReminderMessageId(null); }}
        onConfirm={handleReminderConfirm}
      />
    </div>
  );
}
