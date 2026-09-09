import fs from 'fs';
import path from 'path';
import { type APIRequestContext, type Page, expect } from '@playwright/test';
import { deleteTaskById } from '../../tasks/tasks-helpers';
import {
  BUDGET_E2E_DEFAULT_PASSWORD,
  BUDGET_E2E_EMAILS,
  resolveBudgetRbacHeaders,
  type BudgetE2EFixturePayload,
} from './budget-e2e-types';

const FIXTURE_FILE = path.join(__dirname, 'budget-e2e-fixtures.json');

let cachedFixtures: BudgetE2EFixturePayload | null | undefined;

export function loadBudgetE2EFixtures(): BudgetE2EFixturePayload | null {
  if (cachedFixtures !== undefined) {
    return cachedFixtures;
  }

  const fromEnv = process.env.E2E_BUDGET_FIXTURES;
  if (fromEnv && fs.existsSync(fromEnv)) {
    cachedFixtures = JSON.parse(fs.readFileSync(fromEnv, 'utf-8')) as BudgetE2EFixturePayload;
    return cachedFixtures;
  }

  if (fs.existsSync(FIXTURE_FILE)) {
    cachedFixtures = JSON.parse(fs.readFileSync(FIXTURE_FILE, 'utf-8')) as BudgetE2EFixturePayload;
    return cachedFixtures;
  }

  cachedFixtures = null;
  return null;
}

export function requireBudgetE2EFixtures(): BudgetE2EFixturePayload {
  const fixtures = loadBudgetE2EFixtures();
  if (!fixtures) {
    throw new Error(
      'Budget E2E fixtures not found. Run:\n' +
        '  docker compose -p mediajira-v2 -f docker-compose.dev.yml exec backend ' +
        'python manage.py issue_budget_e2e_fixtures > frontend/e2e/budget/fixtures/budget-e2e-fixtures.json',
    );
  }
  return fixtures;
}

export type BudgetTaskFixture = {
  id: number;
  slug: string;
  projectId: number;
  projectSlug: string;
  cleanup: () => Promise<void>;
};

function apiOrigin(page: Page): string {
  return process.env.BASE_URL || new URL(page.url()).origin || 'http://localhost';
}

function projectRef(
  fixtures: BudgetE2EFixturePayload,
  projectKind: 'chain' | 'single' = 'chain',
): { id: number; slug: string } {
  if (projectKind === 'single') {
    return {
      id: fixtures.single_approver_project_id,
      slug: fixtures.single_approver_project_slug,
    };
  }
  return { id: fixtures.project_id, slug: fixtures.project_slug };
}

async function ensureActiveProject(
  request: APIRequestContext,
  headers: Record<string, string>,
  project: { id: number; slug: string },
): Promise<void> {
  const origin = process.env.BASE_URL || 'http://localhost';
  for (const key of [project.slug, String(project.id)]) {
    const resp = await request.post(
      `${origin}/api/core/projects/${encodeURIComponent(key)}/set_active/`,
      { headers },
    );
    if (resp.ok()) {
      return;
    }
  }
  throw new Error(
    `Failed to set active project (id=${project.id}, slug=${project.slug}). ` +
      'Re-run issue_budget_e2e_fixtures if memberships are stale.',
  );
}

export async function apiAuthHeaders(
  request: APIRequestContext,
  email: string,
  password: string = BUDGET_E2E_DEFAULT_PASSWORD,
): Promise<Record<string, string>> {
  const origin = process.env.BASE_URL || 'http://localhost';
  const loginResp = await request.post(`${origin}/auth/login/`, {
    headers: { 'Content-Type': 'application/json' },
    data: { email, password },
  });
  if (!loginResp.ok()) {
    throw new Error(`Login failed for ${email} (${loginResp.status()}): ${await loginResp.text()}`);
  }
  const body = await loginResp.json();
  const token = body.token as string;
  const headers: Record<string, string> = {
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json',
  };
  if (body.organization_access_token) {
    headers['X-Organization-Token'] = body.organization_access_token;
  }

  const fixtures = loadBudgetE2EFixtures();
  if (fixtures) {
    Object.assign(headers, resolveBudgetRbacHeaders(fixtures, email));
  } else {
    const user = body.user as { roles?: string[]; team_id?: number };
    if (user?.roles?.length) {
      headers['x-user-role'] = user.roles[0];
    }
    if (user?.team_id) {
      headers['x-team-id'] = String(user.team_id);
    }
  }

  return headers;
}

export async function createBudgetTaskViaApi(
  page: Page,
  projectId: number,
  summary: string,
  overrides: Record<string, unknown> = {},
  projectKind: 'chain' | 'single' = 'chain',
): Promise<{ id: number; slug: string }> {
  const fixtures = loadBudgetE2EFixtures();
  const origin = apiOrigin(page);
  const headers = await apiAuthHeaders(
    page.request,
    fixtures?.users.requester.email ?? BUDGET_E2E_EMAILS.requester,
    fixtures?.password ?? BUDGET_E2E_DEFAULT_PASSWORD,
  );
  if (fixtures) {
    await ensureActiveProject(page.request, headers, projectRef(fixtures, projectKind));
  }

  const createTaskResp = await page.request.post(`${origin}/api/tasks/`, {
    headers,
    data: {
      project_id: projectId,
      type: 'budget',
      summary,
      priority: 'MEDIUM',
      create_as_draft: true,
      ...overrides,
    },
  });
  if (!createTaskResp.ok()) {
    throw new Error(`Failed to create budget task (${createTaskResp.status()}): ${await createTaskResp.text()}`);
  }
  const task = await createTaskResp.json();
  if (!task?.id || !task?.slug) {
    throw new Error('Budget task response missing id/slug');
  }
  return { id: task.id as number, slug: task.slug as string };
}

export async function attachBudgetDetailsViaApi(
  page: Page,
  taskId: number,
  opts: {
    amount?: string;
    approverId?: number;
    composite?: string;
    notes?: string;
    projectKind?: 'chain' | 'single';
  } = {},
): Promise<void> {
  const fixtures = requireBudgetE2EFixtures();
  const origin = apiOrigin(page);
  const headers = await apiAuthHeaders(page.request, fixtures.users.requester.email, fixtures.password);
  await ensureActiveProject(
    page.request,
    headers,
    projectRef(fixtures, opts.projectKind ?? 'chain'),
  );
  const composite =
    opts.composite ??
    (opts.projectKind === 'single'
      ? fixtures.single_approver_budget_pool_composite
      : fixtures.budget_pool_composite);
  const parts = composite.split(':');
  if (parts.length < 3) {
    throw new Error(`Invalid budget_pool_composite: ${composite}`);
  }
  const [poolId, channelId, currency] = parts;
  const approverId = opts.approverId ?? fixtures.users.approver_a.user_id;

  const resp = await page.request.post(`${origin}/api/budgets/requests/`, {
    headers,
    data: {
      task: taskId,
      amount: opts.amount ?? '100.00',
      currency,
      ad_channel: Number(channelId),
      budget_pool_id: Number(poolId),
      notes: opts.notes ?? '',
      current_approver: approverId,
    },
  });
  if (!resp.ok()) {
    throw new Error(`Failed to attach budget details (${resp.status()}): ${await resp.text()}`);
  }
}

export async function createIsolatedBudgetTask(
  page: Page,
  summary: string,
  opts: {
    amount?: string;
    approverId?: number;
    taskOverrides?: Record<string, unknown>;
    projectKind?: 'chain' | 'single';
  } = {},
): Promise<BudgetTaskFixture> {
  const fixtures = requireBudgetE2EFixtures();
  const projectKind = opts.projectKind ?? 'chain';
  const projectId =
    projectKind === 'single' ? fixtures.single_approver_project_id : fixtures.project_id;
  const projectSlug =
    projectKind === 'single' ? fixtures.single_approver_project_slug : fixtures.project_slug;
  const approverId = opts.approverId ?? fixtures.users.approver_a.user_id;
  const task = await createBudgetTaskViaApi(
    page,
    projectId,
    summary,
    {
      current_approver_id: approverId,
      ...(opts.taskOverrides ?? {}),
    },
    projectKind,
  );
  await attachBudgetDetailsViaApi(page, task.id, {
    amount: opts.amount,
    approverId,
    projectKind,
  });

  return {
    id: task.id,
    slug: task.slug,
    projectId,
    projectSlug,
    cleanup: async () => {
      await deleteTaskById(page, task.id).catch(() => {});
    },
  };
}

export async function submitBudgetTaskViaApi(
  page: Page,
  task: { slug: string },
  requesterEmail: string,
  projectKind: 'chain' | 'single' = 'chain',
): Promise<void> {
  const result = await postTaskAction(page, task, 'submit', requesterEmail, {}, projectKind);
  if (result.status < 200 || result.status >= 300) {
    throw new Error(`Failed to submit budget task (${result.status}): ${JSON.stringify(result.body)}`);
  }
}

export async function fetchTaskJson(
  page: Page,
  taskId: number,
  email?: string,
  projectKind: 'chain' | 'single' = 'chain',
): Promise<any> {
  const fixtures = loadBudgetE2EFixtures();
  const origin = apiOrigin(page);
  const loginEmail = email ?? fixtures?.users.requester.email ?? BUDGET_E2E_EMAILS.requester;
  const headers = await apiAuthHeaders(
    page.request,
    loginEmail,
    fixtures?.password ?? BUDGET_E2E_DEFAULT_PASSWORD,
  );
  if (fixtures) {
    await ensureActiveProject(page.request, headers, projectRef(fixtures, projectKind));
  }
  const resp = await page.request.get(`${origin}/api/tasks/${taskId}/`, { headers });
  expect(resp.ok()).toBeTruthy();
  return resp.json();
}

export async function postTaskAction(
  page: Page,
  taskRef: { slug: string } | number | string,
  action: string,
  email: string,
  body: Record<string, unknown> = {},
  projectKind: 'chain' | 'single' = 'chain',
): Promise<any> {
  const fixtures = loadBudgetE2EFixtures();
  const origin = apiOrigin(page);
  const headers = await apiAuthHeaders(
    page.request,
    email,
    fixtures?.password ?? BUDGET_E2E_DEFAULT_PASSWORD,
  );
  if (fixtures) {
    await ensureActiveProject(page.request, headers, projectRef(fixtures, projectKind));
  }
  const key = typeof taskRef === 'object' ? taskRef.slug : String(taskRef);
  const resp = await page.request.post(`${origin}/api/tasks/${key}/${action}/`, {
    headers,
    data: body,
  });
  return { status: resp.status(), body: await resp.json().catch(() => ({})) };
}
