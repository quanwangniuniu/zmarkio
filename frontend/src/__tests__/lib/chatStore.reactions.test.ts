import { useChatStore } from '@/lib/chatStore';
import type { Message } from '@/types/chat';

const makeMessage = (overrides: Partial<Message> = {}): Message => ({
  id: 10,
  chat_id: 2,
  sender: {
    id: 1,
    email: 'sender@example.com',
    username: 'sender',
  },
  content: 'hello',
  created_at: '2026-05-27T00:00:00.000Z',
  updated_at: '2026-05-27T00:00:00.000Z',
  ...overrides,
});

describe('chatStore reaction updates', () => {
  beforeEach(() => {
    useChatStore.setState({
      messages: {},
      threadReplies: {},
      chatsByProject: {},
      unreadCounts: {},
      capturedUnreadCounts: {},
      typingUsersByChat: {},
      presenceByUserId: {},
      presenceVersionByUserId: {},
      mentionedChatIds: {},
      currentChatId: null,
    });
  });

  it('ignores duplicate reaction add events from the same user', () => {
    const user = { id: 2, username: 'zenobia' };
    useChatStore.getState().setMessages(2, [makeMessage()]);

    useChatStore.getState().applyReactionUpdate(10, '👍', 'added', user, 2);
    useChatStore.getState().applyReactionUpdate(10, '👍', 'added', user, 2);

    const [message] = useChatStore.getState().messages[2];
    expect(message.reactions).toEqual([
      {
        emoji: '👍',
        count: 1,
        users: [user],
        reacted_by_me: true,
      },
    ]);
  });

  it('ignores duplicate reaction remove events when optimistic UI already removed it', () => {
    const user = { id: 2, username: 'zenobia' };
    useChatStore.getState().setMessages(2, [
      makeMessage({
        reactions: [
          {
            emoji: '👍',
            count: 1,
            users: [user],
            reacted_by_me: true,
          },
        ],
      }),
    ]);

    useChatStore.getState().applyReactionUpdate(10, '👍', 'removed', user, 2);
    useChatStore.getState().applyReactionUpdate(10, '👍', 'removed', user, 2);

    const [message] = useChatStore.getState().messages[2];
    expect(message.reactions).toEqual([]);
  });

  it('keeps my reacted state when another user removes their reaction', () => {
    const me = { id: 2, username: 'zenobia' };
    const teammate = { id: 3, username: 'teammate' };
    useChatStore.getState().setMessages(2, [
      makeMessage({
        reactions: [
          {
            emoji: '🎉',
            count: 2,
            users: [me, teammate],
            reacted_by_me: true,
          },
        ],
      }),
    ]);

    useChatStore.getState().applyReactionUpdate(10, '🎉', 'removed', teammate, 2);

    const [message] = useChatStore.getState().messages[2];
    expect(message.reactions).toEqual([
      {
        emoji: '🎉',
        count: 1,
        users: [me],
        reacted_by_me: true,
      },
    ]);
  });

  it('tracks unread mention state when a new message mentions the current user', () => {
    useChatStore.setState({
      presenceByUserId: {},
      presenceVersionByUserId: {},
      chatsByProject: {
        9: [
          {
            id: 2,
            slug: 'general',
            project_id: 9,
            project: 9,
            type: 'group',
            name: 'general',
            participants: [],
            created_at: '2026-05-27T00:00:00.000Z',
            updated_at: '2026-05-27T00:00:00.000Z',
            unread_count: 0,
            mention_unread_count: 0,
          },
        ],
      },
      currentChatId: null,
    });

    useChatStore.getState().addMessage(
      2,
      makeMessage({
        id: 11,
        sender: { id: 3, email: 'teammate@example.com', username: 'teammate' },
        mentioned_user_ids: [2],
      }),
      2,
    );

    const chat = useChatStore.getState().chatsByProject[9][0];
    expect(chat.unread_count).toBe(1);
    expect(chat.mention_unread_count).toBe(1);
    expect(useChatStore.getState().mentionedChatIds[2]).toBe(true);
  });

  it('clears mention badges when opening a chat', () => {
    useChatStore.setState({
      presenceByUserId: {},
      presenceVersionByUserId: {},
      chatsByProject: {
        9: [
          {
            id: 2,
            slug: 'general',
            project_id: 9,
            project: 9,
            type: 'group',
            name: 'general',
            participants: [],
            created_at: '2026-05-27T00:00:00.000Z',
            updated_at: '2026-05-27T00:00:00.000Z',
            unread_count: 1,
            mention_unread_count: 1,
          },
        ],
      },
      mentionedChatIds: { 2: true },
    });

    useChatStore.getState().setCurrentChat(2);

    const chat = useChatStore.getState().chatsByProject[9][0];
    expect(chat.mention_unread_count).toBe(0);
    expect(useChatStore.getState().mentionedChatIds[2]).toBeUndefined();
  });

  describe('applyReactionSnapshot', () => {
    const me = { id: 1, username: 'jjxx' };
    const teammate = { id: 5, username: 'ticket' };

    it('ignores an older snapshot that arrives after a newer one', () => {
      // ① Arrange: one message (id 10) in chat 2, no reactions yet
      useChatStore.getState().setMessages(2, [makeMessage()]);

      // ② Act: the newer snapshot (version 200) arrives first ...
      useChatStore.getState().applyReactionSnapshot(
        10,
        [{ emoji: '😆', count: 2, users: [me, teammate] }],
        200,
        me.id,
      );
      // ... then an older snapshot (version 100) arrives late
      useChatStore.getState().applyReactionSnapshot(
        10,
        [{ emoji: '😆', count: 1, users: [teammate] }],
        100,
        me.id,
      );

      // ③ Assert: the late, older snapshot was ignored
      const [message] = useChatStore.getState().messages[2];
      expect(message.reactions).toEqual([
        { emoji: '😆', count: 2, users: [me, teammate], reacted_by_me: true },
      ]);
      expect(message.reactions_version).toBe(200);
    });

    it('applies a newer snapshot over an older one', () => {
      // ① Arrange: one message (id 10) in chat 2, no reactions yet
      useChatStore.getState().setMessages(2, [makeMessage()]);

      // ② Act: an older snapshot arrives first, then a newer one with a different emoji
      useChatStore.getState().applyReactionSnapshot(
        10,
        [{ emoji: '👍', count: 1, users: [teammate] }],
        100,
        me.id,
      );
      useChatStore.getState().applyReactionSnapshot(
        10,
        [{ emoji: '😆', count: 2, users: [me, teammate] }],
        200,
        me.id,
      );

      // ③ Assert: the newer snapshot replaced the older one entirely (👍 is gone)
      const [message] = useChatStore.getState().messages[2];
      expect(message.reactions).toEqual([
        { emoji: '😆', count: 2, users: [me, teammate], reacted_by_me: true },
      ]);
      expect(message.reactions_version).toBe(200);
    });

    it('computes reacted_by_me from the users list for the current user', () => {
      // ① Arrange
      useChatStore.getState().setMessages(2, [makeMessage()]);

      // ② Act: one snapshot with two emojis; I am only in the first one
      useChatStore.getState().applyReactionSnapshot(
        10,
        [
          { emoji: '😆', count: 2, users: [me, teammate] },
          { emoji: '❤️', count: 1, users: [teammate] },
        ],
        200,
        me.id,
      );

      // ③ Assert: true where I am in users, false where I am not
      const [message] = useChatStore.getState().messages[2];
      expect(message.reactions).toEqual([
        { emoji: '😆', count: 2, users: [me, teammate], reacted_by_me: true },
        { emoji: '❤️', count: 1, users: [teammate], reacted_by_me: false },
      ]);
    });

    it('clears all reactions when the snapshot is empty', () => {
      // ① Arrange: the message currently shows 👍 from me
      useChatStore.getState().setMessages(2, [
        makeMessage({
          reactions: [{ emoji: '👍', count: 1, users: [me], reacted_by_me: true }],
        }),
      ]);

      // ② Act: the last reaction was removed, so the snapshot is empty
      useChatStore.getState().applyReactionSnapshot(10, [], 100, me.id);

      // ③ Assert: the empty snapshot cleared all reactions
      const [message] = useChatStore.getState().messages[2];
      expect(message.reactions).toEqual([]);
    });

    it('leaves other messages in the chat unchanged', () => {
      // ① Arrange: two messages (id 10 and id 11) in chat 2
      useChatStore.getState().setMessages(2, [makeMessage(), makeMessage({ id: 11 })]);

      // ② Act: a snapshot for message 10 only
      useChatStore.getState().applyReactionSnapshot(
        10,
        [{ emoji: '😆', count: 1, users: [teammate] }],
        200,
        me.id,
      );

      // ③ Assert: message 10 got the reaction, message 11 was not touched
      const [target, other] = useChatStore.getState().messages[2];
      expect(target.reactions).toEqual([
        { emoji: '😆', count: 1, users: [teammate], reacted_by_me: false },
      ]);
      expect(other.reactions).toBeUndefined();
      expect(other.reactions_version).toBeUndefined();
    });

    it('updates the same message inside thread replies', () => {
      // ① Arrange: message 10 is a reply inside the thread of root message 99
      useChatStore.setState({ threadReplies: { 99: [makeMessage()] } });

      // ② Act
      useChatStore.getState().applyReactionSnapshot(
        10,
        [{ emoji: '😆', count: 1, users: [teammate] }],
        200,
        me.id,
      );

      // ③ Assert: the reply in the thread was updated too
      const [reply] = useChatStore.getState().threadReplies[99];
      expect(reply.reactions).toEqual([
        { emoji: '😆', count: 1, users: [teammate], reacted_by_me: false },
      ]);
      expect(reply.reactions_version).toBe(200);
    });
  });
});
