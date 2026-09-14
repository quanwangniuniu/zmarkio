import { expect, test, type Page } from '@playwright/test';
import {
  mockAuthenticatedUserApis,
  mockProjectShellApis,
  seedActiveProject,
  seedAuthenticatedUser,
  waitForLayoutMain,
} from './messages-helpers';

const PROJECT = { id: 1, name: 'MED-234 Project' };
const MANAGER = { id: 1, email: 'manager@example.com', username: 'manager', is_verified: true, roles: [] };
const MEMBER = { id: 2, email: 'member@example.com', username: 'member', is_verified: true, roles: [] };
const CHAT_ID = 234;
const CHAT_SLUG = 'visibility-cache';
const CREATED_AT = '2026-09-12T08:00:00.000Z';

const chat = {
  id: CHAT_ID,
  slug: CHAT_SLUG,
  name: 'visibility-cache',
  type: 'group',
  project_id: PROJECT.id,
  created_by: MANAGER,
  created_by_id: MANAGER.id,
  created_at: CREATED_AT,
  updated_at: CREATED_AT,
  unread_count: 0,
  participants: [
    { id: 1, chat_id: CHAT_ID, user: MANAGER, joined_at: CREATED_AT, is_active: true, is_manager: true },
    { id: 2, chat_id: CHAT_ID, user: MEMBER, joined_at: CREATED_AT, is_active: true, is_manager: false },
  ],
  last_message: null,
};

async function installSocketHarness(page: Page) {
  await page.addInitScript(() => {
    const sockets: Array<{
      url: string;
      onmessage: ((event: MessageEvent) => void) | null;
      dispatchEvent: (event: Event) => boolean;
    }> = [];
    class TestWebSocket extends EventTarget {
      static CONNECTING = 0;
      static OPEN = 1;
      static CLOSING = 2;
      static CLOSED = 3;
      readyState = TestWebSocket.OPEN;
      onopen: (() => void) | null = null;
      onmessage: ((event: MessageEvent) => void) | null = null;
      onclose: ((event: CloseEvent) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;
      constructor(public url: string) {
        super();
        sockets.push(this);
        setTimeout(() => {
          const event = new Event('open');
          this.onopen?.();
          this.dispatchEvent(event);
        }, 0);
      }
      send() {}
      close() { this.readyState = TestWebSocket.CLOSED; }
    }
    Object.defineProperty(window, 'WebSocket', { value: TestWebSocket });
    const testWindow = window as typeof window & {
      emitChatEvent?: (value: unknown) => void;
      hasChatSocket?: () => boolean;
    };
    testWindow.hasChatSocket = () => sockets.some((socket) => socket.url.includes('/ws/chat/'));
    testWindow.emitChatEvent = (value) => {
      sockets.forEach((socket) => {
        const event = new MessageEvent('message', { data: JSON.stringify(value) });
        socket.onmessage?.(event);
        socket.dispatchEvent(event);
      });
    };
  });
}

async function setupMemberPage(page: Page) {
  await installSocketHarness(page);
  await seedAuthenticatedUser(page, MEMBER);
  await mockAuthenticatedUserApis(page, MEMBER);
  await mockProjectShellApis(page);
  await seedActiveProject(page, PROJECT);
  await page.route('**/api/core/projects**', (route) => route.fulfill({ json: [PROJECT] }));
  await page.route('**/api/chat/chats/**', async (route) => {
    const pathname = new URL(route.request().url()).pathname.replace(/\/+$/, '');
    if (pathname === '/api/chat/chats') {
      await route.fulfill({ json: { count: 1, next: null, previous: null, results: [chat] } });
      return;
    }
    if (pathname === `/api/chat/chats/${CHAT_ID}`) {
      await route.fulfill({ json: chat });
      return;
    }
    await route.fallback();
  });
  await page.route('**/api/chat/messages/**', (route) => route.fulfill({
    json: { results: [], next_cursor: null, prev_cursor: null, page_size: 50 },
  }));
  await page.goto('/messages');
  await waitForLayoutMain(page);
  const chatRow = page.getByTestId('messages-chat-row');
  await expect(chatRow).toHaveCount(1);
  await chatRow.click();
  await page.waitForFunction(() => {
    const testWindow = window as typeof window & { hasChatSocket?: () => boolean };
    return testWindow.hasChatSocket?.() === true;
  });
}

test('a manager removal revokes the other user mid-session within one second', async ({ browser, baseURL }) => {
  const managerContext = await browser.newContext({ baseURL });
  const memberContext = await browser.newContext({ baseURL });
  const managerPage = await managerContext.newPage();
  const memberPage = await memberContext.newPage();

  try {
    await setupMemberPage(memberPage);
    await seedAuthenticatedUser(managerPage, MANAGER);
    await managerPage.route(`**/api/chat/chats/${CHAT_SLUG}/remove_participant/**`, async (route) => {
      await route.fulfill({ status: 204, body: '' });
      await memberPage.evaluate((chatId) => {
        (window as typeof window & { emitChatEvent?: (value: unknown) => void }).emitChatEvent?.({
          type: 'chat_access_revoked', chat_id: chatId, reason: 'participant_removed',
        });
      }, CHAT_ID);
    });
    await managerPage.goto('/');
    await managerPage.evaluate(async ({ chatSlug, userId }) => {
      await fetch(`/api/chat/chats/${chatSlug}/remove_participant/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: userId }),
      });
    }, { chatSlug: CHAT_SLUG, userId: MEMBER.id });

    await expect(memberPage.getByText('You were removed from this chat')).toBeVisible({ timeout: 1_000 });
    await expect(memberPage.getByTestId('messages-chat-row')).toHaveCount(0);
  } finally {
    await Promise.all([managerContext.close(), memberContext.close()]);
  }
});
