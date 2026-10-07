// Chat state management with Zustand
import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { ChatState, Chat, Message, OutboxEntry, OutboxAckCommit, PendingAttachment } from '@/types/chat';
import { getUnreadCount, sendMessage, getMessage } from './api/chatApi';

// Session-scoped set of message IDs the current user has deleted.
// Not persisted — only lives until page refresh, but that's enough to
// filter stale search results within the same browsing session.

function outboxOptimisticMessageId(clientMessageId: string): number {
  let hash = 0;
  for (let i = 0; i < clientMessageId.length; i += 1) {
    hash = ((hash << 5) - hash) + clientMessageId.charCodeAt(i);
    hash |= 0;
  }
  return hash > 0 ? -hash : hash;
}

function shouldMergeIncomingMessage(existing: Message, incoming: Message): boolean {
  const existingCount = existing.attachments?.length ?? 0;
  const incomingCount = incoming.attachments?.length ?? 0;
  return incomingCount > existingCount;
}

function messageSequence(message: Message): number | null {
  const seq = Number(message.seq);
  return Number.isSafeInteger(seq) && seq > 0 ? seq : null;
}

/** Keep committed messages in server sequence order under websocket jitter.
 * Sequence-less optimistic messages stay at the end until the server replaces
 * them with their committed payload.
 */
export function reorderMessagesBySequence(messages: Message[]): Message[] {
  let previousSeq = 0;
  let alreadyOrdered = true;

  for (const message of messages) {
    const seq = messageSequence(message);
    if (seq === null || seq <= previousSeq) {
      alreadyOrdered = false;
      break;
    }
    previousSeq = seq;
  }

  if (alreadyOrdered) return messages;

  const bySequence = new Map<number, Message>();
  const optimistic: Message[] = [];

  messages.forEach((message) => {
    const seq = messageSequence(message);
    if (seq === null) {
      optimistic.push(message);
      return;
    }
    const existing = bySequence.get(seq);
    bySequence.set(
      seq,
      existing && !shouldMergeIncomingMessage(existing, message)
        ? existing
        : { ...existing, ...message },
    );
  });

  return [
    ...[...bySequence.entries()].sort(([left], [right]) => left - right).map(([, message]) => message),
    ...optimistic.sort(
      (left, right) => new Date(left.created_at).getTime() - new Date(right.created_at).getTime(),
    ),
  ];
}

function isLaterMessage(candidate: Message, current: Message | null | undefined): boolean {
  if (!current) return true;
  const candidateSeq = messageSequence(candidate);
  const currentSeq = messageSequence(current);
  if (candidateSeq !== null && currentSeq !== null) return candidateSeq > currentSeq;
  return new Date(candidate.created_at).getTime() > new Date(current.created_at).getTime();
}

function stripOptimisticOutboxMessages(messages: Message[], clientMessageId: string): Message[] {
  const optimisticId = outboxOptimisticMessageId(clientMessageId);
  return messages.filter(
    (msg) => msg.client_message_id !== clientMessageId && msg.id !== optimisticId,
  );
}

export const deletedMessageIds = new Set<number>();

const revokeAttachmentPreview = (attachment: PendingAttachment | undefined) => {
  if (attachment?.preview) {
    URL.revokeObjectURL(attachment.preview);
  }
};

const revokeAttachmentPreviews = (attachments: PendingAttachment[] | undefined) => {
  attachments?.forEach(revokeAttachmentPreview);
};

const resolveChatProjectId = (chat: Chat): number | string | null => {
  const rawProjectId = chat.project_id ?? chat.project;
  if (!rawProjectId) return null;
  const parsed = Number(rawProjectId);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : rawProjectId;
};

const normalizeChatProject = (chat: Chat, fallbackProjectId?: number | string): Chat => {
  const projectId = resolveChatProjectId(chat) ?? fallbackProjectId;
  if (!projectId) {
    return chat;
  }
  return {
    ...chat,
    project_id: projectId,
    project: chat.project ?? projectId,
  };
};

// The single place that turns an observed user into a presence entry. Anything
// that ingests a user object (message sender, reply sender, chat creator/
// participant, reaction actor) funnels through here so presenceByUserId stays in
// sync. Users whose payload carries no is_online (e.g. ReactionUser today) are a
// no-op, but the path is ready if their payload ever grows the field.
const collectUserPresence = (
  user: { id?: number | string; is_online?: boolean } | null | undefined,
  target: Record<number, boolean>,
) => {
  if (!user || typeof user.is_online !== 'boolean') return;
  const userId = Number(user.id);
  if (Number.isFinite(userId) && !(userId in target)) target[userId] = user.is_online;
};

const collectMessagePresence = (message: Message | null | undefined, target: Record<number, boolean>) => {
  if (!message) return;
  collectUserPresence(message.sender, target);
  collectUserPresence(message.reply_to?.sender, target);
};

// Returns a new presence map seeded from the given messages. Every mutator that
// ingests Message objects should funnel through this so newly observed senders are
// always reflected in presenceByUserId — skipping it leaves their dots stale until
// the next presence_update WebSocket event.
const presenceFromMessages = (
  current: Record<number, boolean>,
  messages: (Message | null | undefined)[],
): Record<number, boolean> => {
  const next = { ...current };
  messages.forEach(message => collectMessagePresence(message, next));
  return next;
};

const collectChatPresence = (chat: Chat, target: Record<number, boolean>) => {
  collectUserPresence(chat.created_by, target);
  for (const participant of chat.participants ?? []) {
    collectUserPresence(participant.user, target);
  }
  collectMessagePresence(chat.last_message, target);
};

export const useChatStore = create<ChatState>()(
  persist(
    (set, get) => ({
      // ==================== Initial State ====================
      chatsByProject: {},       // Chats keyed by project_id
      currentChatId: null,      // For Messages page
      widgetChatId: null,       // For Chat Widget (independent)
      messages: {},
      pendingAttachmentsByChat: {},
      unreadCounts: {},
      capturedUnreadCounts: {}, // Snapshot of unread_count at the moment each chat is opened
      typingUsersByChat: {},    // chatId -> userIds currently typing (ephemeral, not persisted)
      presenceByUserId: {},     // userId -> current online/offline state
      presenceVersionByUserId: {}, // userId -> latest applied presence version
      mentionedChatIds: {},     // chatId -> true when current user has unread @-mention
      unseenPinChatIds: {},     // chatId -> true until the user opens Pins
      outbox: [],

      // Thread panel
      activeThreadMessageId: null,
      threadReplies: {},
      globalUnreadCount: 0,     // Total unread across ALL projects
      isWidgetOpen: false,
      isMessagePageOpen: false,
      selectedProjectId: null,  // For Messages page
      widgetProjectId: null,    // For Chat Widget (independent)
      currentView: 'list',      // For Messages page
      widgetView: 'list',       // For Chat Widget (independent)
      isLoading: false,

      // ==================== Chat Actions ====================
      
      setChatsForProject: (projectId: number | string, chats: Chat[]) => {
        // Use set() with callback to get CURRENT state at the moment of update
        // This prevents race conditions where state changes between read and write
        set(state => {
          const normalizedChats = chats.map(chat => normalizeChatProject(chat, projectId));
          const currentUnreadCounts = state.unreadCounts;
          const currentChatId = state.currentChatId;
          const nextPresenceByUserId = { ...state.presenceByUserId };
          normalizedChats.forEach(chat => collectChatPresence(chat, nextPresenceByUserId));

          // Build new unread counts, but preserve local values in certain cases:
          // 1. If user is currently viewing a chat (currentChatId), keep its unread as 0
          // 2. If local unread is 0 but backend says non-zero, the user likely just read it
          //    (keep 0 to avoid "unread" reappearing after viewing)
          const newUnreadCounts: Record<number, number> = { ...currentUnreadCounts };
          normalizedChats.forEach(chat => {
            const backendCount = chat.unread_count ?? 0;
            const localCount = currentUnreadCounts[chat.id];
            
            // If this is the currently viewed chat, always keep unread as 0
            if (chat.id === currentChatId) {
              newUnreadCounts[chat.id] = 0;
            }
            // If local count is 0 (user read the messages), don't overwrite with stale backend data
            // This prevents unread count from "reappearing" after user viewed the chat
            else if (localCount === 0 && backendCount > 0) {
              newUnreadCounts[chat.id] = 0;
            }
            // Otherwise, use the higher of local or backend count
            // (WebSocket might have received new messages that backend hasn't counted yet)
            else if (localCount !== undefined) {
              newUnreadCounts[chat.id] = Math.max(localCount, backendCount);
            }
            // No local count exists, use backend value
            else {
              newUnreadCounts[chat.id] = backendCount;
            }
          });
          
          // Always refresh capturedUnreadCounts for the currently-viewed chat from the
          // backend. This handles two races:
          //   1. setCurrentChat fired before fetchChats → captured 0 instead of real count
          //   2. Account switch without remount → stale count from previous session
          // The ChatWindow capture-unread effect is guarded by unreadCapturedForChatRef,
          // so an overwrite here only shows a new divider when the ref hasn't been locked yet.
          const newCapturedUnreadCounts = { ...state.capturedUnreadCounts };
          if (currentChatId !== null) {
            const currentChatBackendCount =
              normalizedChats.find(c => Number(c.id) === Number(currentChatId))?.unread_count ?? 0;
            newCapturedUnreadCounts[currentChatId] = currentChatBackendCount;
          }

          // Update chats with synced unread_count values
          const updatedChats = normalizedChats.map(chat => ({
            ...chat,
            unread_count: newUnreadCounts[chat.id] ?? chat.unread_count ?? 0,
          }));

          return {
            chatsByProject: {
              ...state.chatsByProject,
              [projectId]: updatedChats,
            },
            unreadCounts: newUnreadCounts,
            capturedUnreadCounts: newCapturedUnreadCounts,
            presenceByUserId: nextPresenceByUserId,
          };
        });
      },

      getChatsForProject: (projectId: number | string | null) => {
        if (!projectId) return [];
        return get().chatsByProject[projectId] || [];
      },

      addChat: (chat: Chat) => {
        set(state => {
          const projectId = resolveChatProjectId(chat);
          if (!projectId) {
            console.warn('[ChatStore] Unable to add chat without project id:', chat);
            return state;
          }
          const normalizedChat = normalizeChatProject(chat, projectId);
          const existingChats = state.chatsByProject[projectId] || [];
          const dedupedChats = existingChats.filter(existing => existing.id !== normalizedChat.id);
          const nextPresenceByUserId = { ...state.presenceByUserId };
          collectChatPresence(normalizedChat, nextPresenceByUserId);

          return {
            chatsByProject: {
              ...state.chatsByProject,
              [projectId]: [normalizedChat, ...dedupedChats],
            },
            unreadCounts: {
              ...state.unreadCounts,
              [normalizedChat.id]: normalizedChat.unread_count || 0,
            },
            presenceByUserId: nextPresenceByUserId,
          };
        });
      },

      removeChat: (chatId: number) => {
        set(state => {
          const newChatsByProject: Record<number | string, Chat[]> = {};
          Object.entries(state.chatsByProject).forEach(([projectId, chats]) => {
            newChatsByProject[projectId] = chats.filter(chat => chat.id !== chatId);
          });

          const newMessages = { ...state.messages };
          delete newMessages[chatId];

          const newUnreadCounts = { ...state.unreadCounts };
          delete newUnreadCounts[chatId];

          const clearCurrentChat = state.currentChatId === chatId;
          const clearWidgetChat = state.widgetChatId === chatId;
          const removedUnread = state.unreadCounts[chatId] || 0;

          return {
            chatsByProject: newChatsByProject,
            messages: newMessages,
            unreadCounts: newUnreadCounts,
            globalUnreadCount: Math.max(0, state.globalUnreadCount - removedUnread),
            currentChatId: clearCurrentChat ? null : state.currentChatId,
            currentView: clearCurrentChat ? 'list' : state.currentView,
            widgetChatId: clearWidgetChat ? null : state.widgetChatId,
            widgetView: clearWidgetChat ? 'list' : state.widgetView,
          };
        });
      },

      updateChat: (chatId: number, updates: Partial<Chat>) => {
        set(state => {
          const newChatsByProject = { ...state.chatsByProject };
          
          // Find and update the chat in the correct project
          Object.keys(newChatsByProject).forEach(projectId => {
            newChatsByProject[projectId] = newChatsByProject[projectId].map(chat =>
              chat.id === chatId ? { ...chat, ...updates } : chat
            );
          });
          
          return { chatsByProject: newChatsByProject };
        });
      },

      setCurrentChat: (chatId: number | null) => {
        // Ensure chatId is a number for consistent comparison
        const numericChatId = chatId !== null ? Number(chatId) : null;
        
        // Single atomic state update
        set(state => {
          const updates: Partial<ChatState> = {
            currentChatId: numericChatId,
            currentView: numericChatId !== null ? 'chat' : 'list',
          };
          
          // Snapshot the real unread count before zeroing the badge counter.
          // capturedUnreadCounts[chatId] is immune to subsequent setChatsForProject calls
          // (which see localCount=0 and would zero chat.unread_count), so ChatWindow
          // can reliably read it when the "New messages" divider effect runs.
          if (numericChatId !== null) {
            // Look up current unread_count from chatsByProject
            let capturedCount = 0;
            Object.values(state.chatsByProject).forEach(chats => {
              const found = chats.find(c => Number(c.id) === numericChatId);
              if (found) capturedCount = found.unread_count ?? 0;
            });

            const newUnreadCounts = { ...state.unreadCounts };
            newUnreadCounts[numericChatId] = 0;
            updates.unreadCounts = newUnreadCounts;

            updates.capturedUnreadCounts = {
              ...state.capturedUnreadCounts,
              [numericChatId]: capturedCount,
            };

            // Clear mention badge when opening the chat
            const nextMentionedChatIds = { ...state.mentionedChatIds };
            delete nextMentionedChatIds[numericChatId];
            updates.mentionedChatIds = nextMentionedChatIds;

            const nextChatsByProject = { ...state.chatsByProject };
            Object.keys(nextChatsByProject).forEach(projectId => {
              nextChatsByProject[projectId] = nextChatsByProject[projectId].map(chat =>
                Number(chat.id) === numericChatId ? { ...chat, mention_unread_count: 0 } : chat
              );
            });
            updates.chatsByProject = nextChatsByProject;
          }
          
          return updates;
        });
      },

      // ==================== Message Actions ====================
      
      setMessages: (chatId: number, messages: Message[]) => {
        set(state => {
          // Merge instead of replace so a websocket event that arrives while the
          // REST history request is in flight is not discarded by the response.
          const existingMessages = state.messages[chatId] ?? [];
          const orderedMessages = reorderMessagesBySequence([...messages, ...existingMessages]);
          return {
            messages: {
              ...state.messages,
              [chatId]: orderedMessages,
            },
            presenceByUserId: presenceFromMessages(state.presenceByUserId, orderedMessages),
          };
        });
      },

      addMessage: (chatId: number, message: Message, currentUserId?: number, replaceClientMessageId?: string) => {
        // CRITICAL: Ensure chatId is always a number for consistent key access
        const numericChatId = Number(chatId);

        set(state => {
          const storedMessages = state.messages[numericChatId] || [];
          // When confirming an optimistic send, drop its placeholder in the SAME
          // reducer pass so the list goes straight from optimistic -> committed
          // without an intermediate state where the message is momentarily absent.
          const existingMessages = replaceClientMessageId
            ? stripOptimisticOutboxMessages(storedMessages, replaceClientMessageId)
            : storedMessages;
          const nextPresenceByUserId = presenceFromMessages(state.presenceByUserId, [message]);

          const existingIndex = existingMessages.findIndex(m => m.id === message.id);
          if (existingIndex >= 0) {
            const existing = existingMessages[existingIndex];
            if (!shouldMergeIncomingMessage(existing, message)) {
              return state;
            }
            const mergedMessages = [...existingMessages];
            mergedMessages[existingIndex] = { ...existing, ...message, attachments: message.attachments ?? existing.attachments };
            return {
              ...state,
              messages: {
                ...state.messages,
                [numericChatId]: reorderMessagesBySequence(mergedMessages),
              },
              presenceByUserId: nextPresenceByUserId,
            };
          }
          
          // Determine if we should increment unread count:
          // 1. Not currently viewing this chat
          // 2. Message is NOT from the current user (don't count your own messages as unread)
          const currentChatIdNum = state.currentChatId !== null ? Number(state.currentChatId) : null;
          const senderId = message.sender?.id ? Number(message.sender.id) : null;
          const userId = currentUserId !== undefined ? Number(currentUserId) : null;
          const isOwnMessage = userId !== null && senderId !== null && senderId === userId;
          
          // User is viewing this chat if currentChatId matches
          const isViewingChat = currentChatIdNum !== null && currentChatIdNum === numericChatId;
          
          // Should NOT increment if: viewing this chat OR it's our own message
          const shouldIncrementUnread = !isViewingChat && !isOwnMessage;
          const mentionedCurrentUser =
            shouldIncrementUnread &&
            userId !== null &&
            (message.mentioned_user_ids ?? []).some(id => Number(id) === userId);
          
          const currentUnreadCount = state.unreadCounts[numericChatId] || 0;
          const newUnreadCount = shouldIncrementUnread 
            ? currentUnreadCount + 1 
            : currentUnreadCount;
          
          // Update chat with new last message AND unread_count in all projects
          const newChatsByProject = { ...state.chatsByProject };
          Object.keys(newChatsByProject).forEach(projectId => {
            newChatsByProject[projectId] = newChatsByProject[projectId].map(chat =>
              Number(chat.id) === numericChatId
                ? {
                    ...chat,
                    last_message: isLaterMessage(message, chat.last_message) ? message : chat.last_message,
                    unread_count: newUnreadCount,
                    mention_unread_count: mentionedCurrentUser
                      ? (chat.mention_unread_count ?? 0) + 1
                      : chat.mention_unread_count,
                  }
                : chat
            );
          });
          
          // Create new unreadCounts object to ensure reference change for reactivity
          const newUnreadCounts = { ...state.unreadCounts };
          newUnreadCounts[numericChatId] = newUnreadCount;
          
          return {
            messages: {
              ...state.messages,
              [numericChatId]: reorderMessagesBySequence([...existingMessages, message]),
            },
            chatsByProject: newChatsByProject,
            unreadCounts: newUnreadCounts,
            presenceByUserId: nextPresenceByUserId,
            ...(mentionedCurrentUser
              ? { mentionedChatIds: { ...state.mentionedChatIds, [numericChatId]: true } }
              : {}),
          };
        });
      },


      enqueueOutbox: (entry: OutboxEntry) => {
        set((state) => {
          if (state.outbox.some((item) => item.clientMessageId === entry.clientMessageId)) {
            return state;
          }
          return { outbox: [...state.outbox, entry] };
        });
      },

      markOutboxSending: (clientMessageId: string) => {
        set((state) => ({
          outbox: state.outbox.map((entry) =>
            entry.clientMessageId === clientMessageId
              ? { ...entry, status: 'sending' as const }
              : entry,
          ),
        }));
      },

      markOutboxFailed: (clientMessageId: string) => {
        set((state) => ({
          outbox: state.outbox.map((entry) =>
            entry.clientMessageId === clientMessageId
              ? { ...entry, status: 'failed' as const }
              : entry,
          ),
        }));
      },

      markOutboxSent: (clientMessageId: string, message: Message) => {
        const numericChatId = Number(message.chat_id ?? message.chat);
        // Clear the outbox entry, then let addMessage swap the optimistic
        // placeholder for the committed message in a single reducer pass. The
        // message list never passes through a frame where the message is gone.
        set((state) => ({
          outbox: state.outbox.filter((entry) => entry.clientMessageId !== clientMessageId),
        }));
        get().addMessage(numericChatId, message, undefined, clientMessageId);
      },

      getOutboxDigest: () => {
        return get()
          .outbox
          .filter((entry) => entry.status === 'pending' || entry.status === 'sending' || entry.status === 'failed')
          .map((entry) => entry.clientMessageId);
      },

      retryOutboxEntry: async (clientMessageId: string) => {
        const entry = get().outbox.find((item) => item.clientMessageId === clientMessageId);
        if (!entry) {
          return;
        }
        // Entries left in 'sending' after a refresh/crash must stay retryable;
        // server-side client_message_id dedupe makes a redundant resend safe.
        get().markOutboxSending(clientMessageId);
        try {
          const message = await sendMessage({
            chat_id: entry.chatId,
            content: entry.content,
            attachment_ids: entry.attachmentIds.length > 0 ? entry.attachmentIds : undefined,
            mention_ids: entry.mentionIds,
            rich_body: entry.richBody,
            reply_to_id: entry.replyToId,
            parent_message_id: entry.parentMessageId,
            client_message_id: entry.clientMessageId,
          });
          get().markOutboxSent(clientMessageId, message);
        } catch (error) {
          console.error('Outbox retry failed:', error);
          get().markOutboxFailed(clientMessageId);
        }
      },

      flushOutbox: async () => {
        const pendingIds = get()
          .outbox
          .filter((entry) => entry.status === 'pending' || entry.status === 'sending' || entry.status === 'failed')
          .map((entry) => entry.clientMessageId);
        for (const clientMessageId of pendingIds) {
          await get().retryOutboxEntry(clientMessageId);
        }
      },

      reconcileOutboxAck: async (committed: OutboxAckCommit[]) => {
        for (const item of committed) {
          const entry = get().outbox.find((row) => row.clientMessageId === item.client_message_id);
          set((state) => ({
            outbox: state.outbox.filter((row) => row.clientMessageId !== item.client_message_id),
          }));
          if (!entry) {
            continue;
          }
          try {
            // The server embeds the committed message in the ack, so the common
            // path needs no HTTP. Fall back to a fetch only if it's absent.
            const message = item.message ?? await getMessage(item.message_id);
            get().markOutboxSent(item.client_message_id, message);
          } catch (error) {
            console.error('Failed to hydrate outbox ack message:', error);
          }
        }
      },

      prependMessages: (chatId: number, messages: Message[]) => {
        set(state => {
          const existingMessages = state.messages[chatId] || [];
          
          // Filter out duplicates
          const newMessages = messages.filter(
            newMsg => !existingMessages.some(existing => existing.id === newMsg.id)
          );
          
          return {
            messages: {
              ...state.messages,
              [chatId]: reorderMessagesBySequence([...newMessages, ...existingMessages]),
            },
            presenceByUserId: presenceFromMessages(state.presenceByUserId, newMessages),
          };
        });
      },

      updateMessage: (messageId: number, updates: Partial<Message>) => {
        set(state => {
          const newMessages = { ...state.messages };

          // Find and update the message in the correct chat
          Object.keys(newMessages).forEach(chatIdStr => {
            const chatId = parseInt(chatIdStr);
            newMessages[chatId] = newMessages[chatId].map(msg =>
              msg.id === messageId ? { ...msg, ...updates } : msg
            );
          });

          return { messages: newMessages };
        });
      },

      removeMessage: (messageId: number) => {
        deletedMessageIds.add(messageId);
        set(state => {
          const newMessages = { ...state.messages };
          Object.keys(newMessages).forEach(chatIdStr => {
            const chatId = parseInt(chatIdStr);
            newMessages[chatId] = newMessages[chatId].filter(msg => msg.id !== messageId);
          });
          return { messages: newMessages };
        });
      },

      addPendingAttachments: (chatId: number, attachments: PendingAttachment[]) => {
        if (attachments.length === 0) return;
        set((state) => ({
          pendingAttachmentsByChat: {
            ...state.pendingAttachmentsByChat,
            [chatId]: [...(state.pendingAttachmentsByChat[chatId] ?? []), ...attachments],
          },
        }));
      },

      updatePendingAttachment: (chatId: number, attachmentId: string, updates: Partial<PendingAttachment>) => {
        set((state) => {
          const attachments = state.pendingAttachmentsByChat[chatId];
          if (!attachments?.some((attachment) => attachment.id === attachmentId)) return state;
          return {
            pendingAttachmentsByChat: {
              ...state.pendingAttachmentsByChat,
              [chatId]: attachments.map((attachment) =>
                attachment.id === attachmentId ? { ...attachment, ...updates } : attachment,
              ),
            },
          };
        });
      },

      removePendingAttachment: (chatId: number, attachmentId: string) => {
        set((state) => {
          const attachments = state.pendingAttachmentsByChat[chatId];
          if (!attachments) return state;

          const attachmentToRemove = attachments.find((attachment) => attachment.id === attachmentId);
          const nextAttachments = attachments.filter((attachment) => attachment.id !== attachmentId);
          if (nextAttachments.length === attachments.length) return state;

          revokeAttachmentPreview(attachmentToRemove);

          const nextPendingAttachmentsByChat = { ...state.pendingAttachmentsByChat };
          if (nextAttachments.length === 0) {
            delete nextPendingAttachmentsByChat[chatId];
          } else {
            nextPendingAttachmentsByChat[chatId] = nextAttachments;
          }

          return { pendingAttachmentsByChat: nextPendingAttachmentsByChat };
        });
      },

      clearPendingAttachments: (chatId: number) => {
        set((state) => {
          const attachments = state.pendingAttachmentsByChat[chatId];
          if (!attachments?.length) return state;

          revokeAttachmentPreviews(attachments);

          const nextPendingAttachmentsByChat = { ...state.pendingAttachmentsByChat };
          delete nextPendingAttachmentsByChat[chatId];
          return { pendingAttachmentsByChat: nextPendingAttachmentsByChat };
        });
      },

      applyReactionUpdate: (messageId, emoji, action, user, currentUserId) => {
        set(state => {
          const newMessages = { ...state.messages };
          const actorId = Number(user.id);
          const currentId = currentUserId !== null ? Number(currentUserId) : null;

          const applyToMessage = (msg: Message): Message => {
            if (msg.id !== messageId) return msg;
            const existing = msg.reactions ?? [];
            if (action === 'added') {
              const idx = existing.findIndex(r => r.emoji === emoji);
              if (idx >= 0) {
                if (existing[idx].users.some(u => Number(u.id) === actorId)) return msg;
                const updated = existing.map((r, i) => i !== idx ? r : {
                  ...r,
                  count: r.count + 1,
                  users: [...r.users, user],
                  reacted_by_me: r.reacted_by_me || actorId === currentId,
                });
                return { ...msg, reactions: updated };
              } else {
                return {
                  ...msg,
                  reactions: [
                    ...existing,
                    {
                      emoji,
                      count: 1,
                      users: [user],
                      reacted_by_me: actorId === currentId,
                    },
                  ],
                };
              }
            }

            const reaction = existing.find(r => r.emoji === emoji);
            if (!reaction || !reaction.users.some(u => Number(u.id) === actorId)) {
              return msg;
            }

            const updated = existing
              .map(r => {
                if (r.emoji !== emoji) return r;
                const users = r.users.filter(u => Number(u.id) !== actorId);
                return {
                  ...r,
                  count: users.length,
                  users,
                  reacted_by_me: actorId === currentId ? false : r.reacted_by_me,
                };
              })
              .filter(r => r.count > 0);

            return { ...msg, reactions: updated };
          };

          // Update main timeline messages
          Object.keys(newMessages).forEach(chatIdStr => {
            const chatId = parseInt(chatIdStr);
            newMessages[chatId] = newMessages[chatId].map(applyToMessage);
          });

          // Update thread replies so reaction updates propagate there too
          const newThreadReplies = { ...state.threadReplies };
          Object.keys(newThreadReplies).forEach(rootIdStr => {
            const rootId = parseInt(rootIdStr);
            const replies = newThreadReplies[rootId];
            if (replies.some(r => r.id === messageId)) {
              newThreadReplies[rootId] = replies.map(applyToMessage);
            }
          });

          // Seed presence from the reactor — no-op while ReactionUser carries no
          // is_online, but keeps the "observe a user → seed presence" invariant honest.
          const nextPresenceByUserId = { ...state.presenceByUserId };
          collectUserPresence(user, nextPresenceByUserId);

          return {
            messages: newMessages,
            threadReplies: newThreadReplies,
            presenceByUserId: nextPresenceByUserId,
          };
        });
      },

      applyLinkPreview: (messageId, preview) => {
        set(state => {
          // Attach a preview that finished fetching after the message arrived, so
          // the card appears without a reload. Same shape the serializer sends,
          // so a live card and a reloaded one are identical.
          const attach = (msg: Message): Message =>
            msg.id === messageId ? { ...msg, link_preview: preview } : msg;

          const newMessages = { ...state.messages };
          Object.keys(newMessages).forEach(chatIdStr => {
            const chatId = parseInt(chatIdStr);
            if (newMessages[chatId].some(m => m.id === messageId)) {
              newMessages[chatId] = newMessages[chatId].map(attach);
            }
          });

          const newThreadReplies = { ...state.threadReplies };
          Object.keys(newThreadReplies).forEach(rootIdStr => {
            const rootId = parseInt(rootIdStr);
            if (newThreadReplies[rootId].some(r => r.id === messageId)) {
              newThreadReplies[rootId] = newThreadReplies[rootId].map(attach);
            }
          });

          return { messages: newMessages, threadReplies: newThreadReplies };
        });
      },

      clearLinkPreview: (messageId) => {
        set(state => {
          // Dismissing is a view preference; the message and its text are untouched.
          const drop = (msg: Message): Message =>
            msg.id === messageId ? { ...msg, link_preview: null } : msg;

          const newMessages = { ...state.messages };
          Object.keys(newMessages).forEach(chatIdStr => {
            const chatId = parseInt(chatIdStr);
            if (newMessages[chatId].some(m => m.id === messageId)) {
              newMessages[chatId] = newMessages[chatId].map(drop);
            }
          });

          const newThreadReplies = { ...state.threadReplies };
          Object.keys(newThreadReplies).forEach(rootIdStr => {
            const rootId = parseInt(rootIdStr);
            if (newThreadReplies[rootId].some(r => r.id === messageId)) {
              newThreadReplies[rootId] = newThreadReplies[rootId].map(drop);
            }
          });

          return { messages: newMessages, threadReplies: newThreadReplies };
        });
      },

      updateUserPresence: (userId: number, isOnline: boolean, version: number | null = null) => {
        const numericUserId = Number(userId);
        if (!Number.isFinite(numericUserId)) return;
        const numericVersion = typeof version === 'number' && Number.isFinite(version) ? version : null;

        set(state => {
          const currentVersion = state.presenceVersionByUserId[numericUserId] ?? -1;
          if (numericVersion !== null && numericVersion < currentVersion) return state;
          if (
            state.presenceByUserId[numericUserId] === isOnline &&
            (numericVersion === null || numericVersion === currentVersion)
          ) {
            return state;
          }
          return {
            presenceByUserId: {
              ...state.presenceByUserId,
              [numericUserId]: isOnline,
            },
            presenceVersionByUserId: numericVersion !== null
              ? {
                  ...state.presenceVersionByUserId,
                  [numericUserId]: numericVersion,
                }
              : state.presenceVersionByUserId,
          };
        });
      },

      setPresenceSnapshot: (users) => {
        set((state) => {
          const nextPresenceByUserId: Record<number, boolean> = {};
          const nextPresenceVersionByUserId: Record<number, number> = {};
          for (const user of users) {
            const userId = Number(user.user_id);
            if (Number.isFinite(userId) && typeof user.is_online === 'boolean') {
              const snapshotVersion = typeof user.version === 'number' && Number.isFinite(user.version)
                ? user.version
                : null;
              const currentVersion = state.presenceVersionByUserId[userId] ?? -1;
              if (snapshotVersion !== null && snapshotVersion < currentVersion) {
                if (typeof state.presenceByUserId[userId] === 'boolean') {
                  nextPresenceByUserId[userId] = state.presenceByUserId[userId];
                  nextPresenceVersionByUserId[userId] = currentVersion;
                }
                continue;
              }
              nextPresenceByUserId[userId] = user.is_online;
              if (snapshotVersion !== null) nextPresenceVersionByUserId[userId] = snapshotVersion;
            }
          }
          return {
            presenceByUserId: nextPresenceByUserId,
            presenceVersionByUserId: nextPresenceVersionByUserId,
          };
        });
      },

      // ==================== Unread Count Actions ====================
      
      updateUnreadCount: (chatId: number, count: number) => {
        const safeCount = Math.max(0, count);
        
        // Single atomic state update
        set(state => {
          // Update chat unread_count in all projects
          const newChatsByProject = { ...state.chatsByProject };
          Object.keys(newChatsByProject).forEach(projectId => {
            newChatsByProject[projectId] = newChatsByProject[projectId].map(chat =>
              chat.id === chatId
                ? {
                    ...chat,
                    unread_count: safeCount,
                    mention_unread_count: safeCount === 0 ? 0 : chat.mention_unread_count,
                  }
                : chat
            );
          });

          const nextMentionedChatIds = { ...state.mentionedChatIds };
          if (safeCount === 0) {
            delete nextMentionedChatIds[chatId];
          }
          
          return {
            unreadCounts: {
              ...state.unreadCounts,
              [chatId]: safeCount,
            },
            chatsByProject: newChatsByProject,
            mentionedChatIds: nextMentionedChatIds,
          };
        });
      },

      decrementUnreadCount: (chatId: number) => {
        const current = get().unreadCounts[chatId] || 0;
        get().updateUnreadCount(chatId, current - 1);
      },

      // ==================== Typing Indicator Actions ====================

      setTypingUser: (chatId: number, userId: number) => {
        set((state) => {
          const current = state.typingUsersByChat[chatId] ?? [];
          if (current.includes(userId)) return state;
          return {
            typingUsersByChat: {
              ...state.typingUsersByChat,
              [chatId]: [...current, userId],
            },
          };
        });
      },

      clearTypingUser: (chatId: number, userId: number) => {
        set((state) => {
          const current = state.typingUsersByChat[chatId];
          if (!current || !current.includes(userId)) return state;
          const next = current.filter((id) => id !== userId);
          const updated = { ...state.typingUsersByChat };
          if (next.length === 0) {
            delete updated[chatId];
          } else {
            updated[chatId] = next;
          }
          return { typingUsersByChat: updated };
        });
      },

      getTypingUsers: (chatId: number) => {
        return get().typingUsersByChat[chatId] ?? [];
      },

      // ==================== UI State Actions ====================
      
      openWidget: () => {
        set({ isWidgetOpen: true });
      },

      closeWidget: () => {
        set({
          isWidgetOpen: false,
          widgetChatId: null,
          widgetView: 'list',
        });
      },

      // Widget-specific actions
      setWidgetChat: (chatId: number | null) => {
        const numericChatId = chatId !== null ? Number(chatId) : null;
        set(state => {
          const updates: Partial<ChatState> = {
            widgetChatId: numericChatId,
            widgetView: numericChatId !== null ? 'chat' : 'list',
          };

          // Mirror the same unread-count snapshot logic as setCurrentChat so the
          // "New messages" divider works in the widget for both DMs and channels.
          if (numericChatId !== null) {
            let capturedCount = 0;
            Object.values(state.chatsByProject).forEach(chats => {
              const found = chats.find(c => Number(c.id) === numericChatId);
              if (found) capturedCount = found.unread_count ?? 0;
            });

            const newUnreadCounts = { ...state.unreadCounts };
            newUnreadCounts[numericChatId] = 0;
            updates.unreadCounts = newUnreadCounts;

            updates.capturedUnreadCounts = {
              ...state.capturedUnreadCounts,
              [numericChatId]: capturedCount,
            };

            const nextMentionedChatIds = { ...state.mentionedChatIds };
            delete nextMentionedChatIds[numericChatId];
            updates.mentionedChatIds = nextMentionedChatIds;

            const nextChatsByProject = { ...state.chatsByProject };
            Object.keys(nextChatsByProject).forEach(projectId => {
              nextChatsByProject[projectId] = nextChatsByProject[projectId].map(chat =>
                Number(chat.id) === numericChatId ? { ...chat, mention_unread_count: 0 } : chat
              );
            });
            updates.chatsByProject = nextChatsByProject;
          }

          return updates;
        });
      },

      setWidgetProjectId: (projectId: number | string | null) => {
        set({ widgetProjectId: projectId });
      },

      setWidgetView: (view: 'list' | 'chat') => {
        set({ widgetView: view });
      },

      setMessagePageOpen: (isOpen: boolean) => {
        set({ 
          isMessagePageOpen: isOpen,
          // Close widget when message page opens
          isWidgetOpen: isOpen ? false : get().isWidgetOpen,
        });
      },

      setSelectedProjectId: (projectId: number | string | null) => {
        set({ selectedProjectId: projectId });
      },

      setView: (view: 'list' | 'chat') => {
        set({ currentView: view });
      },

      setLoading: (loading: boolean) => {
        set({ isLoading: loading });
      },

      // ==================== Helper Methods ====================
      
      getCurrentChat: () => {
        const { chatsByProject, currentChatId, selectedProjectId } = get();
        if (!currentChatId || !selectedProjectId) return undefined;
        const chats = chatsByProject[selectedProjectId] || [];
        return chats.find(chat => chat.id === currentChatId);
      },

      getCurrentMessages: () => {
        const { messages, currentChatId } = get();
        if (!currentChatId) return [];
        return messages[currentChatId] || [];
      },

      getTotalUnreadCount: () => {
        const { unreadCounts } = get();
        return Object.values(unreadCounts).reduce((sum, count) => sum + count, 0);
      },

      // Fetch global unread count from backend (across ALL projects)
      fetchGlobalUnreadCount: async () => {
        try {
          const count = await getUnreadCount(); // No chatId = global count
          set({ globalUnreadCount: count });
          return count;
        } catch (error) {
          console.error('Error fetching global unread count:', error);
          return 0;
        }
      },

      // Update global unread count (called when messages are read)
      setGlobalUnreadCount: (count: number) => {
        set({ globalUnreadCount: Math.max(0, count) });
      },

      // Increment global unread count (called when new message received)
      incrementGlobalUnreadCount: () => {
        set(state => ({ globalUnreadCount: state.globalUnreadCount + 1 }));
      },

      // Decrement global unread count (called when message is read)
      decrementGlobalUnreadCount: (amount: number = 1) => {
        set(state => ({ globalUnreadCount: Math.max(0, state.globalUnreadCount - amount) }));
      },

      // Reset all per-user in-memory state so the next login always picks up
      // fresh counts from the backend. Called by authStore.clearAuth().
      // This prevents the "preserve local 0" logic in setChatsForProject from
      // hiding unread counts that arrived while the user was logged out.
      clearUserState: () => {
        revokeAttachmentPreviews(Object.values(get().pendingAttachmentsByChat).flat());
        set({
          chatsByProject: {},
          messages: {},
          pendingAttachmentsByChat: {},
          unreadCounts: {},
          capturedUnreadCounts: {},
          globalUnreadCount: 0,
          typingUsersByChat: {},
          presenceByUserId: {},
          presenceVersionByUserId: {},
          mentionedChatIds: {},
          unseenPinChatIds: {},
          activeThreadMessageId: null,
          threadReplies: {},
          outbox: [],
        });
      },

      // ── SSE-driven chat activity signal ──────────────────────────────
      lastChatActivity: 0,
      triggerChatActivity: () => set({ lastChatActivity: Date.now() }),

      // ── Files tab refresh signal ─────────────────────────────────────
      // Bumped when a message is deleted or revoked so FilesSidebarView refetches.
      filesRefreshAt: 0,
      triggerFilesRefresh: () => set({ filesRefreshAt: Date.now() }),

      // ── Mention badges ───────────────────────────────────────────────
      addMentionedChat: (chatId) =>
        set((state) => ({
          mentionedChatIds: { ...state.mentionedChatIds, [chatId]: true },
        })),
      clearMentionedChat: (chatId) =>
        set((state) => {
          const next = { ...state.mentionedChatIds };
          delete next[chatId];
          const newChatsByProject = { ...state.chatsByProject };
          Object.keys(newChatsByProject).forEach(projectIdStr => {
            const projectId = parseInt(projectIdStr);
            newChatsByProject[projectId] = newChatsByProject[projectId].map(chat =>
              Number(chat.id) === Number(chatId) ? { ...chat, mention_unread_count: 0 } : chat
            );
          });
          return { mentionedChatIds: next, chatsByProject: newChatsByProject };
        }),

      // ── Shared pin badges ─────────────────────────────────────────────
      markChatPinUnseen: (chatId) =>
        set((state) => ({
          unseenPinChatIds: { ...state.unseenPinChatIds, [chatId]: true },
        })),
      clearChatPinUnseen: (chatId) =>
        set((state) => {
          const next = { ...state.unseenPinChatIds };
          delete next[chatId];
          return { unseenPinChatIds: next };
        }),

      // ── Thread panel ─────────────────────────────────────────────────
      setActiveThreadMessageId: (id) => set({ activeThreadMessageId: id }),

      setThreadReplies: (rootId, replies) =>
        set((state) => ({
          threadReplies: { ...state.threadReplies, [rootId]: reorderMessagesBySequence(replies) },
          presenceByUserId: presenceFromMessages(state.presenceByUserId, replies),
        })),

      addThreadReply: (rootId, reply) =>
        set((state) => {
          const existing = state.threadReplies[rootId] ?? [];
          // Avoid duplicates
          if (existing.some((r) => r.id === reply.id)) return state;
          return {
            threadReplies: {
              ...state.threadReplies,
              [rootId]: reorderMessagesBySequence([...existing, reply]),
            },
            presenceByUserId: presenceFromMessages(state.presenceByUserId, [reply]),
          };
        }),

      updateThreadReply: (replyId, updates) =>
        set((state) => {
          const next = { ...state.threadReplies };
          for (const rootId of Object.keys(next)) {
            const replies = next[Number(rootId)];
            if (replies.some((r) => r.id === replyId)) {
              next[Number(rootId)] = replies.map((r) =>
                r.id === replyId ? { ...r, ...updates } : r,
              );
              break;
            }
          }
          return { threadReplies: next };
        }),
    }),
    {
      name: 'chat-storage',
      // Only persist specific fields
      partialize: (state) => ({
        isWidgetOpen: state.isWidgetOpen,
        outbox: state.outbox,
        unseenPinChatIds: state.unseenPinChatIds,
        // Don't persist chats/messages as they should be fetched fresh
      }),
    }
  )
);

/**
 * Resolve a numeric chat id to its slug from the loaded chats.
 *
 * Chat detail routes are slug-only (SMP-539), but hooks that only hold a numeric
 * chat id need the slug to address them. Returns undefined if the chat isn't loaded.
 */
export const getChatSlugById = (chatId: number | string): string | undefined => {
  const { chatsByProject } = useChatStore.getState();
  for (const chats of Object.values(chatsByProject)) {
    const found = chats.find((chat) => chat.id === chatId);
    if (found?.slug) {
      return found.slug;
    }
  }
  return undefined;
};
