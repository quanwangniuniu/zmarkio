/**
 * MED-277: every place outside ChatWindow that adds/removes a reaction through the
 * API must apply the response as a versioned snapshot. A response older than what
 * the store already holds (e.g. a newer WebSocket broadcast arrived first) must be
 * ignored; a newer one must be applied.
 */
import { act, fireEvent, render, screen } from '@testing-library/react';
import ThreadPanel from '@/components/chat/ThreadPanel';
import DrawerChatView from '@/components/notifications/drawer/DrawerChatView';
import { addReaction, removeReaction } from '@/lib/api/chatApi';
import { useChatStore } from '@/lib/chatStore';
import type { Message, Reaction } from '@/types/chat';
import type { NotificationItem } from '@/types/notifications';

jest.mock('@/lib/api/chatApi', () => ({
  addReaction: jest.fn(),
  removeReaction: jest.fn(),
  editMessage: jest.fn(),
  deleteMessage: jest.fn(),
  setMessageReminder: jest.fn(),
  revokeMessage: jest.fn(),
  hideMessage: jest.fn(),
  forwardBatch: jest.fn(),
}));

// Stub out pieces unrelated to reactions so the components render in jsdom.
jest.mock('@/components/chat/ChatComposer', () => () => null);
jest.mock('@/hooks/useThreadData', () => ({
  useThreadData: () => ({ isLoading: false, isSending: false, sendReply: jest.fn() }),
}));
// The real hover menu opens an emoji picker; this stub reacts with 👍 in one click.
jest.mock('@/components/chat/MessageHoverActions', () => ({
  __esModule: true,
  default: ({ onEmojiReaction }: { onEmojiReaction: (emoji: string) => void }) => (
    <button onClick={() => onEmojiReaction('👍')}>hover-react</button>
  ),
}));

// Drawer: replace its data hooks and children; keep its own reaction handler real.
jest.mock('@/hooks/useDrawerChat', () => ({
  useDrawerChat: () => ({
    chat: null,
    messages: [],
    isLoading: false,
    isLoadingMessages: false,
    error: null,
    sendRich: jest.fn(),
    isSending: false,
    highlightMessageId: null,
    currentUserId: 1,
    hasMore: false,
    isLoadingMore: false,
    loadMoreMessages: jest.fn(),
    removeLocalMessage: jest.fn(),
  }),
}));
jest.mock('@/hooks/useChatData', () => ({ useChatData: () => ({}) }));
jest.mock('@/hooks/useProjectMembers', () => ({ useProjectMembers: () => ({ members: [], isLoading: false }) }));
jest.mock('@/hooks/useChatWebSocket', () => ({
  useChatWebSocket: () => ({ connected: true, sendTypingStart: jest.fn(), sendTypingStop: jest.fn() }),
}));
jest.mock('@/lib/api/notificationsApi', () => ({ notificationsApi: { markAsRead: jest.fn().mockResolvedValue({}) } }));
jest.mock('@/components/notifications/drawer/DrawerChatHeader', () => () => null);
jest.mock('@/components/chat/ReminderPickerSheet', () => () => null);
jest.mock('@/components/chat/ForwardMessageSheet', () => () => null);
jest.mock('@/components/notifications/drawer/DrawerChatMessages', () => {
  const { forwardRef } = jest.requireActual('react');
  return {
    __esModule: true,
    default: forwardRef(
      ({ onEmojiReaction }: { onEmojiReaction: (id: number, emoji: string) => void }, _ref: unknown) => (
        <button onClick={() => onEmojiReaction(10, '👍')}>drawer-react</button>
      ),
    ),
  };
});

const ME = { id: 1, username: 'me' };
const TEAMMATE = { id: 5, username: 'teammate' };
const CHAT_ID = 2;

const message = (id: number, reactions: Reaction[], extra: Partial<Message> = {}): Message => ({
  id,
  chat_id: CHAT_ID,
  sender: { id: TEAMMATE.id, username: TEAMMATE.username, email: 't@example.com' },
  content: `message ${id}`,
  created_at: '2026-11-27T00:00:00.000Z',
  updated_at: '2026-11-27T00:00:00.000Z',
  reactions,
  reactions_version: 200, // what the store already holds
  ...extra,
});

// Starting state and server response for each action. 👍 is the only emoji.
const CASES = {
  add: {
    held: [{ emoji: '👍', count: 1, users: [TEAMMATE], reacted_by_me: false }],
    response: [{ emoji: '👍', count: 2, users: [TEAMMATE, ME] }],
  },
  remove: {
    held: [{ emoji: '👍', count: 2, users: [TEAMMATE, ME], reacted_by_me: true }],
    response: [{ emoji: '👍', count: 1, users: [TEAMMATE] }],
  },
};
type Action = keyof typeof CASES;

/** Each entry point: put the message where that UI reads it, render, click, read it back. */
const ENTRY_POINTS = {
  'thread reply': {
    actions: ['add', 'remove'] as Action[],
    setup(held: Reaction[]) {
      const root = message(99, []);
      useChatStore.setState({ messages: { [CHAT_ID]: [root] }, threadReplies: { 99: [message(10, held, { parent_message_id: 99 })] } });
      render(<ThreadPanel rootMessage={root} currentUserId={ME.id} onClose={jest.fn()} />);
    },
    click: () => fireEvent.click(screen.getByRole('button', { name: /👍/ })), // the reaction pill on the reply
    read: () => useChatStore.getState().threadReplies[99][0],
  },
  'thread root': {
    actions: ['add', 'remove'] as Action[],
    setup(held: Reaction[]) {
      const root = message(10, held);
      useChatStore.setState({ messages: { [CHAT_ID]: [root] }, threadReplies: { 10: [] } });
      render(<ThreadPanel rootMessage={root} currentUserId={ME.id} onClose={jest.fn()} />);
    },
    // Hover first, in its own step, so the hover menu has rendered before the click.
    prepare: () => fireEvent.mouseEnter(screen.getByText('message 10')),
    click: () => fireEvent.click(screen.getByText('hover-react')),
    read: () => useChatStore.getState().messages[CHAT_ID][0],
  },
  drawer: {
    actions: ['add'] as Action[], // the drawer only calls addReaction (the backend toggles)
    setup(held: Reaction[]) {
      useChatStore.setState({ messages: { [CHAT_ID]: [message(10, held)] }, threadReplies: {} });
      const notification = { id: 1, is_read: true, metadata: { chat_id: CHAT_ID, message_id: 10 } } as unknown as NotificationItem;
      render(<DrawerChatView notification={notification} onClose={jest.fn()} />);
    },
    click: () => fireEvent.click(screen.getByText('drawer-react')),
    read: () => useChatStore.getState().messages[CHAT_ID][0],
  },
};

const rows = Object.entries(ENTRY_POINTS).flatMap(([name, entry]) =>
  entry.actions.map((action) => [name, action] as [keyof typeof ENTRY_POINTS, Action]),
);

describe('reaction API responses are applied as versioned snapshots', () => {
  beforeAll(() => {
    Element.prototype.scrollIntoView = jest.fn(); // jsdom has no scrolling; ThreadPanel scrolls to the latest reply
  });

  beforeEach(() => {
    jest.clearAllMocks();
    useChatStore.setState({ messages: {}, threadReplies: {} });
  });

  describe.each(rows)('%s – %s', (entryName, action) => {
    const entry = ENTRY_POINTS[entryName];
    const { held, response } = CASES[action];
    const api = (action === 'add' || entryName === 'drawer' ? addReaction : removeReaction) as jest.Mock;

    async function clickWithResponseVersion(version: number) {
      api.mockResolvedValue({ status: action, message: { id: 10, reactions: response }, reactions_version: version });
      entry.setup(held);
      if ('prepare' in entry) entry.prepare();
      await act(async () => entry.click());
      expect(api).toHaveBeenCalledWith(10, '👍');
    }

    it('applies a response newer than the store', async () => {
      await clickWithResponseVersion(300);
      expect(entry.read().reactions).toEqual(
        response.map((r) => ({ ...r, reacted_by_me: r.users.some((u) => u.id === ME.id) })),
      );
      expect(entry.read().reactions_version).toBe(300);
    });

    it('ignores a response older than the store', async () => {
      await clickWithResponseVersion(150);
      expect(entry.read().reactions).toEqual(held);
      expect(entry.read().reactions_version).toBe(200);
    });
  });
});
