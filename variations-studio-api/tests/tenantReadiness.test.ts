import { randomUUID } from 'node:crypto';
import { copyFile, mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { Client, escapeIdentifier } from 'pg';

import { POST as generate } from '@/app/api/ad_copy_variation/variations/generate/route';
import { GET as list } from '@/app/api/ad_copy_variation/variations/route';
import { runCustomGenerate } from '@/lib/generate';
import { runMigrations } from '@/lib/migrate';
import { prisma } from '@/lib/prisma';
import { slugToSchemaName } from '@/lib/tenant';

import { setupStudioFixture, teardownStudioFixture, type StudioFixture } from './support/fixtures';
import { ensureInactiveOrg } from './support/tenantRoutingFixtures';
import { readJson, studioRequest } from './support/requests';
import { accessToken } from './support/tokens';

jest.mock('@/lib/generate', () => ({ runCustomGenerate: jest.fn() }));

const observer = new Client({ connectionString: process.env.DATABASE_URL });
const slug = `readiness-${randomUUID()}`;
const schema = slugToSchemaName(slug);
const namespace = escapeIdentifier(schema);
const migrationsDir = path.join(__dirname, '..', 'migrations');
let fixture: StudioFixture;
let organizationId: bigint;
let token: string;
let baselineMigrationsDir: string | undefined;

beforeAll(async () => {
  await observer.connect();
  fixture = await setupStudioFixture();
  token = await accessToken(fixture.memberUserId);
  organizationId = (await ensureInactiveOrg(slug)).id;
});

afterAll(async () => {
  try {
    await observer.query(`DROP SCHEMA IF EXISTS ${namespace} CASCADE`);
    if (fixture) await teardownStudioFixture(fixture);
    if (organizationId) {
      await prisma.organization.delete({ where: { id: organizationId } });
    }
    if (baselineMigrationsDir) await rm(baselineMigrationsDir, { recursive: true, force: true });
  } finally {
    await observer.end();
    await prisma.$disconnect();
  }
});

it('requires an organization for list and generate without calling generation', async () => {
  await prisma.customUser.update({
    where: { id: BigInt(fixture.memberUserId) },
    data: { organizationId: null, currentOrganizationId: null },
  });
  for (const response of [
    await generate(studioRequest('/api/ad_copy_variation/variations/generate/', { token, body: {} })),
    await list(studioRequest('/api/ad_copy_variation/variations/', { token })),
  ]) {
    expect(response.status).toBe(403);
    expect(await readJson(response)).toEqual({ detail: 'An organization is required to use Studio.' });
  }
  expect(runCustomGenerate).not.toHaveBeenCalled();
});

it('blocks unready tenant states and recovers after the required migration, including an inactive org', async () => {
  await prisma.customUser.update({
    where: { id: BigInt(fixture.memberUserId) },
    data: { organizationId, currentOrganizationId: organizationId },
  });
  async function expectNotReady() {
    const response = await generate(studioRequest('/api/ad_copy_variation/variations/generate/', {
      token, body: {},
    }));
    expect(response.status).toBe(503);
    expect(await readJson(response)).toEqual({
      detail: 'Studio is being initialized or upgraded. Please try again shortly.',
    });
    expect(runCustomGenerate).not.toHaveBeenCalled();
  }

  await expectNotReady(); // Missing schema returns not ready.
  await observer.query(`CREATE SCHEMA ${namespace}`);
  await observer.query(`CREATE TABLE ${namespace}.core_project (id bigint PRIMARY KEY)`);
  await expectNotReady(); // Schema exists, but the variation table does not.

  // An existing baseline table without a ledger is not proof of an upgrade.
  await observer.query('BEGIN');
  try {
    await observer.query(`SET LOCAL search_path TO ${namespace}`);
    await observer.query(await readFile(path.join(migrationsDir, '001_ad_copy_variation_baseline.sql'), 'utf8'));
    await observer.query('COMMIT');
  } catch (error) {
    await observer.query('ROLLBACK');
    throw error;
  }
  await expectNotReady();

  baselineMigrationsDir = await mkdtemp(path.join(path.resolve(tmpdir()), 'studio-readiness-'));
  for (const file of ['001_ad_copy_variation_baseline.sql', '002_ad_copy_variation_project_cascade.sql']) {
    await copyFile(path.join(migrationsDir, file), path.join(baselineMigrationsDir, file));
  }
  const baseline = await runMigrations({ command: 'apply', schemas: [schema], migrationsDir: baselineMigrationsDir });
  expect(baseline.results[0]).toMatchObject({ status: 'applied', appliedNow: ['001', '002'] });

  // A real 001/002 ledger plus a higher version still cannot stand in for 003.
  await observer.query(`INSERT INTO ${namespace}.studio_migrations (version, checksum) VALUES ($1, $2)`,
    ['999', 'synthetic-history-for-readiness-test']);
  await expectNotReady();
  // The runner refuses to apply 003 below a recorded 999, so remove it first.
  await observer.query(`DELETE FROM ${namespace}.studio_migrations WHERE version = '999'`);

  const migrated = await runMigrations({ command: 'apply', schemas: [schema], migrationsDir });
  expect(migrated.results[0]).toMatchObject({ status: 'applied', appliedNow: ['003'] });
  await observer.query(`INSERT INTO ${namespace}.studio_migrations (version, checksum) VALUES ($1, $2)`,
    ['999', 'synthetic-history-for-readiness-test']);
  // The required version now exists alongside a higher version; the next request works.
  jest.mocked(runCustomGenerate).mockResolvedValueOnce({ count_succeeded: 1 } as Awaited<ReturnType<typeof runCustomGenerate>>);
  const response = await generate(studioRequest('/api/ad_copy_variation/variations/generate/', {
    token, body: {},
  }));
  expect(response.status).toBe(200);
  expect(runCustomGenerate).toHaveBeenCalledWith({ schema, userId: fixture.memberUserId, body: {} });
});
