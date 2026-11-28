import { test, expect, type Browser, type Page } from '@playwright/test';
import {
  installApiMockSafetyNet,
  seedAuthenticatedUser,
  mockAuthenticatedUserApis,
  mockProjectShellApis,
  seedActiveProject,
  waitForLayoutMain,
  isChatListEndpoint,
} from './messages-helpers';

/**
 * MED-277: two users in two browser windows watch the same message.
 * The backend is faked inside this test: it keeps the reactions and, after
 * every change, pushes a reaction_update snapshot (full list + version) to
 * both windows through a fake WebSocket — the shape the real backend sends.
 */

const PROJECT = { id: 933, name: 'Reaction Sync Project' };
const CHAT_ID = 633;
const MESSAGE_ID = 9301;
const ALICE = { id: 1, email: 'alice@example.com', username: 'alice', is_verified: true, roles: [] };
const BOB = { id: 2, email: 'bob@example.com', username: 'bob', is_verified: true, roles: [] };

type User = typeof ALICE;
type Reaction = { emoji: string; count: number; users: { id: number; username: string }[] };

/** Shared fake backend: who reacted with 👍, a version counter, and the open windows. */
function createFakeServer() {
  return { thumbsUp: [{ id: BOB.id, username: BOB.username }], version: 100, pages: [] as Page[] };
}
type FakeServer = ReturnType<typeof createFakeServer>;

const snapshotOf = (server: FakeServer): Reaction[] =>
  server.thumbsUp.length ? [{ emoji: '👍', count: server.thumbsUp.length, users: server.thumbsUp }] : [];

/** Deliver a reaction_update into one window, as if it came over the WebSocket. */
async function pushSnapshot(page: Page, reactions: Reaction[], version: number) {
  const event = {
    type: 'reaction_update',
    reaction: { message_id: MESSAGE_ID, chat_id: CHAT_ID, reactions, reactions_version: version },
  };
  await page.evaluate((e) => (window as any).__emitChatEvent(e), event);
}

/**
 * Replace the browser's WebSocket with a fake one the test can push messages into.
 * Extends EventTarget because Next.js dev hot-reload also opens a WebSocket and calls addEventListener on it.
 */
function installFakeWebSocket() {
  const sockets: any[] = [];
  class FakeWebSocket extends EventTarget {
    static OPEN = 1;
    readyState = 1;
    onopen: ((e: Event) => void) | null = null;
    onmessage: ((e: { data: string }) => void) | null = null;
    onclose: ((e: CloseEvent) => void) | null = null;
    onerror: (() => void) | null = null;
    constructor(public url: string) {
      super();
      sockets.push(this);
      queueMicrotask(() => this.onopen?.(new Event('open')));
    }
    send() {}
    close() {
      this.readyState = 3;
    }
  }
  (window as any).__emitChatEvent = (event: unknown) =>
    sockets.forEach((s) => s.onmessage?.({ data: JSON.stringify(event) }));
  (window as any).WebSocket = FakeWebSocket;
}

async function openWindowAs(browser: Browser, user: User, server: FakeServer) {
  const context = await browser.newContext();
  const page = await context.newPage();

  await installApiMockSafetyNet(page); // any API not mocked below gets a 404 instead of hitting the real backend
  await seedAuthenticatedUser(page, user);
  await mockAuthenticatedUserApis(page, user);
  await mockProjectShellApis(page);
  await seedActiveProject(page, PROJECT);
  await page.addInitScript(installFakeWebSocket);

  const message = () => ({
    id: MESSAGE_ID,
    chat: CHAT_ID,
    chat_id: CHAT_ID,
    content: 'React to this message',
    created_at: '2026-11-27T10:00:00.000Z',
    updated_at: '2026-11-27T10:00:00.000Z',
    sender: ALICE,
    statuses: [],
    attachments: [],
    reactions: snapshotOf(server).map((r) => ({ ...r, reacted_by_me: r.users.some((u) => u.id === user.id) })),
    reactions_version: server.version,
  });

  await page.route('**/api/core/projects**', (route) => route.fulfill({ json: [PROJECT] }));

  await page.route('**/api/chat/chats/**', async (route) => {
    if (!isChatListEndpoint(route.request().url())) return route.fallback();
    await route.fulfill({
      json: {
        results: [{
          id: CHAT_ID,
          slug: 'reaction-sync',
          name: 'reaction-sync',
          type: 'group',
          project_id: PROJECT.id,
          participants: [ALICE, BOB].map((u) => ({ id: u.id, user: u, is_active: true })),
          unread_count: 0,
        }],
      },
    });
  });

  await page.route('**/api/chat/messages/**', async (route) => {
    const req = route.request();
    const pathname = new URL(req.url()).pathname.replace(/\/+$/, '');

    if (pathname === '/api/chat/messages' && req.method() === 'GET') {
      return route.fulfill({ json: { results: [message()], next_cursor: null, prev_cursor: null } });
    }

    // POST .../react/ adds my 👍, DELETE .../react/👍/ removes it — then broadcast, like the real backend.
    if (pathname.endsWith('/react') || pathname.includes('/react/')) {
      const adding = req.method() === 'POST';
      server.thumbsUp = server.thumbsUp.filter((u) => u.id !== user.id);
      if (adding) server.thumbsUp.push({ id: user.id, username: user.username });
      server.version += 1;

      await route.fulfill({
        json: { status: adding ? 'added' : 'removed', message: message(), reactions_version: server.version },
      });
      for (const p of server.pages) await pushSnapshot(p, snapshotOf(server), server.version);
      return;
    }

    await route.fallback();
  });

  await page.goto(`/messages?projectId=${PROJECT.id}&chatId=${CHAT_ID}`);
  await waitForLayoutMain(page);
  await expect(page.locator(`#message-${MESSAGE_ID}`)).toBeVisible({ timeout: 15_000 });

  server.pages.push(page);
  return page;
}

/** The "👍 N" button under the message. */
const thumbsUpPill = (page: Page) =>
  page.locator(`#message-${MESSAGE_ID}`).getByRole('button').filter({ hasText: '👍' });

test.describe('Reaction sync between two users', () => {
  test('adding and removing a reaction shows up in the other window without a refresh', async ({ browser }) => {
    const server = createFakeServer(); // starts with 👍 from bob
    const alice = await openWindowAs(browser, ALICE, server);
    const bob = await openWindowAs(browser, BOB, server);
    await expect(thumbsUpPill(alice)).toContainText('1');

    // alice adds her 👍 → both windows show 2, blue (mine) for both
    await thumbsUpPill(alice).click();
    await expect(thumbsUpPill(bob)).toContainText('2');
    await expect(thumbsUpPill(alice)).toHaveClass(/bg-blue-100/);
    await expect(thumbsUpPill(bob)).toHaveClass(/bg-blue-100/);

    // bob removes his 👍 → both windows show 1, blue only for alice
    await thumbsUpPill(bob).click();
    await expect(thumbsUpPill(alice)).toContainText('1');
    await expect(thumbsUpPill(bob)).toContainText('1');
    await expect(thumbsUpPill(alice)).toHaveClass(/bg-blue-100/);
    await expect(thumbsUpPill(bob)).toHaveClass(/bg-gray-100/);
  });

  test('a late, older snapshot does not overwrite a newer one', async ({ browser }) => {
    const server = createFakeServer(); // the page loads with version 100
    const bob = await openWindowAs(browser, BOB, server);
    const both = [{ id: ALICE.id, username: ALICE.username }, { id: BOB.id, username: BOB.username }];

    await pushSnapshot(bob, [{ emoji: '👍', count: 2, users: both }], 200);
    await expect(thumbsUpPill(bob)).toContainText('2');

    await pushSnapshot(bob, [{ emoji: '👍', count: 1, users: [both[0]] }], 150); // older, arrives late
    await bob.waitForTimeout(500); // give the page time to (wrongly) apply it, so the check below means something
    await expect(thumbsUpPill(bob)).toContainText('2');
  });
});
