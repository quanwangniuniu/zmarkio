import { test, expect } from '@playwright/test';
import {
  seedAuthenticatedUser,
  mockAuthenticatedUserApis,
  mockProjectShellApis,
  seedActiveProject,
  waitForLayoutMain,
  isChatListEndpoint,
} from './messages-helpers';

const PROJECT = { id: 932, name: 'Ordering Jitter Project', member_count: 2 };
const CHAT_ID = 632;

type JitterWindow = Window & {
  __emitChatEvent?: (event: Record<string, unknown>) => void;
};

const messagePayload = (id: number, seq: number, content: string) => ({
  id,
  seq,
  chat: CHAT_ID,
  chat_id: CHAT_ID,
  sender: { id: 2, username: 'teammate', email: 'tm@example.com' },
  content,
  created_at: `2026-08-05T00:00:0${seq}.000Z`,
  updated_at: `2026-08-05T00:00:0${seq}.000Z`,
  statuses: [],
  attachments: [],
});

test.beforeEach(async ({ page }) => {
  await seedAuthenticatedUser(page);
  await mockAuthenticatedUserApis(page);
  await mockProjectShellApis(page);
  await seedActiveProject(page, PROJECT);

  await page.addInitScript(() => {
    const sockets: Array<{
      onmessage: ((event: { data: string }) => void) | null;
    }> = [];

    class MockWebSocket extends EventTarget {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSING = 2;
      static CLOSED = 3;

      readyState = MockWebSocket.OPEN;
      onopen: ((event: Event) => void) | null = null;
      onclose: ((event: CloseEvent) => void) | null = null;
      onmessage: ((event: { data: string }) => void) | null = null;
      onerror: (() => void) | null = null;
      url: string;
      protocol = '';
      extensions = '';
      bufferedAmount = 0;
      binaryType: BinaryType = 'blob';

      constructor(url: string) {
        super();
        this.url = url;
        sockets.push(this);
        queueMicrotask(() => {
          const event = new Event('open');
          this.dispatchEvent(event);
          this.onopen?.(event);
        });
      }

      send() {}

      close() {
        this.readyState = MockWebSocket.CLOSED;
        const event = new CloseEvent('close', { code: 1000 });
        this.dispatchEvent(event);
        this.onclose?.(event);
      }
    }

    (window as JitterWindow).__emitChatEvent = (event) => {
      sockets.forEach((socket) => {
        socket.onmessage?.({ data: JSON.stringify(event) });
      });
    };

    // @ts-expect-error test shim
    window.WebSocket = MockWebSocket;
  });

  await page.route('**/api/core/projects**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([PROJECT]),
    });
  });

  await page.route(`**/api/core/projects/${PROJECT.id}/members/**`, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ results: [], next: null }),
    });
  });

  await page.route('**/api/chat/starred/**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
  });

  await page.route('**/api/chat/chats/**', async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (/\/api\/chat\/chats\/\d+\/pins\/?$/.test(pathname)) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
      return;
    }
    if (!isChatListEndpoint(route.request().url()) || route.request().method() !== 'GET') {
      await route.fallback();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        count: 1,
        next: null,
        previous: null,
        results: [{
          id: CHAT_ID,
          slug: 'ordering-jitter-chat',
          project_id: PROJECT.id,
          type: 'private',
          name: 'Ordering Jitter Chat',
          participants: [
            { id: 1, user: { id: 1, username: 'e2e-user', email: 'e2e@example.com' } },
            { id: 2, user: { id: 2, username: 'teammate', email: 'tm@example.com' } },
          ],
          last_message: messagePayload(9101, 1, 'sequence one'),
          unread_count: 0,
        }],
      }),
    });
  });

  await page.route(`**/api/chat/chats/${CHAT_ID}/mark_as_read/**`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
  });

  await page.route('**/api/chat/messages/**', async (route) => {
    const url = route.request().url();
    if (route.request().method() === 'GET' && url.includes('chat_id=')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          results: [messagePayload(9101, 1, 'sequence one')],
          next_cursor: null,
          prev_cursor: null,
          page_size: 50,
        }),
      });
      return;
    }
    await route.fallback();
  });
});

test('renders REST history and jittered websocket messages in sequence order', async ({ page }) => {
  await page.goto(`/messages?projectId=${PROJECT.id}`);
  await waitForLayoutMain(page);
  await page.getByRole('button', { name: /teammate.*sequence one/i }).click();
  await expect(page.locator('[data-message-id="9101"]')).toBeVisible({ timeout: 15_000 });

  await page.evaluate(
    async ({ chatId }) => {
      const emit = (window as JitterWindow).__emitChatEvent;
      if (!emit) throw new Error('Mock websocket emitter was not installed');

      const makeMessage = (id: number, seq: number, content: string) => ({
        type: 'chat_message',
        message: {
          id,
          seq,
          chat: chatId,
          chat_id: chatId,
          sender: { id: 2, username: 'teammate', email: 'tm@example.com' },
          content,
          created_at: `2026-08-05T00:00:0${seq}.000Z`,
          updated_at: `2026-08-05T00:00:0${seq}.000Z`,
          statuses: [],
          attachments: [],
        },
      });

      for (const event of [
        makeMessage(9104, 4, 'sequence four'),
        makeMessage(9102, 2, 'sequence two'),
        makeMessage(9103, 3, 'sequence three'),
      ]) {
        emit(event);
        await new Promise((resolve) => setTimeout(resolve, 40));
      }
    },
    { chatId: CHAT_ID },
  );

  await expect.poll(async () => (
    page.locator('[data-message-id]').evaluateAll((elements) =>
      elements.map((element) => element.getAttribute('data-message-id')),
    )
  )).toEqual(['9101', '9102', '9103', '9104']);
});
