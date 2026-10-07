import { type Browser, type BrowserContext, type Page } from '@playwright/test';
import { waitForTasksPageReady } from '../../tasks/tasks-helpers';
import { BUDGET_E2E_DEFAULT_PASSWORD, resolveBudgetRbacAuthState, type BudgetE2EFixturePayload } from './budget-e2e-types';
import { loadBudgetE2EFixtures } from './budget-fixtures';

type LoginPayload = {
  token: string;
  refresh: string;
  user: Record<string, unknown>;
  organization_access_token?: string | null;
};

type ProjectSeed = {
  id: number;
  slug?: string;
  name?: string;
};

export type BudgetUserSession = {
  email: string;
  context: BrowserContext;
  page: Page;
  close: () => Promise<void>;
};

async function loginRequest(
  page: Page,
  email: string,
  password: string,
): Promise<LoginPayload> {
  const origin = process.env.BASE_URL || 'http://localhost';
  const response = await page.request.post(`${origin}/auth/login/`, {
    headers: { 'Content-Type': 'application/json' },
    data: { email, password },
  });
  if (!response.ok()) {
    throw new Error(`Budget E2E login failed for ${email} (${response.status()}): ${await response.text()}`);
  }
  return response.json() as Promise<LoginPayload>;
}

async function seedAuthStorage(
  page: Page,
  auth: LoginPayload,
  project: ProjectSeed,
  rbac: { roles: string[]; userTeams: number[]; selectedTeamId: number | null },
): Promise<void> {
  await page.goto('/login', { waitUntil: 'domcontentloaded' });
  await page.evaluate(
    ({ authState, activeProject, rbacState }) => {
      localStorage.setItem(
        'auth-storage-v1',
        JSON.stringify({
          state: {
            token: authState.token,
            refreshToken: authState.refresh,
            organizationAccessToken: authState.organization_access_token ?? null,
            user: {
              ...authState.user,
              roles: rbacState.roles,
              team_id: rbacState.selectedTeamId ?? undefined,
            },
            isAuthenticated: true,
            userTeams: rbacState.userTeams,
            selectedTeamId: rbacState.selectedTeamId,
            loading: false,
            initialized: true,
            hasHydrated: true,
          },
          version: 0,
        }),
      );
      localStorage.setItem(
        'project-storage-v1',
        JSON.stringify({
          state: {
            activeProject,
            activeProjectIds: [activeProject.id],
            inactiveProjectIds: [],
            completedProjectIds: [],
          },
          version: 0,
        }),
      );
    },
    { authState: auth, activeProject: project, rbacState: rbac },
  );
}

export async function openBudgetUserSession(
  browser: Browser,
  email: string,
  password: string = BUDGET_E2E_DEFAULT_PASSWORD,
  projectKind: 'chain' | 'single' = 'chain',
): Promise<BudgetUserSession> {
  const fixtures = loadBudgetE2EFixtures();
  if (!fixtures) {
    throw new Error('Budget E2E fixtures file missing — run issue_budget_e2e_fixtures first.');
  }

  const project =
    projectKind === 'single'
      ? {
          id: fixtures.single_approver_project_id,
          slug: fixtures.single_approver_project_slug,
          name: fixtures.single_approver_project_name,
        }
      : {
          id: fixtures.project_id,
          slug: fixtures.project_slug,
          name: fixtures.project_name,
        };

  const context = await browser.newContext();
  const page = await context.newPage();
  const auth = await loginRequest(page, email, password);
  const origin = process.env.BASE_URL || 'http://localhost';
  const setActiveKey = project.slug ?? String(project.id);
  const setActiveResp = await page.request.post(
    `${origin}/api/core/projects/${encodeURIComponent(setActiveKey)}/set_active/`,
    {
      headers: {
        Authorization: `Bearer ${auth.token}`,
        'Content-Type': 'application/json',
        ...(auth.organization_access_token
          ? { 'X-Organization-Token': auth.organization_access_token }
          : {}),
      },
    },
  );
  if (!setActiveResp.ok()) {
    throw new Error(
      `Failed to set active project for ${email} (${setActiveResp.status()}): ${await setActiveResp.text()}`,
    );
  }
  await seedAuthStorage(page, auth, {
    id: project.id,
    slug: project.slug,
    name: project.name,
  }, resolveBudgetRbacAuthState(fixtures, email));
  await page.goto('/tasks', { waitUntil: 'domcontentloaded' });
  await waitForTasksPageReady(page);

  return {
    email,
    context,
    page,
    close: async () => {
      await context.close().catch(() => {});
    },
  };
}

export function fixtureEmail(role: keyof BudgetE2EFixturePayload['users']): string {
  const fixtures = loadBudgetE2EFixtures();
  if (!fixtures) {
    throw new Error('Budget E2E fixtures file missing.');
  }
  return fixtures.users[role].email;
}
