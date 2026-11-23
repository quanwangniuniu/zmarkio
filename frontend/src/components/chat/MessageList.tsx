'use client';

import { useEffect, useLayoutEffect, useRef, useState, useMemo, useCallback } from 'react';
import { format, isSameDay } from 'date-fns';
import { ChevronDown, MessagesSquare } from 'lucide-react';
import { useVirtualizer } from '@tanstack/react-virtual';
import type { Message, MessageListProps } from '@/types/chat';
import { Skeleton } from '@/components/ui/skeleton';
import EmptyState from '@/components/state-feedback/EmptyState';
import MessageItem from './MessageItem';

// ─── Loading skeleton ──────────────────────────────────────────────────────────

const LOADING_GROUPS = [
  {
    align: 'left',
    lines: [
      ['w-28', 'w-24', 'w-20', 'w-32'],
      ['w-32', 'w-28', 'w-24'],
    ],
  },
  {
    align: 'right',
    lines: [
      ['w-20', 'w-36', 'w-24', 'w-16', 'w-28'],
      ['w-24', 'w-32', 'w-20'],
    ],
    media: true,
  },
  {
    align: 'left',
    lines: [
      ['w-24', 'w-20', 'w-28', 'w-16', 'w-24'],
      ['w-36', 'w-24', 'w-20', 'w-28'],
    ],
  },
  {
    align: 'right',
    lines: [
      ['w-16', 'w-24', 'w-20', 'w-32'],
      ['w-28', 'w-20', 'w-24'],
    ],
  },
];

function LoadingBrickRow({ widths, align = 'left' }: { widths: string[]; align?: 'left' | 'right' }) {
  return (
    <div className={`flex flex-wrap gap-2 ${align === 'right' ? 'justify-end' : ''}`}>
      {widths.map((width, index) => (
        <Skeleton key={`${width}-${index}`} className={`h-7 rounded-xl ${width}`} />
      ))}
    </div>
  );
}

function MessageListLoadingSkeleton({ compact = false }: { compact?: boolean }) {
  const groups = compact ? LOADING_GROUPS.slice(0, 1) : LOADING_GROUPS;
  return (
    <div className={compact ? 'space-y-3 pb-3' : 'task-tab-scrollbar flex-1 overflow-y-auto p-4 space-y-6'}>
      {groups.map((group, groupIndex) => (
        <div key={`message-loading-group-${groupIndex}`} className="space-y-3">
          {group.lines.map((line, lineIndex) => (
            <LoadingBrickRow
              key={`message-loading-line-${groupIndex}-${lineIndex}`}
              widths={line}
              align={group.align === 'left' || group.align === 'right' ? group.align : undefined}
            />
          ))}
          {group.media ? (
            <div className={`pt-1 ${group.align === 'right' ? 'flex justify-end' : ''}`}>
              <Skeleton className="h-40 w-40 rounded-2xl" />
            </div>
          ) : null}
        </div>
      ))}
    </div>
  );
}

// ─── Flat item types ───────────────────────────────────────────────────────────

type FlatItem =
  | { type: 'date-header'; date: string }
  | { type: 'unread-divider' }
  | { type: 'message'; message: Message; showSender: boolean; senderRole?: string };

type PrependAnchor = {
  messageId: number;
  offsetTop: number;
};

const JUMP_SCROLL_MIN_DURATION_MS = 1100;
const JUMP_SCROLL_MAX_DURATION_MS = 1500;
const JUMP_HIGHLIGHT_CLEAR_MS = 8300;

function formatDateHeader(dateStr: string): string {
  const date = new Date(dateStr + 'T00:00:00');
  const today = new Date();
  if (isSameDay(date, today)) return 'Today';
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  if (isSameDay(date, yesterday)) return 'Yesterday';
  return format(date, 'MMMM d, yyyy');
}

function flatItemKey(item: FlatItem, index: number): string {
  if (item.type === 'message') return `message-${item.message.id}`;
  if (item.type === 'date-header') return `date-${item.date}`;
  return `unread-${index}`;
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function MessageList({
  messages,
  chatSlug,
  currentUserId,
  onLoadMore,
  hasMore,
  isLoading,
  isLoadingMoreMessages = false,
  showSwitchLoadingSkeleton = false,
  roleByUserId,
  isGroupChat = false,
  isSelectMode = false,
  selectedMessageIds = [],
  onToggleSelectMessage,
  firstUnreadMessageId = null,
  onEditMessage,
  onDeleteMessage,
  onReactionAdd,
  onReactionRemove,
  onQuoteReply,
  onForwardSingle,
  onEnterSelectMode,
  onOpenThread,
  activeThreadMessageId,
  jumpTarget,
  onPinMessage,
  onSaveMessage,
  onRemindMessage,
  pinnedMessageIds,
  savedMessageIds,
}: MessageListProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const [isAtBottom, setIsAtBottom] = useState(true);
  const [highlightMessageId, setHighlightMessageId] = useState<number | null>(null);

  // Refs that survive re-renders without triggering effects
  const isLoadingMoreRef = useRef(false);
  const prependAnchorRef = useRef<PrependAnchor | null>(null);
  const restoreFrameRef = useRef<number | null>(null);
  const loadMoreFrameRef = useRef<number | null>(null);
  const jumpFrameRef = useRef<number | null>(null);
  const jumpObserverRef = useRef<ResizeObserver | null>(null);
  const jumpStopTimeoutRef = useRef<number | null>(null);
  const activeJumpRequestRef = useRef<string | null>(null);
  const completedJumpRequestRef = useRef<string | null>(null);
  const isRestoringPrependRef = useRef(false);
  const lastMessageIdRef = useRef<number | null>(null);
  const bottomSettleFrameRef = useRef<number | null>(null);
  const bottomSettleRemainingRef = useRef(0);
  const previousCountRef = useRef(messages.length);
  // stickyBottomRef: when true, size/content changes scroll to the real bottom.
  // This handles initial scroll, chat switches, and composer height changes.
  const stickyBottomRef = useRef(true);

  // ── Group messages by date ───────────────────────────────────────────────────
  const messageGroups = useMemo(() => {
    const groups: { date: string; messages: Message[] }[] = [];
    messages.forEach((message) => {
      const dateStr = format(new Date(message.created_at), 'yyyy-MM-dd');
      const existing = groups.find((g) => g.date === dateStr);
      if (existing) existing.messages.push(message);
      else groups.push({ date: dateStr, messages: [message] });
    });
    return groups;
  }, [messages]);

  // ── Flatten into a single list ──────────────────────────────────────────────
  const flatItems = useMemo<FlatItem[]>(() => {
    const items: FlatItem[] = [];
    messageGroups.forEach((group) => {
      items.push({ type: 'date-header', date: group.date });
      group.messages.forEach((message, index) => {
        const prevMessage = index > 0 ? group.messages[index - 1] : null;
        const showSender = !prevMessage || prevMessage.sender.id !== message.sender.id;
        const senderRole = isGroupChat ? roleByUserId?.[message.sender.id] : undefined;
        if (firstUnreadMessageId === message.id) {
          items.push({ type: 'unread-divider' });
        }
        items.push({ type: 'message', message, showSender, senderRole });
      });
    });
    return items;
  }, [messageGroups, firstUnreadMessageId, isGroupChat, roleByUserId]);

  const rowVirtualizer = useVirtualizer({
    count: flatItems.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: (index) => {
      const item = flatItems[index];
      if (!item) return 80;
      if (item.type === 'date-header') return 56;
      if (item.type === 'unread-divider') return 38;
      return item.showSender ? 116 : 64;
    },
    getItemKey: (index) => {
      const item = flatItems[index];
      return item ? flatItemKey(item, index) : `missing-${index}`;
    },
    overscan: 24,
  });

  // ── Scroll helpers ───────────────────────────────────────────────────────────
  const scrollToBottom = useCallback((behavior: ScrollBehavior = 'instant') => {
    if (!scrollRef.current) return;
    stickyBottomRef.current = true;
    scrollRef.current.scrollTo({ top: scrollRef.current.scrollHeight, behavior });
  }, []);

  const cancelBottomSettle = useCallback(() => {
    if (bottomSettleFrameRef.current !== null) {
      window.cancelAnimationFrame(bottomSettleFrameRef.current);
      bottomSettleFrameRef.current = null;
    }
    bottomSettleRemainingRef.current = 0;
  }, []);

  const scheduleBottomSettle = useCallback((frames = 18) => {
    if (activeJumpRequestRef.current) return;
    if (isLoadingMoreRef.current || isRestoringPrependRef.current) return;
    if (!stickyBottomRef.current) return;

    bottomSettleRemainingRef.current = Math.max(bottomSettleRemainingRef.current, frames);
    if (bottomSettleFrameRef.current !== null) return;

    const settle = () => {
      bottomSettleFrameRef.current = null;
      if (!stickyBottomRef.current || activeJumpRequestRef.current) {
        bottomSettleRemainingRef.current = 0;
        return;
      }
      if (isLoadingMoreRef.current || isRestoringPrependRef.current) {
        bottomSettleRemainingRef.current = 0;
        return;
      }

      const container = scrollRef.current;
      if (!container) {
        bottomSettleRemainingRef.current -= 1;
        if (bottomSettleRemainingRef.current > 0) {
          bottomSettleFrameRef.current = window.requestAnimationFrame(settle);
        }
        return;
      }

      container.scrollTo({ top: container.scrollHeight, behavior: 'instant' });
      bottomSettleRemainingRef.current -= 1;

      if (bottomSettleRemainingRef.current > 0) {
        bottomSettleFrameRef.current = window.requestAnimationFrame(settle);
      }
    };

    bottomSettleFrameRef.current = window.requestAnimationFrame(settle);
  }, []);

  const restorePrependAnchor = useCallback((): void => {
    const anchor = prependAnchorRef.current;
    const container = scrollRef.current;
    if (!anchor || !container) return;

    const anchorEl = container.querySelector<HTMLElement>(
      `[data-message-id="${anchor.messageId}"]`,
    );
    if (!anchorEl) {
      const anchorIndex = flatItems.findIndex(
        (item) => item.type === 'message' && item.message.id === anchor.messageId,
      );
      if (anchorIndex >= 0) {
        rowVirtualizer.scrollToIndex(anchorIndex, { align: 'start' });
      }
      return;
    }

    const currentOffset =
      anchorEl.getBoundingClientRect().top - container.getBoundingClientRect().top;
    const delta = currentOffset - anchor.offsetTop;

    if (Math.abs(delta) > 0.5) {
      container.scrollTop += delta;
    }
  }, [flatItems, rowVirtualizer]);

  const cancelPendingJumpSettle = useCallback(() => {
    if (jumpFrameRef.current !== null) {
      window.cancelAnimationFrame(jumpFrameRef.current);
      jumpFrameRef.current = null;
    }
    if (jumpObserverRef.current) {
      jumpObserverRef.current.disconnect();
      jumpObserverRef.current = null;
    }
    if (jumpStopTimeoutRef.current !== null) {
      window.clearTimeout(jumpStopTimeoutRef.current);
      jumpStopTimeoutRef.current = null;
    }
  }, []);

  const getJumpTargetPosition = useCallback((
    messageId: number,
    attachmentId?: number,
  ): { element: HTMLElement; top: number } | null => {
    const container = scrollRef.current;
    const attachmentEl = attachmentId
      ? container?.querySelector<HTMLElement>(`[data-attachment-id="${attachmentId}"]`)
      : null;
    const messageEl = container?.querySelector<HTMLElement>(
      `[data-message-id="${messageId}"]`,
    );
    const targetEl = attachmentEl ?? messageEl;
    if (!container || !targetEl) return null;

    const containerRect = container.getBoundingClientRect();
    const messageRect = targetEl.getBoundingClientRect();
    const nextTop =
      container.scrollTop +
      messageRect.top -
      containerRect.top -
      container.clientHeight / 2 +
      messageRect.height / 2;

    const maxTop = Math.max(0, container.scrollHeight - container.clientHeight);
    return {
      element: targetEl,
      top: Math.max(0, Math.min(nextTop, maxTop)),
    };
  }, []);

  const scrollJumpTargetToCenter = useCallback((messageId: number, attachmentId?: number): HTMLElement | null => {
    const container = scrollRef.current;
    const target = getJumpTargetPosition(messageId, attachmentId);
    if (!container || !target) return null;

    stickyBottomRef.current = false;
    container.scrollTop = target.top;
    return target.element;
  }, [getJumpTargetPosition]);

  const animateJumpScroll = useCallback((
    getTargetTop: () => number,
    isActiveJump: () => boolean,
    onComplete: () => void,
  ) => {
    const container = scrollRef.current;
    if (!container) return;

    stickyBottomRef.current = false;
    const startTop = container.scrollTop;
    const initialTargetTop = getTargetTop();
    const initialDistance = Math.abs(initialTargetTop - startTop);
    const duration = Math.min(
      JUMP_SCROLL_MAX_DURATION_MS,
      Math.max(JUMP_SCROLL_MIN_DURATION_MS, initialDistance * 0.3),
    );

    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      container.scrollTop = initialTargetTop;
      onComplete();
      return;
    }

    const startedAt = window.performance.now();
    const step = (now: number) => {
      if (!isActiveJump()) return;

      const progress = Math.min(1, (now - startedAt) / duration);
      const eased = progress < 0.5
        ? 4 * progress * progress * progress
        : 1 - Math.pow(-2 * progress + 2, 3) / 2;
      const maxTop = Math.max(0, container.scrollHeight - container.clientHeight);
      const latestTargetTop = Math.max(0, Math.min(getTargetTop(), maxTop));
      container.scrollTop = startTop + (latestTargetTop - startTop) * eased;

      if (progress < 1) {
        jumpFrameRef.current = window.requestAnimationFrame(step);
        return;
      }

      container.scrollTop = latestTargetTop;
      jumpFrameRef.current = null;
      onComplete();
    };

    jumpFrameRef.current = window.requestAnimationFrame(step);
  }, []);

  const startJumpToTarget = useCallback((target: {
    messageId: number;
    attachmentId?: number;
    requestId: string;
  }) => {
    if (!target.messageId || !Number.isFinite(target.messageId)) return;
    if (completedJumpRequestRef.current === target.requestId) return;

    const targetIndex = flatItems.findIndex(
      (item) => item.type === 'message' && item.message.id === target.messageId,
    );
    const container = scrollRef.current;
    if (!container || targetIndex < 0) return;

    cancelPendingJumpSettle();
    activeJumpRequestRef.current = target.requestId;
    const isActiveJump = () => activeJumpRequestRef.current === target.requestId;

    const startSettle = (targetEl: HTMLElement) => {
      let attempts = 0;
      const settle = () => {
        if (!isActiveJump()) return;
        scrollJumpTargetToCenter(target.messageId, target.attachmentId);
        attempts += 1;
        if (attempts < 12) {
          jumpFrameRef.current = window.requestAnimationFrame(settle);
          return;
        }
        jumpFrameRef.current = null;
      };
      jumpFrameRef.current = window.requestAnimationFrame(settle);

      if (typeof ResizeObserver !== 'undefined') {
        const observer = new ResizeObserver(() => {
          if (!isActiveJump()) return;
          scrollJumpTargetToCenter(target.messageId, target.attachmentId);
        });
        observer.observe(targetEl);
        jumpObserverRef.current = observer;
      }

      jumpStopTimeoutRef.current = window.setTimeout(() => {
        if (!isActiveJump()) return;
        completedJumpRequestRef.current = target.requestId;
        activeJumpRequestRef.current = null;
        cancelPendingJumpSettle();
      }, 1200);

      setHighlightMessageId(target.messageId);
    };

    let findAttempts = 0;
    let usedFallback = false;
    const findAndSettle = () => {
      if (!isActiveJump()) return;
      const exactTarget = getJumpTargetPosition(target.messageId, target.attachmentId);
      if (exactTarget) {
        startSettle(exactTarget.element);
        return;
      }
      findAttempts += 1;
      if (findAttempts < 30) {
        jumpFrameRef.current = window.requestAnimationFrame(findAndSettle);
        return;
      }
      // Measurement estimates are normally close enough to render the target.
      // Keep one defensive fallback for unusually tall, late-loading rows.
      if (!usedFallback) {
        usedFallback = true;
        findAttempts = 0;
        rowVirtualizer.scrollToIndex(targetIndex, { align: 'center' });
        jumpFrameRef.current = window.requestAnimationFrame(findAndSettle);
        return;
      }
      activeJumpRequestRef.current = null;
      jumpFrameRef.current = null;
    };

    const getAnimatedTargetTop = () => {
      const exactTarget = getJumpTargetPosition(target.messageId, target.attachmentId);
      if (exactTarget) return exactTarget.top;
      return rowVirtualizer.getOffsetForIndex(targetIndex, 'center')?.[0] ?? container.scrollTop;
    };

    animateJumpScroll(getAnimatedTargetTop, isActiveJump, findAndSettle);
  }, [animateJumpScroll, cancelPendingJumpSettle, flatItems, getJumpTargetPosition, rowVirtualizer, scrollJumpTargetToCenter]);

  const schedulePrependAnchorRestore = useCallback((framesRemaining = 6): void => {
    if (restoreFrameRef.current !== null) {
      window.cancelAnimationFrame(restoreFrameRef.current);
    }

    isRestoringPrependRef.current = true;
    restoreFrameRef.current = window.requestAnimationFrame(() => {
      restorePrependAnchor();

      if (framesRemaining > 1) {
        schedulePrependAnchorRestore(framesRemaining - 1);
        return;
      }

      restoreFrameRef.current = null;
      prependAnchorRef.current = null;
      isRestoringPrependRef.current = false;
    });
  }, [restorePrependAnchor]);

  // ── Jump-to-message event (cross-component) ──────────────────────────────────
  useEffect(() => {
    const handler = (e: Event) => {
      const ce = e as CustomEvent<{ messageId?: number; attachmentId?: number; requestId?: string }>;
      const messageId = ce.detail?.messageId;
      const attachmentId = ce.detail?.attachmentId;
      if (!messageId || !Number.isFinite(messageId)) return;
      const requestId = ce.detail?.requestId ?? `${messageId}:${attachmentId ?? 'message'}:${Date.now()}`;
      startJumpToTarget({ messageId, attachmentId, requestId });
    };
    window.addEventListener('mj:chat:jumpToMessage', handler as EventListener);
    return () => window.removeEventListener('mj:chat:jumpToMessage', handler as EventListener);
  }, [startJumpToTarget]);

  useLayoutEffect(() => {
    if (!jumpTarget) return;
    startJumpToTarget(jumpTarget);
  }, [flatItems.length, jumpTarget, startJumpToTarget]);

  // Keep the React class alive slightly beyond the CSS animation so its final
  // transparent frame is painted instead of disappearing mid-fade.
  useEffect(() => {
    if (!highlightMessageId) return;
    const t = window.setTimeout(() => {
      setHighlightMessageId(null);
    }, JUMP_HIGHLIGHT_CLEAR_MS);
    return () => window.clearTimeout(t);
  }, [highlightMessageId]);

  useEffect(() => {
    return () => {
      activeJumpRequestRef.current = null;
      completedJumpRequestRef.current = null;
      if (restoreFrameRef.current !== null) {
        window.cancelAnimationFrame(restoreFrameRef.current);
      }
      if (loadMoreFrameRef.current !== null) {
        window.cancelAnimationFrame(loadMoreFrameRef.current);
      }
      cancelPendingJumpSettle();
      cancelBottomSettle();
    };
  }, [cancelBottomSettle, cancelPendingJumpSettle]);

  // Keep the newest messages anchored when the composer changes height
  // (for example, Slack-style Aa toolbar show/hide) or message content settles
  // after first paint.
  useLayoutEffect(() => {
    const container = scrollRef.current;
    const content = contentRef.current;
    if (!container || typeof ResizeObserver === 'undefined') return;

    let frame: number | null = null;
    const observer = new ResizeObserver(() => {
      const isSettlingBottom = bottomSettleRemainingRef.current > 0;
      if (!stickyBottomRef.current && !isAtBottom && !isSettlingBottom) return;
      if (isRestoringPrependRef.current || isLoadingMoreRef.current) return;
      if (frame !== null) window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        container.scrollTo({ top: container.scrollHeight, behavior: 'instant' });
      });
    });

    observer.observe(container);
    if (content) observer.observe(content);
    return () => {
      observer.disconnect();
      if (frame !== null) window.cancelAnimationFrame(frame);
    };
  }, [isAtBottom]);

  // ── Detect chat switch / initial load ───────────────────────────────────────
  useLayoutEffect(() => {
    if (messages.length === 0) return;
    const lastMessageId = messages[messages.length - 1]?.id;
    const isNewChat =
      lastMessageIdRef.current !== null &&
      lastMessageIdRef.current !== lastMessageId &&
      !isLoadingMoreRef.current;
    const isInitialLoad = lastMessageIdRef.current === null;

    if (isInitialLoad || isNewChat) {
      // Opening a conversation should land on the latest message. The unread
      // divider still renders in history, but it should not steal initial focus.
      stickyBottomRef.current = true;
      scrollToBottom('instant');
      scheduleBottomSettle(45);
    }
    lastMessageIdRef.current = lastMessageId;
  }, [messages, scheduleBottomSettle, scrollToBottom]);

  // ── Scroll to bottom on content changes ─────────────────────────────────────
  useLayoutEffect(() => {
    if (showSwitchLoadingSkeleton) return;
    if (flatItems.length === 0 || !scrollRef.current) return;

    if (stickyBottomRef.current) {
      scrollRef.current.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'instant' });
      scheduleBottomSettle(45);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [flatItems.length, showSwitchLoadingSkeleton]);

  // ── Restore scroll position after prepending older messages ──────────────────
  // Must be useLayoutEffect (not useEffect) so the position is corrected before
  // the browser paints — otherwise the user sees a jump as the DOM shifts first.
  useLayoutEffect(() => {
    if (!isLoadingMoreRef.current) return;
    const anchor = prependAnchorRef.current;
    isLoadingMoreRef.current = false;

    if (!anchor) return;

    const anchorStillExists = flatItems.some(
      (item) => item.type === 'message' && item.message.id === anchor.messageId,
    );
    if (!anchorStillExists) {
      prependAnchorRef.current = null;
      isRestoringPrependRef.current = false;
      return;
    }

    isRestoringPrependRef.current = true;
    schedulePrependAnchorRestore(10);
  }, [flatItems, schedulePrependAnchorRestore]);

  useEffect(() => {
    if (isLoadingMoreMessages || !isLoadingMoreRef.current) return;
    // If the backend says there are no older rows, flatItems will not change, so
    // the prepend restore effect above never gets a chance to clear this guard.
    isLoadingMoreRef.current = false;
    prependAnchorRef.current = null;
    isRestoringPrependRef.current = false;
  }, [isLoadingMoreMessages]);

  // ── Auto-scroll to bottom on new incoming messages ───────────────────────────
  useEffect(() => {
    const isNewMessage = messages.length > previousCountRef.current;
    previousCountRef.current = messages.length;
    if (isNewMessage && isAtBottom && !isLoadingMoreRef.current) {
      stickyBottomRef.current = true;
    }
  }, [messages.length, isAtBottom]);

  // ── Load more (older messages) ───────────────────────────────────────────────
  const capturePrependAnchor = useCallback((): PrependAnchor | null => {
    const container = scrollRef.current;
    if (!container) return null;

    const containerRect = container.getBoundingClientRect();
    const messageEls = Array.from(
      container.querySelectorAll<HTMLElement>('[data-message-id]'),
    );

    const visibleMessage = messageEls
      .map((el) => {
        const messageId = Number(el.dataset.messageId);
        if (!Number.isFinite(messageId)) return null;
        const rect = el.getBoundingClientRect();
        if (rect.bottom <= containerRect.top || rect.top >= containerRect.bottom) return null;
        return {
          messageId,
          offsetTop: rect.top - containerRect.top,
          distanceFromTop: Math.abs(rect.top - containerRect.top),
        };
      })
      .filter(
        (anchor): anchor is { messageId: number; offsetTop: number; distanceFromTop: number } =>
          anchor !== null,
      )
      .sort((a, b) => a.distanceFromTop - b.distanceFromTop)[0];

    if (visibleMessage) {
      return {
        messageId: visibleMessage.messageId,
        offsetTop: visibleMessage.offsetTop,
      };
    }

    const firstMessageEl = messageEls[0];
    const firstMessageId = firstMessageEl ? Number(firstMessageEl.dataset.messageId) : NaN;
    if (Number.isFinite(firstMessageId) && firstMessageEl) {
      return {
        messageId: firstMessageId,
        offsetTop: firstMessageEl.getBoundingClientRect().top - containerRect.top,
      };
    }

    return null;
  }, []);

  const handleLoadMore = useCallback(() => {
    if (isLoadingMoreRef.current) return;
    prependAnchorRef.current = capturePrependAnchor();

    if (!prependAnchorRef.current) return;

    isLoadingMoreRef.current = true;
    onLoadMore();
  }, [capturePrependAnchor, onLoadMore]);

  const scheduleLoadMore = useCallback(() => {
    if (loadMoreFrameRef.current !== null) return;
    loadMoreFrameRef.current = window.requestAnimationFrame(() => {
      loadMoreFrameRef.current = null;
      handleLoadMore();
    });
  }, [handleLoadMore]);

  // ── Scroll event ─────────────────────────────────────────────────────────────
  const handleScroll = useCallback(() => {
    if (!scrollRef.current) return;
    const { scrollHeight, scrollTop, clientHeight } = scrollRef.current;
    // When content is shorter than the viewport there's no scroll — treat as "at bottom"
    const distFromBottom = scrollHeight - scrollTop - clientHeight;
    const isBottom = scrollHeight <= clientHeight || distFromBottom < 50;
    setIsAtBottom(isBottom);
    if (
      !isBottom &&
      bottomSettleRemainingRef.current === 0 &&
      !activeJumpRequestRef.current &&
      !isRestoringPrependRef.current
    ) {
      stickyBottomRef.current = false;
    }

    // Only trigger load-more when the user has actually scrolled up (not at
    // bottom). This prevents firing on initial mount when content is short
    // enough that scrollTop stays 0 and the container isn't overflowing.
    if (
      !activeJumpRequestRef.current &&
      !isBottom &&
      scrollTop < 100 &&
      bottomSettleRemainingRef.current === 0 &&
      hasMore &&
      !isLoading &&
      !isLoadingMoreRef.current &&
      !isRestoringPrependRef.current
    ) {
      scheduleLoadMore();
    }
  }, [hasMore, isLoading, scheduleLoadMore]);

  const shouldShowFullSwitchSkeleton = showSwitchLoadingSkeleton && !isLoadingMoreMessages;

  return (
    <div className="h-full flex flex-col">
      {shouldShowFullSwitchSkeleton ? (
        <MessageListLoadingSkeleton />
      ) : (
        <>
          {/* Initial loading */}
          {messages.length === 0 && isLoading && !showSwitchLoadingSkeleton && (
            <MessageListLoadingSkeleton />
          )}

          {/* Empty state */}
          {messages.length === 0 && !isLoading && (
            <div className="flex flex-1 items-center justify-center">
              <EmptyState
                icon={<MessagesSquare className="h-5 w-5" />}
                title="No messages yet"
                description="Start the conversation by sending the first message."
              />
            </div>
          )}

          {/* Message list */}
          {messages.length > 0 && (
            <div className="relative flex-1 min-h-0">
              {isLoadingMoreMessages && hasMore && (
                <div className="pointer-events-none absolute left-0 right-0 top-2 z-10 flex justify-center">
                  <span className="rounded-full border border-gray-200 bg-white/95 px-3 py-1 text-[11px] font-medium text-gray-500 shadow-sm">
                    Loading older messages...
                  </span>
                </div>
              )}
              <div
                ref={scrollRef}
                onScroll={handleScroll}
                className="task-tab-scrollbar h-full overflow-y-auto flex flex-col"
              >
                <div
                  ref={contentRef}
                  className="relative pb-3"
                  style={{ height: `${rowVirtualizer.getTotalSize()}px` }}
                >
                  {rowVirtualizer.getVirtualItems().map((virtualRow) => {
                    const index = virtualRow.index;
                    const item = flatItems[index];
                    if (!item) return null;
                    return (
                      <div
                        key={virtualRow.key}
                        ref={rowVirtualizer.measureElement}
                        data-index={index}
                        data-message-id={item.type === 'message' ? item.message.id : undefined}
                        className="absolute left-0 top-0 w-full"
                        style={{ transform: `translateY(${virtualRow.start}px)` }}
                      >
                        {item.type === 'date-header' && (
                          <div className="flex justify-center py-4">
                            <span className="bg-gray-100 text-gray-600 text-xs font-medium px-3 py-1 rounded-full">
                              {formatDateHeader(item.date)}
                            </span>
                          </div>
                        )}

                        {item.type === 'unread-divider' && (
                          <div
                            id="new-messages-divider"
                            className="my-3 px-3 sm:px-4 flex items-center gap-3"
                            aria-label="New messages"
                          >
                            <div className="h-px flex-1 bg-[#3CCED7]/40" />
                            <span className="shrink-0 rounded-full bg-[#3CCED7]/10 px-2.5 py-0.5 text-[11px] font-medium text-[#3CCED7]">
                              New messages
                            </span>
                            <div className="h-px flex-1 bg-[#3CCED7]/40" />
                          </div>
                        )}

                        {item.type === 'message' && (
                          <MessageItem
                            message={item.message}
                            chatSlug={chatSlug}
                            isOwnMessage={item.message.sender.id === currentUserId}
                            showSender={item.showSender}
                            isCompact={!item.showSender}
                            senderRole={item.senderRole}
                            isSelectMode={isSelectMode}
                            isSelected={selectedMessageIds.includes(item.message.id)}
                            onToggleSelect={onToggleSelectMessage}
                            isHighlighted={highlightMessageId === item.message.id}
                            onEdit={onEditMessage}
                            onDelete={onDeleteMessage}
                            onReactionAdd={onReactionAdd ? (emoji) => onReactionAdd(item.message.id, emoji) : undefined}
                            onReactionRemove={onReactionRemove ? (emoji) => onReactionRemove(item.message.id, emoji) : undefined}
                            onQuoteReply={onQuoteReply ? () => onQuoteReply(item.message) : undefined}
                            onForwardSingle={onForwardSingle ? () => onForwardSingle(item.message.id) : undefined}
                            onEnterSelectMode={onEnterSelectMode}
                            onOpenThread={onOpenThread ? () => onOpenThread(item.message) : undefined}
                            isThreadActive={activeThreadMessageId === item.message.id}
                            onPin={onPinMessage}
                            onSave={onSaveMessage}
                            onRemind={onRemindMessage}
                            isPinned={pinnedMessageIds?.has(item.message.id)}
                            isSaved={savedMessageIds?.has(item.message.id)}
                          />
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* Scroll-to-bottom button */}
              {!isAtBottom && (
                <button
                  onClick={() => scrollToBottom('smooth')}
                  className="absolute bottom-4 right-4 z-10 flex h-8 w-8 items-center justify-center rounded-full bg-white shadow-md border border-gray-200 text-gray-500 hover:text-gray-800 hover:bg-gray-50 transition-all"
                  aria-label="Scroll to bottom"
                >
                  <ChevronDown className="h-4 w-4" />
                </button>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
