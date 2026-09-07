import { type Page, expect } from '@playwright/test';

type MessagesProjectSeed = {
	id: number;
	name: string;
	[key: string]: unknown;
};

type MessagesUserSeed = {
	id: number;
	email: string;
	username: string;
	roles?: string[];
	[key: string]: unknown;
};

export function isChatListEndpoint(url: string): boolean {
	const pathname = new URL(url).pathname.replace(/\/+$/, '');
	return pathname === '/api/chat/chats';
}

export const DEFAULT_MESSAGES_E2E_USER: MessagesUserSeed = {
	id: 1,
	email: 'e2e@example.com',
	username: 'e2e-user',
	is_verified: true,
	is_staff: false,
	roles: ['Media Buyer'],
};

export async function seedAuthenticatedUser(
	page: Page,
	user: MessagesUserSeed = DEFAULT_MESSAGES_E2E_USER
) {
	await page.addInitScript((authUser) => {
		window.localStorage.setItem(
			'auth-storage-v1',
			JSON.stringify({
				state: {
					token: 'e2e-access-token',
					refreshToken: 'e2e-refresh-token',
					organizationAccessToken: 'e2e-organization-token',
					user: authUser,
					isAuthenticated: true,
					loading: false,
					initialized: true,
					hasHydrated: true,
					userTeams: [],
					selectedTeamId: null,
				},
				version: 0,
			})
		);
	}, user);
}

export async function mockAuthenticatedUserApis(
	page: Page,
	user: MessagesUserSeed = DEFAULT_MESSAGES_E2E_USER
) {
	await page.route('**/auth/me/', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify(user),
		});
	});

	await page.route('**/auth/me/teams/', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ team_ids: [] }),
		});
	});
}

export async function mockProjectShellApis(page: Page) {
	await page.route('**/api/core/onboarding-status/**', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({
				needs_onboarding: false,
				has_org: true,
				has_project: true,
			}),
		});
	});

	await page.route('**/api/core/projects**', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify([]),
		});
	});

	// Called on every Messages page mount — must be mocked to avoid hanging without a backend.
	await page.route('**/api/chat/messages/unread_count/**', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ unread_count: 0 }),
		});
	});

	// DashboardLayout mounts useNotificationSSE which immediately fetches this.
	// Without a mock, the fake e2e token returns 401 from the real backend,
	// triggering the axios interceptor's hard redirect to /login.
	await page.route('**/api/notifications/**', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ count: 0, next: null, previous: null, results: [] }),
		});
	});

	await page.route('**/api/csm/notifications**', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ count: 0, next: null, previous: null, results: [] }),
		});
	});

	await page.route('**/api/chat/starred/**', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify([]),
		});
	});

	await page.route('**/api/chat/saved/**', async (route) => {
		const method = route.request().method();
		if (method === 'GET') {
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({ count: 0, next: null, previous: null, results: [] }),
			});
			return;
		}
		if (method === 'DELETE') {
			await route.fulfill({ status: 204, body: '' });
			return;
		}
		await route.fallback();
	});

	await page.route('**/api/chat/scheduled/**', async (route) => {
		const method = route.request().method();
		if (method === 'GET') {
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify({ count: 0, next: null, previous: null, results: [] }),
			});
			return;
		}
		await route.fallback();
	});

	await page.route('**/api/core/invitations/pending**', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify([]),
		});
	});

	await page.route('**/api/projects/*/meetings**', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ count: 0, next: null, previous: null, results: [] }),
		});
	});

	await page.route('**/api/dashboard/summary**', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({}),
		});
	});

	await page.route('**/api/decisions**', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ count: 0, next: null, previous: null, results: [] }),
		});
	});

	await page.route('**/api/campaigns**', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify([]),
		});
	});

	await page.route('**/api/assets**', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ results: [] }),
		});
	});

	await page.route('**/api/alerting/alert-tasks**', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ results: [] }),
		});
	});

	await page.route('**/api/core/projects/*/members/**', async (route) => {
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ results: [], next: null }),
		});
	});

	await page.route('**/api/chat/chats/**', async (route) => {
		const pathname = new URL(route.request().url()).pathname;
		if (/\/api\/chat\/chats\/[^/]+\/pins\/?$/.test(pathname)) {
			await route.fulfill({
				status: 200,
				contentType: 'application/json',
				body: JSON.stringify([]),
			});
			return;
		}
		if (!isChatListEndpoint(route.request().url())) {
			await route.fallback();
			return;
		}
		if (route.request().method() !== 'GET') {
			await route.fallback();
			return;
		}
		await route.fulfill({
			status: 200,
			contentType: 'application/json',
			body: JSON.stringify({ count: 0, next: null, previous: null, results: [] }),
		});
	});
}

export async function seedActiveProject(page: Page, project: MessagesProjectSeed) {
	await page.context().addCookies([
		{
			name: 'active-project',
			value: JSON.stringify(project),
			url: process.env.BASE_URL || 'http://localhost:3000',
		},
	]);

	await page.addInitScript((activeProject) => {
		window.localStorage.setItem(
			'project-storage-v1',
			JSON.stringify({
				state: {
					activeProject,
					activeProjectIds: [activeProject.id],
					inactiveProjectIds: [],
					completedProjectIds: [],
					hasHydrated: true,
					loading: false,
					error: null,
				},
				version: 0,
			})
		);
	}, project);
}

export async function clearProjectStore(page: Page) {
	await page.addInitScript(() => {
		window.localStorage.setItem(
			'project-storage-v1',
			JSON.stringify({
				state: {
					activeProject: null,
					activeProjectIds: [],
					inactiveProjectIds: [],
					completedProjectIds: [],
					hasHydrated: true,
					loading: false,
					error: null,
				},
				version: 0,
			})
		);
	});
}

export async function waitForLayoutMain(page: Page) {
	await page.setViewportSize({ width: 1280, height: 800});
	await page.waitForLoadState('domcontentloaded');

	await page
	    .getByText('Loading...', { exact: true})
		.waitFor({ state: 'hidden', timeout: 45_000 })
		.catch(() => {});

	await page.waitForURL((url) => !/\/(login|unauthorized)(\?|$)/i.test(url.pathname), {
		timeout: 20_000,
	});

	await expect(page.locator('body')).toBeAttached({ timeout: 15_000});
}

export function getMessagesHeader(page: Page) {
	return page.getByTestId('messages-header');
}

export function getProjectSelector(page: Page) {
	return getMessagesHeader(page);
}

export function getChatRows(page: Page) {
	return page.getByTestId('messages-chat-row');
}

export function getMessagesNewChatButton(page: Page) {
	return page.getByTestId('messages-new-chat');
}

export async function selectFirstProject(page: Page): Promise<boolean> {
	await expect(getMessagesHeader(page)).toBeVisible();
	const newChatButton = getMessagesNewChatButton(page);
	const newChatCount = await newChatButton.count();
	if (newChatCount > 0 && !(await newChatButton.first().isDisabled().catch(() => true))) {
		return true;
	}

	return !(await page.getByText('Select a project to view chats').isVisible().catch(() => false));
}

export async function assertChatListOrEmptyState(page: Page) {
	const chatRows = getChatRows(page);
	const noChatsState = page.getByText('No chats yet', { exact: true });
	const selectProjectHint = page.getByText('Select a project to view chats');
	const selectProjectStart = page.getByRole('heading', { name: 'Select a project to start' });
	const noDirectMessages = page.getByText('No direct messages yet', { exact: true });

	await expect
		.poll(async () => {
			const rows = await chatRows.count();
			const noChatsVisible = await noChatsState.isVisible().catch(() => false);
			const selectHintVisible = await selectProjectHint.isVisible().catch(() => false);
			const selectStartVisible = await selectProjectStart.isVisible().catch(() => false);
			const noDirectMessagesVisible = await noDirectMessages.isVisible().catch(() => false);
			return rows > 0 || noChatsVisible || selectHintVisible || selectStartVisible || noDirectMessagesVisible;
		}, { timeout: 20_000 })
		.toBeTruthy();
}

export async function openFirstChatIfPresent(page: Page) {
	const chatRows = getChatRows(page);
	const count = await chatRows.count();

	if (count === 0) {
		return false;
	}

	await chatRows.first().click();
	await expect(page.getByTestId('messages-chat-window')).toBeVisible({
		timeout: 15_000,
	});
	return true;
}

export async function trySendMessage(page: Page, content: string) {
	// ChatComposer uses a Tiptap contenteditable editor, not a textarea.
	// Use the data-testid wrapper to find it reliably.
	const composerWrapper = page.getByTestId('chat-composer-input');
	await expect(composerWrapper).toBeVisible({ timeout: 10_000 });
	const messageInput = composerWrapper.locator('[contenteditable]');
	await messageInput.click();
	// pressSequentially fires real keyboard events so ProseMirror updates its state.
	await messageInput.pressSequentially(content, { delay: 10 });
	// Wait for the Send button to become enabled once ProseMirror detects content.
	const sendButton = page.getByRole('button', { name: 'Send message' });
	await expect(sendButton).toBeEnabled({ timeout: 5_000 });
	await sendButton.click();

	await expect
		.poll(async () => {
			const sentVisible = await page.getByText(content, { exact: false }).isVisible().catch(() => false);
			const inputText = await messageInput.textContent().catch(() => content);
			return sentVisible || (inputText ?? '').trim().length === 0;
		}, { timeout: 15_000 })
		.toBeTruthy();
}
