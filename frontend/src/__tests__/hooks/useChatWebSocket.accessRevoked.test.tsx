import { act, renderHook, waitFor } from '@testing-library/react';
import toast from 'react-hot-toast';
import { useChatWebSocket } from '@/hooks/useChatWebSocket';
import { useAuthStore } from '@/lib/authStore';
import { useChatStore } from '@/lib/chatStore';
import type { Chat, Message } from '@/types/chat';
import { getChat, getChats, resolveLegacyChatSlug } from '@/lib/api/chatApi';

jest.mock('@/lib/api/chatApi', () => ({
  getChat: jest.fn(),
  getChats: jest.fn(),
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
    (getChats as jest.Mock).mockResolvedValue({
      count: 1, next: null, previous: null, results: [grantedChat],
    });
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
      expect(useChatStore.getState().chatsByProject[1]).toContainEqual(grantedChat);
      expect(useChatStore.getState().chatsByProject['med-234-project']).toContainEqual(grantedChat);
    });
    expect(getChats).toHaveBeenCalledWith({ project_id: 1, limit: 100 });
    expect(resolveLegacyChatSlug).not.toHaveBeenCalled();
    expect(getChat).not.toHaveBeenCalled();
    expect(onChatAccessGranted).toHaveBeenCalledWith(expect.objectContaining({ chat_id: 13 }));
    expect(MockWebSocket.instances[0].close).not.toHaveBeenCalled();
    unmount();
  });
});
