import { act, renderHook } from '@testing-library/react';
import toast from 'react-hot-toast';
import { useChatWebSocket } from '@/hooks/useChatWebSocket';
import { useAuthStore } from '@/lib/authStore';
import { useChatStore } from '@/lib/chatStore';
import type { Chat, Message } from '@/types/chat';

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
});
