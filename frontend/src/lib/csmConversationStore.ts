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

export interface CsmConversationState { 
  conversations: Conversation[];
  activeConversationId: number | null;
  selectedQueueId: number | null;
  messagesByConversation: Record<number, ConversationMessage[]>;
  typingByConversation: TypingState;
  draftsByConversation: Record<number, ComposerDraft>;

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
}

type UseCsmConversationStore = {
  (): CsmConversationState;
  <T>(selector: (state: CsmConversationState) => T): T;
  getState: () => CsmConversationState;
  setState: (
    partial:
      | CsmConversationState
      | Partial<CsmConversationState>
      | ((state: CsmConversationState) => CsmConversationState | Partial<CsmConversationState>),
    replace?: boolean
  ) => void;
  subscribe: (
    listener: (state: CsmConversationState, prevState: CsmConversationState) => void
  ) => () => void;
  getInitialState: () => CsmConversationState;
};
export const useCsmConversationStore = create<CsmConversationState>((set) => ({
  conversations: [],
  activeConversationId: null,
  selectedQueueId: null,
  messagesByConversation: {},
  typingByConversation: {},
  draftsByConversation: {},

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
})) as unknown as UseCsmConversationStore; 
