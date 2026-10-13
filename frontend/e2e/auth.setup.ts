import path from 'path';
import fs from 'fs';
import { expect, test as setup } from '@playwright/test';

const AUTH_DIR = path.join(__dirname, '.auth');
const AUTH_FILE = path.join(AUTH_DIR, 'user.json');

const TEST_EMAIL = process.env.DEV_USER_EMAIL || 'devuser@example.com';
const TEST_PASSWORD = process.env.DEV_USER_PASSWORD || 'password123!';

// The refresh token isn't in the body: the login response sets it as an
// HttpOnly cookie, which page.request stores in this browser context and
// storageState() saves along with localStorage.
type LoginPayload = {
  token: string;
  user: Record<string, unknown>;
  organization_access_token?: string | null;
};

type ProjectPayload = {
  id: number;
  slug?: string;
  name?: string;
  [key: string]: unknown;
};

setup('authenticate', async ({ page, baseURL }) => {
  setup.setTimeout(120_000);

  const origin = baseURL ?? 'http://localhost';

  let loginBody: LoginPayload | undefined;
  await expect
    .poll(
      async () => {
        const response = await page.request.post(`${origin}/auth/login/`, {
          headers: { 'Content-Type': 'application/json' },
          data: { email: TEST_EMAIL, password: TEST_PASSWORD },
        });
        if ([502, 503, 504].includes(response.status())) {
          return false;
        }
        if (!response.ok()) {
          throw new Error(
            `Login API failed (${response.status()}): ${await response.text()}. ` +
              'Check backend is up and DEV_USER_EMAIL / DEV_USER_PASSWORD in frontend/.env.local.',
          );
        }
        loginBody = (await response.json()) as LoginPayload;
        return true;
      },
      {
        message: `Login API did not become ready at ${origin}/auth/login/`,
        timeout: 60_000,
        intervals: [500, 1_000, 2_000],
      },
    )
    .toBe(true);

  if (!loginBody?.token) {
    throw new Error('Login API succeeded but returned no token');
  }

  const projectsResponse = await page.request.get(`${origin}/api/core/projects/`, {
    headers: { Authorization: `Bearer ${loginBody.token}` },
  });
  if (!projectsResponse.ok()) {
    throw new Error(`Projects API failed (${projectsResponse.status()}): ${await projectsResponse.text()}`);
  }

  const projectsBody = await projectsResponse.json();
  const projects: ProjectPayload[] = Array.isArray(projectsBody)
    ? projectsBody
    : (projectsBody.results ?? []);
  const activeProject = projects[0] ?? null;
  if (!activeProject?.id) {
    throw new Error('No project available for E2E user — create at least one project first.');
  }

  await page.goto('/login', { waitUntil: 'domcontentloaded' });
  await page.evaluate(
    ({ auth, project }) => {
      localStorage.setItem(
        'auth-storage-v1',
        JSON.stringify({
          state: {
            token: auth.token,
            organizationAccessToken: auth.organization_access_token ?? null,
            user: auth.user,
            isAuthenticated: true,
            userTeams: [],
            selectedTeamId: null,
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
            activeProject: project,
            activeProjectIds: [project.id],
            inactiveProjectIds: [],
            completedProjectIds: [],
          },
          version: 0,
        }),
      );
    },
    { auth: loginBody, project: activeProject },
  );

  fs.mkdirSync(AUTH_DIR, { recursive: true });
  await page.context().storageState({ path: AUTH_FILE });
});
