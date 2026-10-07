import { randomBytes, randomUUID } from 'crypto';
import { test as base, expect, type APIRequestContext } from '@playwright/test';

type Account = {
  api: APIRequestContext;
  storageState: Awaited<ReturnType<APIRequestContext['storageState']>>;
  projectPath: string;
  facebookPath: string;
};

export const test = base.extend<{}, { account: Account }>({
  account: [async ({ playwright }, use, workerInfo) => {
    const baseURL = workerInfo.project.use.baseURL!;
    // Automatic provisioning is intentionally limited to a local/test stack.
    if (!['localhost', '127.0.0.1', '[::1]'].includes(new URL(baseURL).hostname)) {
      throw new Error('Ads account provisioning requires a loopback BASE_URL. Do not run against production.');
    }
    const id = randomUUID();
    // A second UUID can fail Django's email-similarity password validator.
    const credentials = { email: `ads-${id}@example.test`, password: `E2e!${randomBytes(24).toString('base64url')}` };
    const guest = await playwright.request.newContext({ baseURL, timeout: 60_000 });
    let api = guest;
    const cleanup: Array<() => Promise<void>> = [];
    try {
      const registered = await guest.post('/auth/register/', {
        data: { ...credentials, username: `ads-${id.slice(0, 12)}` },
      });
      expect(registered.status(), 'Register the worker test user').toBe(201);
      const registration = await registered.json();
      api = await playwright.request.newContext({
        baseURL, timeout: 60_000,
        extraHTTPHeaders: { Authorization: `Bearer ${registration.token}` },
      });
      cleanup.push(async () => {
        const response = await api.delete('/auth/me/delete/', { data: { confirm: 'DELETE MY ACCOUNT' } });
        expect(response.status(), 'Anonymize the test account').toBe(200);
      });

      const createdOrg = await api.post('/api/core/organizations/create/', { data: { name: `Ads E2E ${id}` } });
      expect(createdOrg.status(), 'Create the worker organization').toBe(201);
      const { organization } = await createdOrg.json();
      cleanup.push(async () => {
        const response = await api.delete(`/api/core/organizations/${organization.slug}/?force=true`);
        expect(response.status(), 'Deactivate the test organization').toBe(200);
      });

      // Login again to obtain the real profile and organization token after onboarding.
      const loggedIn = await guest.post('/auth/login/', { data: credentials });
      expect(loggedIn.status(), 'Log in the worker test user').toBe(200);
      const auth = await loggedIn.json();
      await api.dispose();
      api = await playwright.request.newContext({
        baseURL,
        extraHTTPHeaders: {
          Authorization: `Bearer ${auth.token}`,
          ...(auth.organization_access_token ? { 'X-Organization-Token': auth.organization_access_token } : {}),
        },
      });
      const createdProject = await api.post('/api/core/projects/', {
        data: { name: 'Ads E2E Project', objectives: ['awareness'], kpis: { ctr: { target: 0.02 } } },
      });
      expect(createdProject.status(), 'Create the worker project').toBe(201);
      const project = await createdProject.json();
      cleanup.push(async () => {
        const response = await api.delete(`/api/core/projects/${project.slug}/`);
        expect(response.status(), 'Delete the test project').toBe(204);
      });

      const storageState = {
        cookies: [],
        origins: [{ origin: new URL(baseURL).origin, localStorage: [
          { name: 'auth-storage-v1', value: JSON.stringify({ version: 0, state: {
            token: auth.token, refreshToken: auth.refresh,
            organizationAccessToken: auth.organization_access_token ?? null,
            user: auth.user, isAuthenticated: true, userTeams: [], selectedTeamId: null,
            loading: false, initialized: true, hasHydrated: true,
          } }) },
          { name: 'project-storage-v1', value: JSON.stringify({ version: 0, state: {
            activeProject: project, activeProjectIds: [project.id], inactiveProjectIds: [], completedProjectIds: [],
          } }) },
        ] }],
      };
      const projectPath = `/${organization.slug}/${project.slug}`;
      await use({ api, storageState, projectPath, facebookPath: `${projectPath}/facebook-meta` });
    } finally {
      // Attempt every cleanup even when an earlier one fails. Never touch pre-existing records.
      const errors: unknown[] = [];
      for (const remove of cleanup.reverse()) {
        try { await remove(); } catch (error) { errors.push(error); }
      }
      if (api !== guest) await api.dispose();
      await guest.dispose();
      if (errors.length) throw new Error(`Ads account cleanup failed:\n${errors.map(String).join('\n')}`);
    }
  }, { scope: 'worker', timeout: 120_000 }],
  storageState: async ({ account }, use) => use(account.storageState),
});

export { expect };
