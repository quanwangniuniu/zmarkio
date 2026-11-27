/**
 * Lighthouse CI prerequisites (runs before `lhci autorun`):
 * 1. Register user if not exists (POST /auth/register/)
 * 2. Login (POST /auth/login/) → JWT
 * 3. Find project by name or create (GET/POST /api/core/projects/)
 *
 * Env:
 * - LHCI_API_BASE / LHCI_BASE_URL — default http://localhost (match lighthouserc base)
 * - LHCI_AUTH_EMAIL / LHCI_AUTH_PASSWORD — required when not skipping
 * - LHCI_TEST_PROJECT_ID — if set, skip steps 1–3 for project (still need auth for Puppeteer)
 * - LHCI_ENSURE_PROJECT — set to "0" to skip registration/login/project API (Puppeteer only)
 * - LHCI_PROJECT_NAME — stable name to find-or-create (default: "LHCI Test Project")
 */
const {
  getBaseUrl,
  getCredentials,
  registerUserIfNotExists,
  loginWithApi,
  findProjectByName,
  createProject,
} = require('./lhci-lib.cjs');
const crypto = require('crypto');

const DEFAULT_PROJECT_NAME = 'LHCI Test Project';

/**
 * @returns {Promise<string | null>} project id as string, or null if skipped
 */
async function requestJson(method, url, token, body) {
  const res = await fetch(url, {
    method,
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  if (!res.ok) throw new Error(`LHCI ${method} ${url} failed ${res.status}: ${text.slice(0, 300)}`);
  return text ? JSON.parse(text) : {};
}

/**
 * MED-326: org admins must pass MFA to sign in, and a user's first project
 * auto-creates an org with that user as admin. So a throwaway account creates
 * the org and the audited account joins it as a regular member first.
 */
async function joinOrganizationAsMember(base, token) {
  const status = await requestJson('GET', `${base}/api/core/onboarding-status/`, token);
  if (status.has_org) return;

  const id = crypto.randomUUID();
  const owner = await requestJson('POST', `${base}/auth/register/`, null, {
    email: `lhci-org-owner-${id}@example.com`,
    password: `Lhci!${crypto.randomBytes(18).toString('base64url')}`,
    username: `lhci-owner-${id.slice(0, 8)}`,
  });
  const created = await requestJson('POST', `${base}/api/core/organizations/create/`, owner.token, {
    name: `LHCI Org ${id.slice(0, 8)}`,
  });
  await requestJson('POST', `${base}/api/core/organizations/join/`, token, {
    slug: created.organization.slug,
  });
  process.stderr.write(`[lhci] joined organization ${created.organization.slug} as a regular member\n`);
}

async function ensureProject() {
  if (process.env.LHCI_ENSURE_PROJECT === '0') {
    return null;
  }
  if (process.env.LHCI_TEST_PROJECT_ID) {
    return String(process.env.LHCI_TEST_PROJECT_ID);
  }

  const creds = getCredentials();
  if (!creds) {
    return null;
  }

  const base = getBaseUrl();
  const projectName = process.env.LHCI_PROJECT_NAME || DEFAULT_PROJECT_NAME;

  process.stderr.write(
    `[lhci] ensure-project: apiBase=${base} projectName=${projectName}\n`
  );

  await registerUserIfNotExists(base, creds.email, creds.password);
  const token = await loginWithApi(base, creds.email, creds.password);
  await joinOrganizationAsMember(base, token);

  let project = await findProjectByName(base, token, projectName);
  if (!project) {
    project = await createProject(base, token, projectName);
  }

  if (project == null || project.id == null) {
    throw new Error('lhci-ensure-project: project missing id');
  }

  const id = String(project.id);
  process.env.LHCI_TEST_PROJECT_ID = id;
  process.stderr.write(`lhci-ensure-project: LHCI_TEST_PROJECT_ID=${id} (${projectName})\n`);
  return id;
}

module.exports = { ensureProject, apiBase: getBaseUrl };
