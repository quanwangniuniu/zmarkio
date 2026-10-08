/**
 * useNotificationSSE – unit tests
 *
 * Approach
 * --------
 *  • EventSource is replaced with a jest mock so we can capture handler
 *    assignments and simulate incoming messages / errors without a real HTTP
 *    server.
 *  • Zustand stores are mocked via jest.mock + runtime configuration so we can
 *    assert that the correct store actions are dispatched without running the
 *    real stores.
 *  • notificationsApi.list is mocked to avoid real HTTP calls.
 *  • jest.useFakeTimers() is used only in the reconnect/back-off suite to
 *    control setTimeout without sleeping for real seconds.
 */

import { renderHook, act } from '@testing-library/react';
import { useNotificationSSE } from '../useNotificationSSE';

// ─── Module mocks (hoisted by Jest before imports) ────────────────────────────

jest.mock('@/lib/authStore', () => ({
  useAuthStore: jest.fn(),
}));

jest.mock('@/lib/notificationStore', () => ({
  // Zustand stores are both a hook and a static object with .getState().
  useNotificationStore: Object.assign(jest.fn(), {
    getState: jest.fn(),
  }),
}));

jest.mock('@/lib/chatStore', () => ({
  useChatStore: Object.assign(jest.fn(), {
    getState: jest.fn(),
  }),
}));

// Only the token helpers are replaced; tests decide whether a token is expiring.
jest.mock('@/lib/api', () => ({
  ...jest.requireActual('@/lib/api'),
  isAccessTokenExpiring: jest.fn(),
  getValidAccessToken: jest.fn(),
}));

jest.mock('@/lib/api/notificationsApi', () => ({
  notificationsApi: {
    list: jest.fn().mockResolvedValue({ data: { unread_count: 3 } }),
  },
}));

// ─── Import mocked modules (received after factory transforms above) ──────────
import { useAuthStore } from '@/lib/authStore';
import { useNotificationStore } from '@/lib/notificationStore';
import { useChatStore } from '@/lib/chatStore';
import { notificationsApi } from '@/lib/api/notificationsApi';
import * as authApi from '@/lib/api';

// ─── EventSource mock ─────────────────────────────────────────────────────────

interface MockES {
  url: string;
  onopen: ((e: Event) => void) | null;
  onmessage: ((e: MessageEvent) => void) | null;
  onerror: ((e: Event) => void) | null;
  close: jest.Mock;
}

/** The most recently created EventSource instance. */
let latestES: MockES | null = null;

const MockEventSource = jest.fn().mockImplementation((url: string): MockES => {
  latestES = { url, onopen: null, onmessage: null, onerror: null, close: jest.fn() };
  return latestES;
});

// ─── Helpers ──────────────────────────────────────────────────────────────────

function fireMessage(data: object, lastEventId = ''): void {
  act(() => {
    latestES?.onmessage?.({
      data: JSON.stringify(data),
      lastEventId,
    } as MessageEvent);
  });
}

function setVisibility(state: 'visible' | 'hidden'): void {
  Object.defineProperty(document, 'visibilityState', {
    value: state,
    configurable: true,
  });
  document.dispatchEvent(new Event('visibilitychange'));
}

// ─── Test suite ───────────────────────────────────────────────────────────────

describe('useNotificationSSE', () => {
  // Per-test mock action references configured in beforeEach.
  let mockTriggerRefresh: jest.Mock;
  let mockSetUnreadCount: jest.Mock;
  let mockSetConnectionStatus: jest.Mock;
  let mockTriggerChatActivity: jest.Mock;
  let mockIncrementGlobalUnreadCount: jest.Mock;

  beforeAll(() => {
    // Replace the browser's native EventSource for the whole suite.
    global.EventSource = MockEventSource as unknown as typeof EventSource;
  });

  beforeEach(() => {
    jest.clearAllMocks();
    latestES = null;
    (authApi.isAccessTokenExpiring as jest.Mock).mockReturnValue(false);

    // Default: authenticated user with a token.
    (useAuthStore as unknown as jest.Mock).mockImplementation(
      (selector: (s: { token: string }) => unknown) =>
        selector({ token: 'fake-jwt-token' }),
    );

    // Notification store
    mockTriggerRefresh = jest.fn();
    mockSetUnreadCount = jest.fn();
    mockSetConnectionStatus = jest.fn();
    (useNotificationStore.getState as jest.Mock).mockReturnValue({
      triggerRefresh: mockTriggerRefresh,
      setUnreadCount: mockSetUnreadCount,
      setConnectionStatus: mockSetConnectionStatus,
    });

    // Chat store
    mockTriggerChatActivity = jest.fn();
    mockIncrementGlobalUnreadCount = jest.fn();
    (useChatStore.getState as jest.Mock).mockReturnValue({
      triggerChatActivity: mockTriggerChatActivity,
      incrementGlobalUnreadCount: mockIncrementGlobalUnreadCount,
    });
  });

  afterEach(() => {
    // Restore visibility state to default so tests don't bleed into each other.
    Object.defineProperty(document, 'visibilityState', {
      value: 'visible',
      configurable: true,
    });
    jest.useRealTimers();
    jest.restoreAllMocks();
  });

  // ── 1. Connection setup ────────────────────────────────────────────────────

  describe('connection', () => {
    it('creates an EventSource with the JWT token in the URL query string', () => {
      renderHook(() => useNotificationSSE());

      expect(MockEventSource).toHaveBeenCalledTimes(1);
      const url: string = MockEventSource.mock.calls[0][0];
      expect(url).toContain('/api/notifications/stream/');
      expect(url).toContain('token=fake-jwt-token');
    });

    it('does NOT create an EventSource when the token is absent', () => {
      (useAuthStore as unknown as jest.Mock).mockImplementation(
        (selector: (s: { token: string | null }) => unknown) =>
          selector({ token: null }),
      );

      renderHook(() => useNotificationSSE());

      expect(MockEventSource).not.toHaveBeenCalled();
    });

    it('closes the EventSource and removes listeners on unmount', () => {
      const { unmount } = renderHook(() => useNotificationSSE());
      const closeMock = latestES!.close;

      unmount();

      expect(closeMock).toHaveBeenCalled();
    });

    it('appends lastEventId to the reconnect URL after a tracked event', () => {
      renderHook(() => useNotificationSSE());

      // Simulate an event that carries a Last-Event-ID.
      fireMessage(
        {
          type: 'notification',
          data: {
            created_at: '2026-08-22T10:00:00.000000Z',
            event_type: 'task_assigned',
          },
        },
        'aaaabbbb-0000-0000-0000-aabbccddeeff',
      );

      // Trigger a reconnect via visibility change (hidden → visible).
      act(() => setVisibility('hidden'));
      MockEventSource.mockClear();
      act(() => setVisibility('visible'));

      expect(MockEventSource).toHaveBeenCalledTimes(1);
      const reconnectUrl: string = MockEventSource.mock.calls[0][0];
      expect(reconnectUrl).toContain('lastEventId=');
      expect(reconnectUrl).toContain('aaaabbbb-0000-0000-0000-aabbccddeeff');
      expect(reconnectUrl).toContain('lastEventCreatedAt=');
      expect(decodeURIComponent(reconnectUrl)).toContain('2026-08-22T10:00:00.000000Z');
    });

    it('tracks connecting and connected health states', () => {
      renderHook(() => useNotificationSSE());

      expect(mockSetConnectionStatus).toHaveBeenCalledWith('connecting');
      act(() => latestES?.onopen?.(new Event('open')));
      expect(mockSetConnectionStatus).toHaveBeenLastCalledWith('connected');
    });

    it('sets disconnected health when no token is available', () => {
      (useAuthStore as unknown as jest.Mock).mockImplementation(
        (selector: (s: { token: string | null }) => unknown) => selector({ token: null }),
      );

      renderHook(() => useNotificationSSE());

      expect(mockSetConnectionStatus).toHaveBeenCalledWith('disconnected');
    });
  });

  // ── 2. Notification message handling ─────────────────────────────────────

  describe('notification event handling', () => {
    it('calls triggerRefresh when a notification message arrives', () => {
      renderHook(() => useNotificationSSE());

      fireMessage({ type: 'notification', data: { event_type: 'task_assigned' } });

      expect(mockTriggerRefresh).toHaveBeenCalledTimes(1);
    });

    it('calls notificationsApi.list to sync the unread badge count', async () => {
      renderHook(() => useNotificationSSE());

      await act(async () => {
        fireMessage({ type: 'notification', data: { event_type: 'task_assigned' } });
      });

      expect(notificationsApi.list).toHaveBeenCalled();
    });

    it('calls setUnreadCount with the count returned by the API', async () => {
      (notificationsApi.list as jest.Mock).mockResolvedValueOnce({
        data: { unread_count: 7 },
      });
      renderHook(() => useNotificationSSE());

      await act(async () => {
        fireMessage({ type: 'notification', data: { event_type: 'task_assigned' } });
      });

      expect(mockSetUnreadCount).toHaveBeenCalledWith(7);
    });

    it('silently ignores messages with an unrecognised type', () => {
      renderHook(() => useNotificationSSE());

      fireMessage({ type: 'heartbeat' });
      fireMessage({ type: 'unknown_event', data: {} });

      expect(mockTriggerRefresh).not.toHaveBeenCalled();
      expect(mockTriggerChatActivity).not.toHaveBeenCalled();
    });

    it('silently ignores malformed JSON without throwing', () => {
      renderHook(() => useNotificationSSE());

      act(() => {
        latestES?.onmessage?.({ data: '{ not valid json }}}', lastEventId: '' } as MessageEvent);
      });

      expect(mockTriggerRefresh).not.toHaveBeenCalled();
    });

    it('suppresses a replayed notification UUID already seen by this page', () => {
      renderHook(() => useNotificationSSE());
      const payload = {
        type: 'notification',
        data: {
          created_at: '2026-08-22T10:00:00.000000Z',
          event_type: 'task_assigned',
        },
      };

      fireMessage(payload, 'aaaabbbb-0000-0000-0000-aabbccddeeff');
      fireMessage(payload, 'aaaabbbb-0000-0000-0000-aabbccddeeff');

      expect(mockTriggerRefresh).toHaveBeenCalledTimes(1);
    });
  });

  // ── 3. Chat event handling ────────────────────────────────────────────────

  describe('chat event handling', () => {
    it('calls triggerChatActivity on a chat_new_message event', () => {
      renderHook(() => useNotificationSSE());

      fireMessage({ type: 'notification', data: { event_type: 'chat_new_message' } });

      expect(mockTriggerChatActivity).toHaveBeenCalledTimes(1);
    });

    it('calls incrementGlobalUnreadCount on a chat_new_message event', () => {
      renderHook(() => useNotificationSSE());

      fireMessage({ type: 'notification', data: { event_type: 'chat_new_message' } });

      expect(mockIncrementGlobalUnreadCount).toHaveBeenCalledTimes(1);
    });

    it('calls triggerChatActivity on a chat_new_conversation event', () => {
      renderHook(() => useNotificationSSE());

      fireMessage({ type: 'notification', data: { event_type: 'chat_new_conversation' } });

      expect(mockTriggerChatActivity).toHaveBeenCalledTimes(1);
    });

    it('does NOT call triggerChatActivity for non-chat event types', () => {
      renderHook(() => useNotificationSSE());

      fireMessage({ type: 'notification', data: { event_type: 'meeting_invited' } });
      fireMessage({ type: 'notification', data: { event_type: 'task_assigned' } });

      expect(mockTriggerChatActivity).not.toHaveBeenCalled();
      expect(mockIncrementGlobalUnreadCount).not.toHaveBeenCalled();
    });

    it('still calls triggerRefresh for chat events (bell badge must update too)', () => {
      renderHook(() => useNotificationSSE());

      fireMessage({ type: 'notification', data: { event_type: 'chat_new_message' } });

      expect(mockTriggerRefresh).toHaveBeenCalled();
      expect(mockTriggerChatActivity).toHaveBeenCalled();
    });
  });

  // ── 4. Page Visibility API ────────────────────────────────────────────────

  describe('Page Visibility API', () => {
    it('closes the EventSource when the tab is hidden', () => {
      renderHook(() => useNotificationSSE());
      const closeMock = latestES!.close;

      act(() => setVisibility('hidden'));

      expect(closeMock).toHaveBeenCalled();
      expect(mockSetConnectionStatus).toHaveBeenCalledWith('disconnected');
    });

    it('creates a new EventSource when the tab becomes visible again', () => {
      renderHook(() => useNotificationSSE());

      act(() => setVisibility('hidden'));
      MockEventSource.mockClear();
      act(() => setVisibility('visible'));

      expect(MockEventSource).toHaveBeenCalledTimes(1);
    });

    it('eagerly refreshes the unread count when the tab becomes visible', async () => {
      renderHook(() => useNotificationSSE());
      (notificationsApi.list as jest.Mock).mockClear();

      await act(async () => setVisibility('visible'));

      expect(notificationsApi.list).toHaveBeenCalled();
    });
  });

  // ── 5. Error handling and reconnect back-off ──────────────────────────────

  describe('error handling and reconnect back-off', () => {
    beforeEach(() => {
      jest.useFakeTimers();
    });

    it('closes the current EventSource immediately on onerror', () => {
      renderHook(() => useNotificationSSE());
      const closeMock = latestES!.close;

      act(() => {
        latestES?.onerror?.(new Event('error'));
      });

      expect(closeMock).toHaveBeenCalled();
      expect(mockSetConnectionStatus).toHaveBeenCalledWith('reconnecting');
    });

    it('does not reconnect synchronously – waits for the back-off timer', () => {
      renderHook(() => useNotificationSSE());
      MockEventSource.mockClear();

      act(() => {
        latestES?.onerror?.(new Event('error'));
      });

      // No immediate reconnect.
      expect(MockEventSource).not.toHaveBeenCalled();
    });

    it('creates a new EventSource after the MIN_RETRY_MS back-off elapses', () => {
      renderHook(() => useNotificationSSE());
      MockEventSource.mockClear();

      act(() => {
        latestES?.onerror?.(new Event('error'));
      });

      // Advance past the minimum retry delay (3 000 ms).
      act(() => {
        jest.advanceTimersByTime(4_000);
      });

      expect(MockEventSource).toHaveBeenCalledTimes(1);
    });

    it('adds jitter to the reconnect delay', () => {
      jest.spyOn(Math, 'random').mockReturnValue(0);
      renderHook(() => useNotificationSSE());
      MockEventSource.mockClear();

      act(() => {
        latestES?.onerror?.(new Event('error'));
      });

      // MIN_RETRY_MS with -20% jitter is 2,400 ms.
      act(() => {
        jest.advanceTimersByTime(2_399);
      });
      expect(MockEventSource).not.toHaveBeenCalled();

      act(() => {
        jest.advanceTimersByTime(1);
      });
      expect(MockEventSource).toHaveBeenCalledTimes(1);
    });

    it('cancels the pending retry timer when the component unmounts', () => {
      const { unmount } = renderHook(() => useNotificationSSE());

      act(() => {
        latestES?.onerror?.(new Event('error'));
      });
      MockEventSource.mockClear();

      unmount();

      // Fast-forward well past any back-off – no new connection must appear.
      act(() => {
        jest.advanceTimersByTime(60_000);
      });

      expect(MockEventSource).not.toHaveBeenCalled();
    });
  });

  // ── 6. Expiring token before connecting ───────────────────────────────────

  describe('refreshing an expiring token before connecting', () => {
    let storeToken: string;
    let mockSetToken: jest.Mock;
    let getValidAccessTokenSpy: jest.Mock;

    beforeEach(() => {
      storeToken = 'expiring-token';
      mockSetToken = jest.fn((t: string) => {
        storeToken = t;
      });
      (useAuthStore as unknown as jest.Mock).mockImplementation(
        (selector: (s: { token: string }) => unknown) => selector({ token: storeToken }),
      );
      (useAuthStore as unknown as { getState: () => unknown }).getState = () => ({
        setToken: mockSetToken,
      });
      // Every token looks expiring, as with a client clock far ahead of the server.
      (authApi.isAccessTokenExpiring as jest.Mock).mockReturnValue(true);
      getValidAccessTokenSpy = authApi.getValidAccessToken as jest.Mock;
    });

    it('refreshes once and connects with the new token even if it still looks expiring', async () => {
      getValidAccessTokenSpy.mockResolvedValue('refreshed-token');
      const { rerender } = renderHook(() => useNotificationSSE());
      expect(MockEventSource).not.toHaveBeenCalled();

      await act(async () => {});
      expect(mockSetToken).toHaveBeenCalledWith('refreshed-token');

      // The store update re-renders the hook with the new token.
      rerender();
      await act(async () => {});

      expect(getValidAccessTokenSpy).toHaveBeenCalledTimes(1);
      expect(MockEventSource).toHaveBeenCalledTimes(1);
      expect(MockEventSource.mock.calls[0][0]).toContain('token=refreshed-token');
    });

    it('connects directly when the refresh hands back the same token', async () => {
      getValidAccessTokenSpy.mockResolvedValue('expiring-token');
      renderHook(() => useNotificationSSE());

      await act(async () => {});

      expect(mockSetToken).not.toHaveBeenCalled();
      expect(MockEventSource).toHaveBeenCalledTimes(1);
      expect(MockEventSource.mock.calls[0][0]).toContain('token=expiring-token');
    });

    it('backs off and retries when the refresh fails', async () => {
      jest.useFakeTimers();
      getValidAccessTokenSpy.mockResolvedValue(null);
      renderHook(() => useNotificationSSE());

      await act(async () => {});
      expect(MockEventSource).not.toHaveBeenCalled();
      expect(mockSetConnectionStatus).toHaveBeenCalledWith('reconnecting');

      await act(async () => {
        jest.advanceTimersByTime(4_000);
      });
      expect(getValidAccessTokenSpy).toHaveBeenCalledTimes(2);
    });

    it('ignores a refresh that resolves after unmount', async () => {
      let resolveRefresh!: (t: string) => void;
      getValidAccessTokenSpy.mockReturnValue(
        new Promise<string>((r) => {
          resolveRefresh = r;
        }),
      );
      const { unmount } = renderHook(() => useNotificationSSE());
      unmount();

      await act(async () => {
        resolveRefresh('refreshed-token');
      });

      expect(mockSetToken).not.toHaveBeenCalled();
      expect(MockEventSource).not.toHaveBeenCalled();
    });
  });
});
