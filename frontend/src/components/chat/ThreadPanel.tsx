'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { X, MessageSquare, Loader2 } from 'lucide-react';
import MessageFlagChips from './MessageFlagChips';
import { format } from 'date-fns';
import type { ChatParticipant, Message } from '@/types/chat';
import { useThreadData } from '@/hooks/useThreadData';
import { useChatStore } from '@/lib/chatStore';
import { editMessage, deleteMessage, addReaction, removeReaction } from '@/lib/api/chatApi';
import ChatComposer from './ChatComposer';
import type { RichSendData } from './ChatComposer';
import MessageItem from './MessageItem';
import MessageHoverActions from './MessageHoverActions';
import ChatRichTextRenderer from './ChatRichTextRenderer';

const JUMP_HIGHLIGHT_CLEAR_MS = 8300;

// ── Avatar helper ─────────────────────────────────────────────────────────────

const AVATAR_COLORS = [
  'bg-blue-500', 'bg-emerald-500', 'bg-violet-500', 'bg-orange-500',
  'bg-pink-500', 'bg-teal-500', 'bg-red-500', 'bg-indigo-500',
];

function getAvatarColor(userId: number) {
  return AVATAR_COLORS[userId % AVATAR_COLORS.length];
}

function Avatar({ user, size = 'md' }: { user: { id: number; username?: string; email?: string; avatar?: string | null }; size?: 'sm' | 'md' }) {
  const initials = (user.username || user.email || '?')[0].toUpperCase();
  const sizeClass = size === 'sm' ? 'h-6 w-6 text-[10px]' : 'h-8 w-8 text-xs';
  if (user.avatar) {
    return <img src={user.avatar} alt={initials} className={`${sizeClass} rounded-full object-cover`} />;
  }
  return (
    <div className={`${sizeClass} flex shrink-0 items-center justify-center rounded-full font-semibold text-white ${getAvatarColor(user.id)}`}>
      {initials}
    </div>
  );
}

// ── Root message summary — original look + hover toolbar ─────────────────────

interface RootMessageSummaryProps {
  message: Message;
  isOwnMessage: boolean;
  onEdit: (messageId: number, content: string) => void;
  onDelete: (messageId: number) => void;
  onReactionAdd: (emoji: string) => void;
  onReactionRemove: (emoji: string) => void;
  onForward?: () => void;
  onPin?: (messageId: number) => void;
  onSave?: (messageId: number) => void;
  isPinned?: boolean;
  isSaved?: boolean;
}

function RootMessageSummary({
  message,
  isOwnMessage,
  onEdit,
  onDelete,
  onReactionAdd,
  onReactionRemove,
  onForward,
  onPin,
  onSave,
  isPinned = false,
  isSaved = false,
}: RootMessageSummaryProps) {
  const [isHovering, setIsHovering] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [editContent, setEditContent] = useState(message.content || '');
  const leaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const menuOpenRef = useRef(false);

  const handleMouseEnter = () => {
    if (leaveTimerRef.current) clearTimeout(leaveTimerRef.current);
    setIsHovering(true);
  };
  const handleMouseLeave = () => {
    leaveTimerRef.current = setTimeout(() => {
      if (!menuOpenRef.current) setIsHovering(false);
    }, 200);
  };

  const handleCopyText = () => {
    const text = message.content || '';
    if (text) navigator.clipboard.writeText(text).catch(() => {});
  };

  const hasContent = !!message.content || !!message.rich_body;

  return (
    <div className="relative border-b border-gray-100 px-4 py-3">
      {/* Pinned/saved accent bar — mirror MessageItem so it's obvious here too */}
      {(isPinned || isSaved) && (
        <div className="pointer-events-none absolute inset-y-0 left-0 flex w-0.5 flex-col">
          <div className={`flex-1 ${isPinned ? 'bg-teal-400' : 'bg-amber-400'}`} />
          {isPinned && isSaved && <div className="flex-1 bg-amber-400" />}
        </div>
      )}
      <div
        className="relative flex gap-3"
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
      >
        <Avatar user={message.sender} size="md" />
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className="text-sm font-semibold text-gray-900">
              {message.sender.username || message.sender.email}
            </span>
            <span className="text-[11px] text-gray-400">
              {format(new Date(message.created_at), 'MMM d, h:mm a')}
            </span>
          </div>

          {isEditing ? (
            <div className="mt-1">
              <textarea
                value={editContent}
                onChange={(e) => setEditContent(e.target.value)}
                className="w-full rounded border border-gray-300 px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-teal-400"
                rows={3}
                autoFocus
              />
              <div className="mt-1 flex gap-2">
                <button
                  type="button"
                  onClick={() => { onEdit(message.id, editContent); setIsEditing(false); }}
                  className="rounded bg-[#3CCED7] px-2 py-0.5 text-xs text-white hover:bg-[#33b8c0]"
                >
                  Save
                </button>
                <button
                  type="button"
                  onClick={() => { setEditContent(message.content || ''); setIsEditing(false); }}
                  className="rounded px-2 py-0.5 text-xs text-gray-500 hover:bg-gray-100"
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : hasContent && (
            message.rich_body ? (
              <div className="mt-0.5 text-sm text-gray-800 [overflow-wrap:anywhere]">
                <ChatRichTextRenderer body={message.rich_body} />
              </div>
            ) : (
              <p className="mt-0.5 whitespace-pre-wrap text-sm text-gray-800 [overflow-wrap:anywhere]">
                {message.content}
              </p>
            )
          )}

          <MessageFlagChips
            isPinned={isPinned}
            isSaved={isSaved}
            onUnpin={onPin ? () => onPin(message.id) : undefined}
          />
        </div>

        {isHovering && !isEditing && (
          <MessageHoverActions
            isOwnMessage={isOwnMessage}
            onEmojiReaction={(emoji) => {
              const existing = message.reactions?.find(r => r.emoji === emoji);
              if (existing?.reacted_by_me) {
                onReactionRemove(emoji);
              } else {
                onReactionAdd(emoji);
              }
            }}
            onEdit={isOwnMessage ? () => setIsEditing(true) : undefined}
            onDelete={isOwnMessage ? () => onDelete(message.id) : undefined}
            onForward={() => onForward?.()}
            onCopy={handleCopyText}
            onCopyLink={() => {}}
            onPin={() => onPin?.(message.id)}
            onSave={() => onSave?.(message.id)}
            onRemind={() => {}}
            onMultiSelect={() => {}}
            isPinned={isPinned}
            onMenuOpenChange={(open) => {
              menuOpenRef.current = open;
              if (open) {
                if (leaveTimerRef.current) clearTimeout(leaveTimerRef.current);
                setIsHovering(true);
              } else {
                leaveTimerRef.current = setTimeout(() => setIsHovering(false), 200);
              }
            }}
          />
        )}
      </div>
    </div>
  );
}

// ── Main ThreadPanel ──────────────────────────────────────────────────────────

interface ThreadPanelProps {
  rootMessage: Message;
  participants?: ChatParticipant[];
  currentUserId?: number;
  onClose: () => void;
  onForwardMessage?: (messageId: number) => void;
  onPinMessage?: (messageId: number) => void;
  onSaveMessage?: (messageId: number) => void;
  pinnedMessageIds?: Set<number>;
  savedMessageIds?: Set<number>;
  /** When set, highlight + scroll to that reply (root message id matches → highlight root). */
  highlightMessageId?: number | null;
  /** Called after the highlight fades so the parent can clear its state. */
  onHighlightCleared?: () => void;
}

export default function ThreadPanel({
  rootMessage,
  participants = [],
  currentUserId,
  onClose,
  onForwardMessage,
  onPinMessage,
  onSaveMessage,
  pinnedMessageIds,
  savedMessageIds,
  highlightMessageId = null,
  onHighlightCleared,
}: ThreadPanelProps) {
  const { isLoading, isSending, sendReply } = useThreadData(rootMessage);
  const repliesEndRef = useRef<HTMLDivElement>(null);
  const updateThreadReply = useChatStore((s) => s.updateThreadReply);
  const updateMessage = useChatStore((s) => s.updateMessage);
  const applyReactionSnapshot = useChatStore((s) => s.applyReactionSnapshot);

  // Subscribe directly to threadReplies so this component re-renders on every edit/reaction.
  const threadReplies = useChatStore((s) => s.threadReplies);
  const rootId = rootMessage?.id ?? null;
  const replies = rootId != null ? (threadReplies[rootId] ?? []) : [];

  // Auto-scroll to bottom when new replies arrive — but skip when the parent
  // asked us to highlight a specific reply (jumped here from pinned/saved).
  useEffect(() => {
    if (highlightMessageId) return;
    repliesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [replies.length, highlightMessageId]);

  // When a highlight target is set and replies are loaded, scroll the target
  // into view and retain the class through the animation's transparent frame.
  useEffect(() => {
    if (!highlightMessageId) return;
    if (isLoading) return;
    if (!replies.some((r) => r.id === highlightMessageId)) return;
    const frame = window.requestAnimationFrame(() => {
      const el = document.getElementById(`message-${highlightMessageId}`);
      el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
    const timer = window.setTimeout(() => {
      onHighlightCleared?.();
    }, JUMP_HIGHLIGHT_CLEAR_MS);
    return () => {
      window.cancelAnimationFrame(frame);
      window.clearTimeout(timer);
    };
  }, [highlightMessageId, isLoading, replies, onHighlightCleared]);

  const handleSendRich = async (data: RichSendData) => {
    await sendReply({
      content: data.content,
      richBody: data.rich_body,
      mentionIds: data.mention_ids,
      attachmentIds: data.attachment_ids,
    });
  };

  // ── Reply handlers (threadReplies store) ─────────────────────────────────

  const handleEditReply = useCallback(async (messageId: number, newContent: string) => {
    updateThreadReply(messageId, { content: newContent, rich_body: null, is_edited: true });
    try {
      const updated = await editMessage(messageId, newContent, null, []);
      updateThreadReply(messageId, {
        content: updated.content,
        rich_body: updated.rich_body ?? null,
        is_edited: updated.is_edited ?? true,
      });
    } catch {
      // silently fail — optimistic state stays
    }
  }, [updateThreadReply]);

  const handleDeleteReply = useCallback(async (messageId: number) => {
    try {
      const response = await deleteMessage(messageId);
      updateThreadReply(messageId, response.message);
    } catch {
      // silently fail
    }
  }, [updateThreadReply]);

  const handleReactionAdd = useCallback(async (messageId: number, emoji: string) => {
    try {
      const result = await addReaction(messageId, emoji);
      applyReactionSnapshot(messageId, result.message.reactions ?? [], result.reactions_version, currentUserId ?? null);
    } catch {
      // silently fail
    }
  }, [applyReactionSnapshot, currentUserId]);

  const handleReactionRemove = useCallback(async (messageId: number, emoji: string) => {
    try {
      const result = await removeReaction(messageId, emoji);
      applyReactionSnapshot(messageId, result.message.reactions ?? [], result.reactions_version, currentUserId ?? null);
    } catch {
      // silently fail
    }
  }, [applyReactionSnapshot, currentUserId]);

  // ── Root message handlers (main messages store) ──────────────────────────

  const handleEditRoot = useCallback(async (messageId: number, newContent: string) => {
    updateMessage(messageId, { content: newContent, rich_body: null, is_edited: true });
    try {
      const updated = await editMessage(messageId, newContent, null, []);
      updateMessage(messageId, {
        content: updated.content,
        rich_body: updated.rich_body ?? null,
        is_edited: updated.is_edited ?? true,
      });
    } catch {
      // silently fail
    }
  }, [updateMessage]);

  const handleDeleteRoot = useCallback(async (messageId: number) => {
    try {
      const response = await deleteMessage(messageId);
      updateMessage(messageId, response.message);
    } catch {
      // silently fail
    }
  }, [updateMessage]);

  const handleReactionAddRoot = useCallback(async (emoji: string) => {
    try {
      const result = await addReaction(rootMessage.id, emoji);
      applyReactionSnapshot(rootMessage.id, result.message.reactions ?? [], result.reactions_version, currentUserId ?? null);
    } catch {
      // silently fail
    }
  }, [rootMessage.id, applyReactionSnapshot, currentUserId]);

  const handleReactionRemoveRoot = useCallback(async (emoji: string) => {
    try {
      const result = await removeReaction(rootMessage.id, emoji);
      applyReactionSnapshot(rootMessage.id, result.message.reactions ?? [], result.reactions_version, currentUserId ?? null);
    } catch {
      // silently fail
    }
  }, [rootMessage.id, applyReactionSnapshot, currentUserId]);

  return (
    <div
      className="flex h-full w-full flex-col border-l border-gray-200 bg-white"
      data-testid="thread-panel"
    >
      {/* Header */}
      <div className="relative z-10 flex shrink-0 items-center justify-between border-b border-gray-100 px-4 py-3 shadow-[0_1px_3px_rgba(16,24,40,0.05)]">
        <div className="flex items-center gap-2">
          <MessageSquare className="h-4 w-4 text-gray-500" />
          <span className="text-sm font-semibold text-gray-800">Thread</span>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg p-1.5 text-gray-400 transition hover:bg-gray-100 hover:text-gray-600"
          aria-label="Close thread"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Root message */}
      <RootMessageSummary
        message={rootMessage}
        isOwnMessage={rootMessage.sender.id === currentUserId}
        onEdit={handleEditRoot}
        onDelete={handleDeleteRoot}
        onReactionAdd={handleReactionAddRoot}
        onReactionRemove={handleReactionRemoveRoot}
        onForward={onForwardMessage ? () => onForwardMessage(rootMessage.id) : undefined}
        onPin={onPinMessage}
        onSave={onSaveMessage}
        isPinned={pinnedMessageIds?.has(rootMessage.id)}
        isSaved={savedMessageIds?.has(rootMessage.id)}
      />

      {/* Reply count header */}
      {(replies.length > 0 || isLoading) && (
        <div className="shrink-0 border-b border-gray-100 px-4 py-2">
          <span className="text-xs font-semibold uppercase tracking-wide text-gray-500">
            {isLoading ? 'Loading replies…' : `${replies.length} ${replies.length === 1 ? 'reply' : 'replies'}`}
          </span>
        </div>
      )}

      {/* Replies list */}
      <div className="task-tab-scrollbar flex-1 overflow-y-auto">
        {isLoading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-5 w-5 animate-spin text-gray-300" />
          </div>
        ) : replies.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 px-6 py-12 text-center text-gray-400">
            <MessageSquare className="h-8 w-8 opacity-30" />
            <p className="text-sm">No replies yet. Start the thread!</p>
          </div>
        ) : (
          <div className="py-2">
            {replies.map((reply) => (
              <MessageItem
                key={reply.id}
                message={reply}
                isOwnMessage={reply.sender.id === currentUserId}
                showSender
                isThreadActive={false}
                isHighlighted={reply.id === highlightMessageId}
                onEdit={handleEditReply}
                onDelete={handleDeleteReply}
                onReactionAdd={(emoji) => handleReactionAdd(reply.id, emoji)}
                onReactionRemove={(emoji) => handleReactionRemove(reply.id, emoji)}
                onForwardSingle={onForwardMessage ? () => onForwardMessage(reply.id) : undefined}
                onPin={onPinMessage}
                onSave={onSaveMessage}
                isPinned={pinnedMessageIds?.has(reply.id)}
                isSaved={savedMessageIds?.has(reply.id)}
              />
            ))}
          </div>
        )}
        <div ref={repliesEndRef} />
      </div>

      {/* Composer */}
      <div className="shrink-0 border-t border-gray-100 px-3 py-3">
        <ChatComposer
          onSendRich={handleSendRich}
          participants={participants}
          placeholder="Reply in thread…"
          disabled={isSending}
          compact
        />
      </div>
    </div>
  );
}
