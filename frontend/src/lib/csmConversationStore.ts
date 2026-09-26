import { create } from 'zustand';
import { Conversation, ConversationMessage } from '@/types/csmConversation';

interface TypingState {
  [conversationId: number]: number[]; // user IDs currently typing
}

/**
 * In-memory composer draft, keyed by conversation. Survives switching between
 * conversations but is lost on page reload (the store has no persist layer),
 * so an unsent reply is restored when the agent returns to a conversation but
 * never persists beyond the session.
 */
export interface ComposerDraft {
  richBody: object | null; // editor.getJSON() snapshot
  imageFile: File | null; // File reference (valid only until reload)
  imagePreviewUrl: string | null; // ObjectURL tied to imageFile
}

/**
 * Text another panel (e.g. guidance) asks the reply composer to insert. The
 * composer for ``conversationId`` consumes it; ``nonce`` makes each request
 * unique so the same text can be inserted twice.
 */
export interface ComposerInsertRequest {
  conversationId: number;
  text: string;
  nonce: number;
}

interface CsmConversationState {
  conversations: Conversation[];
  activeConversationId: number | null;
  selectedQueueId: number | null;
  messagesByConversation: Record<number, ConversationMessage[]>;
  typingByConversation: TypingState;
  draftsByConversation: Record<number, ComposerDraft>;
  /** Bumped when guidance for an Experience Group changes (live updates). */
  guidanceVersionByGroup: Record<number, number>;
  pendingComposerInsert: ComposerInsertRequest | null;

  // Actions
  setConversations: (conversations: Conversation[]) => void;
  updateConversation: (updated: Conversation) => void;
  setActiveConversation: (id: number | null) => void;
  setSelectedQueueId: (id: number | null) => void;
  setMessages: (conversationId: number, messages: ConversationMessage[]) => void;
  addMessage: (conversationId: number, message: ConversationMessage) => void;
  setTyping: (conversationId: number, userId: number, isTyping: boolean) => void;
  setDraft: (conversationId: number, draft: ComposerDraft) => void;
  clearDraft: (conversationId: number) => void;
  bumpGuidance: (experienceGroupIds: number[]) => void;
  requestComposerInsert: (conversationId: number, text: string) => void;
  consumeComposerInsert: (nonce: number) => void;
}

let composerInsertNonce = 0;

export const useCsmConversationStore = create<CsmConversationState>((set) => ({
  conversations: [],
  activeConversationId: null,
  selectedQueueId: null,
  messagesByConversation: {},
  typingByConversation: {},
  draftsByConversation: {},
  guidanceVersionByGroup: {},
  pendingComposerInsert: null,

  setConversations: (conversations) => set({ conversations }),

  updateConversation: (updated) =>
    set((state) => ({
      conversations: state.conversations.map((c) =>
        c.id === updated.id ? { ...c, ...updated } : c
      ),
    })),

  setActiveConversation: (id) => set({ activeConversationId: id }),

  setSelectedQueueId: (id) => set({ selectedQueueId: id }),

  setMessages: (conversationId, messages) =>
    set((state) => ({
      messagesByConversation: { ...state.messagesByConversation, [conversationId]: messages },
    })),

  addMessage: (conversationId, message) =>
    set((state) => {
      const existing = state.messagesByConversation[conversationId] ?? [];
      // Avoid duplicates
      if (existing.some((m) => m.id === message.id)) return state;
      return {
        messagesByConversation: {
          ...state.messagesByConversation,
          [conversationId]: [...existing, message],
        },
      };
    }),

  setTyping: (conversationId, userId, isTyping) =>
    set((state) => {
      const current = state.typingByConversation[conversationId] ?? [];
      const updated = isTyping
        ? current.includes(userId) ? current : [...current, userId]
        : current.filter((id) => id !== userId);
      return {
        typingByConversation: { ...state.typingByConversation, [conversationId]: updated },
      };
    }),

  setDraft: (conversationId, draft) =>
    set((state) => ({
      draftsByConversation: { ...state.draftsByConversation, [conversationId]: draft },
    })),

  clearDraft: (conversationId) =>
    set((state) => {
      if (!(conversationId in state.draftsByConversation)) return state;
      const next = { ...state.draftsByConversation };
      delete next[conversationId];
      return { draftsByConversation: next };
    }),

  bumpGuidance: (experienceGroupIds) =>
    set((state) => {
      const next = { ...state.guidanceVersionByGroup };
      experienceGroupIds.forEach((id) => {
        next[id] = (next[id] ?? 0) + 1;
      });
      return { guidanceVersionByGroup: next };
    }),

  requestComposerInsert: (conversationId, text) => {
    composerInsertNonce += 1;
    set({ pendingComposerInsert: { conversationId, text, nonce: composerInsertNonce } });
  },

  consumeComposerInsert: (nonce) =>
    set((state) =>
      state.pendingComposerInsert?.nonce === nonce ? { pendingComposerInsert: null } : state
    ),
}));
