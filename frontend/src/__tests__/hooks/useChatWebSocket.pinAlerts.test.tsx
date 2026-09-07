import { act, renderHook } from '@testing-library/react';
import { useChatWebSocket } from '@/hooks/useChatWebSocket';
import { useAuthStore } from '@/lib/authStore';
import { useChatStore } from '@/lib/chatStore';

class MockWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  static instances: MockWebSocket[] = [];

  readonly url: string;
  readyState = MockWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  send = jest.fn();

  open() {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.();
  }

  receive(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent);
  }

  close = jest.fn((code = 1000) => {
    this.readyState = MockWebSocket.CLOSED;
  });

  finishClose(code = 1000) {
    this.onclose?.({ code, reason: '' } as CloseEvent);
  }
}

describe('useChatWebSocket pin alerts', () => {
  const originalWebSocket = global.WebSocket;

  beforeEach(() => {
    MockWebSocket.instances = [];
    global.WebSocket = MockWebSocket as unknown as typeof WebSocket;
    useAuthStore.setState({ token: 'test-token' });
    useChatStore.setState({ unseenPinChatIds: {}, outbox: [] });
  });

  afterEach(() => {
    useAuthStore.setState({ token: null, user: null });
    useChatStore.setState({ unseenPinChatIds: {}, outbox: [] });
  });

  afterAll(() => {
    global.WebSocket = originalWebSocket;
  });

  it('marks the event channel before forwarding a pinned update', () => {
    const onPinUpdate = jest.fn();
    const { unmount } = renderHook(() => useChatWebSocket(100, { onPinUpdate }));

    expect(MockWebSocket.instances).toHaveLength(1);

    act(() => {
      MockWebSocket.instances[0].receive({
        type: 'pin_update',
        action: 'pinned',
        chat_id: 12,
        message_id: 44,
      });
    });

    expect(useChatStore.getState().unseenPinChatIds[12]).toBe(true);
    expect(onPinUpdate).toHaveBeenCalledWith(expect.objectContaining({ chat_id: 12 }));

    unmount();
  });

  it('does not create a new alert for an unpin update', () => {
    const { unmount } = renderHook(() => useChatWebSocket(100));

    act(() => {
      MockWebSocket.instances[0].receive({
        type: 'pin_update',
        action: 'unpinned',
        chat_id: 12,
        message_id: 44,
      });
    });

    expect(useChatStore.getState().unseenPinChatIds[12]).toBeUndefined();

    unmount();
  });

  it('does not let a stale close clear a replacement socket', () => {
    const { result, unmount } = renderHook(() => useChatWebSocket(100));
    const firstSocket = MockWebSocket.instances[0];

    act(() => {
      firstSocket.open();
      useAuthStore.setState({ token: 'replacement-token' });
    });

    expect(MockWebSocket.instances).toHaveLength(2);
    const replacementSocket = MockWebSocket.instances[1];
    act(() => {
      replacementSocket.open();
      firstSocket.finishClose(1001);
      result.current.sendTypingStart(12);
    });

    expect(replacementSocket.send).toHaveBeenCalledWith(
      JSON.stringify({ type: 'typing_start', chat_id: 12 }),
    );

    unmount();
  });
});
