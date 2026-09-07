import { reorderMessagesBySequence, useChatStore } from '@/lib/chatStore';
import type { Message } from '@/types/chat';

const sender = { id: 7, email: 'sender@example.com', username: 'sender' };

const message = (id: number, seq: number): Message => ({
  id,
  seq,
  chat_id: 3,
  sender,
  content: `message-${seq}`,
  created_at: `2026-08-05T00:00:${String(seq).padStart(2, '0')}.000Z`,
  updated_at: `2026-08-05T00:00:${String(seq).padStart(2, '0')}.000Z`,
});

describe('chat message sequence ordering', () => {
  beforeEach(() => {
    useChatStore.setState({
      messages: {},
      chatsByProject: {},
      unreadCounts: {},
      capturedUnreadCounts: {},
      typingUsersByChat: {},
      presenceByUserId: {},
      presenceVersionByUserId: {},
      mentionedChatIds: {},
      currentChatId: 3,
      threadReplies: {},
    });
  });

  it('orders a burst by server sequence rather than arrival time', () => {
    expect(reorderMessagesBySequence([
      message(103, 3),
      message(101, 1),
      message(102, 2),
    ]).map((item) => item.seq)).toEqual([1, 2, 3]);
  });

  it('returns an already ordered committed message list without rebuilding it', () => {
    const ordered = [message(101, 1), message(102, 2), message(103, 3)];

    expect(reorderMessagesBySequence(ordered)).toBe(ordered);
  });

  it('reorders live messages that arrive out of order', () => {
    const store = useChatStore.getState();
    store.addMessage(3, message(103, 3));
    store.addMessage(3, message(101, 1));
    store.addMessage(3, message(102, 2));

    expect(useChatStore.getState().messages[3].map((item) => item.seq)).toEqual([1, 2, 3]);
  });

  it('merges REST history and a live message consistently', () => {
    useChatStore.getState().setMessages(3, [message(102, 2), message(101, 1)]);
    useChatStore.getState().addMessage(3, message(103, 3));

    expect(useChatStore.getState().messages[3].map((item) => item.seq)).toEqual([1, 2, 3]);
  });

  it('keeps a live message that arrives before REST history completes', () => {
    useChatStore.getState().addMessage(3, message(103, 3));
    useChatStore.getState().setMessages(3, [message(102, 2), message(101, 1)]);

    expect(useChatStore.getState().messages[3].map((item) => item.seq)).toEqual([1, 2, 3]);
  });

  it('deduplicates retry payloads by sequence', () => {
    expect(reorderMessagesBySequence([
      message(101, 1),
      message(101, 1),
      message(102, 2),
    ]).map((item) => item.seq)).toEqual([1, 2]);
  });
});
