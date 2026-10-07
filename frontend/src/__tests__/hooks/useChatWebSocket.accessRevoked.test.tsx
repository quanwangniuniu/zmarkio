import { act, render, renderHook, waitFor } from '@testing-library/react';
import toast from 'react-hot-toast';
import { ChatWebSocketProvider, useChatWebSocket } from '@/hooks/useChatWebSocket';
import { useAuthStore } from '@/lib/authStore';
import { useChatStore } from '@/lib/chatStore';
import type { Chat, Message } from '@/types/chat';
import { getChat, resolveLegacyChatSlug } from '@/lib/api/chatApi';

jest.mock('@/lib/api/chatApi', () => ({
  getChat: jest.fn(),
  resolveLegacyChatSlug: jest.fn(),
}));

jest.mock('react-hot-toast', () => ({
  __esModule: true,
  default: { error: jest.fn() },
}));

class MockWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  static instances: MockWebSocket[] = [];
  readyState = MockWebSocket.OPEN;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  send = jest.fn();
  close = jest.fn();

  constructor(readonly url: string) {
    MockWebSocket.instances.push(this);
  }

  receive(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent);
  }
}

describe('useChatWebSocket access revocation', () => {
  const originalWebSocket = global.WebSocket;

  beforeEach(() => {
    MockWebSocket.instances = [];
    global.WebSocket = MockWebSocket as unknown as typeof WebSocket;
    useAuthStore.setState({ token: 'test-token' });
    useChatStore.setState({
      chatsByProject: { 1: [{ id: 12, project_id: 1 } as Chat] },
      messages: { 12: [{ id: 44, chat_id: 12 } as Message] },
      unreadCounts: { 12: 3 },
      globalUnreadCount: 3,
      currentChatId: 12,
      currentView: 'chat',
      outbox: [],
    });
  });

  afterEach(() => {
    useAuthStore.setState({ token: null, user: null });
    useChatStore.setState({
      chatsByProject: {}, messages: {}, unreadCounts: {}, globalUnreadCount: 0,
      currentChatId: null, currentView: 'list', outbox: [],
    });
    jest.clearAllMocks();
  });

  afterAll(() => {
    global.WebSocket = originalWebSocket;
  });

  it('removes the revoked room without closing the shared socket', () => {
    const onChatAccessRevoked = jest.fn();
    const { unmount } = renderHook(() => useChatWebSocket(100, { onChatAccessRevoked }));

    act(() => {
      MockWebSocket.instances[0].receive({
        type: 'chat_access_revoked', chat_id: 12, reason: 'participant_removed',
      });
    });

    const state = useChatStore.getState();
    expect(state.chatsByProject[1]).toEqual([]);
    expect(state.messages[12]).toBeUndefined();
    expect(state.currentChatId).toBeNull();
    expect(state.globalUnreadCount).toBe(0);
    expect(MockWebSocket.instances[0].close).not.toHaveBeenCalled();
    expect(toast.error).toHaveBeenCalledWith('You were removed from this chat', {
      id: 'chat-access-revoked-12',
    });
    expect(onChatAccessRevoked).toHaveBeenCalledWith(expect.objectContaining({ chat_id: 12 }));
    unmount();
  });

  it('adds a newly granted room without reconnecting', async () => {
    const grantedChat = { id: 13, slug: 'new-room', project_id: 1 } as Chat;
    (getChat as jest.Mock).mockResolvedValue(grantedChat);
    const onChatAccessGranted = jest.fn();
    const { unmount } = renderHook(() => useChatWebSocket(100, { onChatAccessGranted }));

    act(() => {
      MockWebSocket.instances[0].receive({
        type: 'chat_access_granted', chat_id: 13, chat_slug: 'new-room', project_id: 1,
        project_slug: 'med-234-project',
        reason: 'participant_added',
      });
    });

    await waitFor(() => {
      const expectedChat = expect.objectContaining({
        id: grantedChat.id,
        slug: grantedChat.slug,
        project_id: grantedChat.project_id,
      });
      expect(useChatStore.getState().chatsByProject[1]).toContainEqual(expectedChat);
      expect(useChatStore.getState().chatsByProject['med-234-project']).toContainEqual(expectedChat);
    });
    expect(getChat).toHaveBeenCalledWith('new-room');
    expect(resolveLegacyChatSlug).not.toHaveBeenCalled();
    expect(onChatAccessGranted).toHaveBeenCalledWith(expect.objectContaining({ chat_id: 13 }));
    expect(MockWebSocket.instances[0].close).not.toHaveBeenCalled();
    unmount();
  });

  it('does not restore a room when revoke arrives while the grant fetch is pending', async () => {
    let resolveChat!: (chat: Chat) => void;
    (getChat as jest.Mock).mockReturnValue(new Promise<Chat>((resolve) => {
      resolveChat = resolve;
    }));
    const { unmount } = renderHook(() => useChatWebSocket(100));

    act(() => {
      MockWebSocket.instances[0].receive({
        type: 'chat_access_granted', chat_id: 13, chat_slug: 'new-room', project_id: 1,
        project_slug: 'med-234-project', reason: 'participant_added',
      });
    });
    await waitFor(() => expect(getChat).toHaveBeenCalledWith('new-room'));

    act(() => {
      MockWebSocket.instances[0].receive({
        type: 'chat_access_revoked', chat_id: 13, reason: 'participant_removed',
      });
    });
    await act(async () => {
      resolveChat({ id: 13, slug: 'new-room', project_id: 1 } as Chat);
      await Promise.resolve();
    });

    expect(useChatStore.getState().chatsByProject[1]).not.toContainEqual(
      expect.objectContaining({ id: 13 }),
    );
    expect(useChatStore.getState().chatsByProject['med-234-project']).toBeUndefined();
    unmount();
  });

  it('shares one socket between consumers under the project provider', () => {
    function Consumer() {
      useChatWebSocket(100);
      return null;
    }

    const view = render(
      <ChatWebSocketProvider userId={100}>
        <Consumer />
        <Consumer />
      </ChatWebSocketProvider>,
    );

    expect(MockWebSocket.instances).toHaveLength(1);
    view.unmount();
  });
});
