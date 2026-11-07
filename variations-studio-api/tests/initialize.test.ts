import { randomUUID } from 'node:crypto';
import { Client, escapeIdentifier } from 'pg';

import { POST as initialize } from '@/app/api/ad_copy_variation/initialize/route';
import { prisma } from '@/lib/prisma';
import { slugToSchemaName } from '@/lib/tenant';
import { STUDIO_MIGRATIONS_TABLE, VARIATION_TABLE } from '@/lib/variationSchema';

import { createVariationTableWithoutLedger, setupStudioFixture, teardownStudioFixture, type StudioFixture } from './support/fixtures';
import { readJson, studioRequest } from './support/requests';
import { ensureInactiveOrg } from './support/tenantRoutingFixtures';
import { accessToken } from './support/tokens';

const observer = new Client({ connectionString: process.env.DATABASE_URL });
const endpoint = '/api/ad_copy_variation/initialize/';
let fixture: StudioFixture;
let token: string;
let organizationId: bigint;
let slug: string;
let schema: string;
let namespace: string;

beforeAll(async () => {
  await observer.connect();
  fixture = await setupStudioFixture();
  token = await accessToken(fixture.memberUserId);
});

beforeEach(async () => {
  slug = `initialize-${randomUUID()}`;
  schema = slugToSchemaName(slug);
  namespace = escapeIdentifier(schema);
  organizationId = (await ensureInactiveOrg(slug)).id;
  await setUserOrganization(organizationId);
  await observer.query(`CREATE SCHEMA ${namespace}`);
  const source = escapeIdentifier(fixture.schema);
  // Copy only this suite's fixture rows; never migrate or alter its source schema.
  for (const table of ['core_project', 'core_projectmember']) {
    await observer.query(`CREATE TABLE ${namespace}.${table} (LIKE ${source}.${table} INCLUDING ALL)`);
  }
  await observer.query(`INSERT INTO ${namespace}.core_project OVERRIDING SYSTEM VALUE
    SELECT * FROM ${source}.core_project WHERE id = ANY($1::bigint[])`,
    [[fixture.projectA.toString(), fixture.projectB.toString()]]);
  await observer.query(`UPDATE ${namespace}.core_project SET organization_id = $1`, [organizationId.toString()]);
  await observer.query(`INSERT INTO ${namespace}.core_projectmember OVERRIDING SYSTEM VALUE
    SELECT * FROM ${source}.core_projectmember WHERE project_id = $1 AND user_id = $2`,
    [fixture.projectA.toString(), fixture.memberUserId]);
});

afterEach(async () => {
  jest.restoreAllMocks();
  await observer.query(`DROP SCHEMA IF EXISTS ${namespace} CASCADE`);
  await setUserOrganization(fixture.organizationId);
  await prisma.organization.delete({ where: { id: organizationId } });
});

afterAll(async () => {
  try {
    if (fixture) await teardownStudioFixture(fixture);
  } finally {
    await observer.end();
    await prisma.$disconnect();
  }
});

async function setUserOrganization(id: bigint | null) {
  await prisma.customUser.update({
    where: { id: BigInt(fixture.memberUserId) },
    data: { organizationId: id, currentOrganizationId: id },
  });
}

function request(projectId: number | string = Number(fixture.projectA)) {
  return initialize(studioRequest(endpoint, { token, body: { project_id: projectId } }));
}

async function storageState() {
  const result = await observer.query('SELECT to_regclass($1)::text AS variation, to_regclass($2)::text AS ledger',
    [`${namespace}.${VARIATION_TABLE}`, `${namespace}.${STUDIO_MIGRATIONS_TABLE}`]);
  return result.rows[0];
}

async function expectNotReady(response: Response) {
  expect(response.status).toBe(503);
  expect(await readJson(response)).toEqual({
    detail: 'Studio is being initialized or upgraded. Please try again shortly.',
  });
}

it('initializes the tenant through 003 and accepts an idempotent slug request', async () => {
  const first = await request();
  expect(first.status).toBe(200);
  expect(await readJson(first)).toEqual({ ready: true });
  const history = await observer.query(`SELECT * FROM ${namespace}.${STUDIO_MIGRATIONS_TABLE} ORDER BY version`);
  expect(history.rows.map(row => row.version)).toEqual(['001', '002', '003']);

  const project = await observer.query(`SELECT slug FROM ${namespace}.core_project WHERE id = $1`,
    [fixture.projectA.toString()]);
  const repeated = await request(project.rows[0].slug);
  expect(repeated.status).toBe(200);
  expect(await readJson(repeated)).toEqual({ ready: true });
  expect((await observer.query(`SELECT * FROM ${namespace}.${STUDIO_MIGRATIONS_TABLE} ORDER BY version`)).rows)
    .toEqual(history.rows);
});

it('rejects non-members, including header fallback, without creating storage', async () => {
  expect((await request(Number(fixture.projectB))).status).toBe(403);

  await setUserOrganization(null);
  await prisma.organization.update({ where: { id: organizationId }, data: { isActive: true } });
  const headerFallback = await initialize(studioRequest(endpoint, {
    token, headers: { 'X-Organization-Slug': slug }, body: { project_id: Number(fixture.projectB) },
  }));
  expect(headerFallback.status).toBe(403);
  expect(await storageState()).toEqual({ variation: null, ledger: null });
});

it('does not adopt an existing variation table without a ledger', async () => {
  await createVariationTableWithoutLedger(observer, schema);

  await expectNotReady(await request());
  expect((await storageState()).ledger).toBeNull();
});

it('returns 503 for a missing schema', async () => {
  await observer.query(`DROP SCHEMA ${namespace} CASCADE`);
  await expectNotReady(await request());
});

it('reports a real migration failure and allows a later request to retry successfully', async () => {
  // A relation with an index's name makes 001 fail after table creation begins.
  const collision = `${namespace}.ad_copy_var_project_5f6c42_idx`;
  await observer.query(`CREATE TABLE ${collision} (id bigint)`);
  const errorLog = jest.spyOn(console, 'error').mockImplementation(() => {});
  await expectNotReady(await request());
  expect(errorLog).toHaveBeenCalledWith('Studio initialization did not complete', expect.objectContaining({
    schema, status: 'failed', error: expect.objectContaining({ code: '42P07' }),
  }));
  expect(await storageState()).toEqual({ variation: null, ledger: null });

  await observer.query(`DROP TABLE ${collision}`);
  const recovered = await request();
  expect(recovered.status).toBe(200);
  expect(await readJson(recovered)).toEqual({ ready: true });
  expect((await observer.query(`SELECT version FROM ${namespace}.${STUDIO_MIGRATIONS_TABLE} ORDER BY version`)).rows)
    .toEqual([{ version: '001' }, { version: '002' }, { version: '003' }]);
});
