import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { Client, escapeIdentifier } from 'pg';

import { POST as generate } from '@/app/api/ad_copy_variation/variations/generate/route';
import { GET as list } from '@/app/api/ad_copy_variation/variations/route';
import { runCustomGenerate } from '@/lib/generate';
import { runMigrations } from '@/lib/migrate';
import { prisma } from '@/lib/prisma';
import { slugToSchemaName } from '@/lib/tenant';
import * as variations from '@/lib/variationStore';

import { setupStudioFixture, teardownStudioFixture, type StudioFixture } from './support/fixtures';
import { readJson, studioRequest } from './support/requests';
import { accessToken } from './support/tokens';

jest.mock('@/lib/generate', () => ({ runCustomGenerate: jest.fn() }));
jest.mock('@/lib/variationStore', () => {
  const actual = jest.requireActual('@/lib/variationStore');
  return {
    ...actual,
    listVariations: jest.fn(actual.listVariations),
    countVariations: jest.fn(actual.countVariations),
  };
});

const observer = new Client({ connectionString: process.env.DATABASE_URL });
const slug = `readiness-${randomUUID()}`;
const schema = slugToSchemaName(slug);
const namespace = escapeIdentifier(schema);
const migrationsDir = path.join(__dirname, '..', 'migrations');
let fixture: StudioFixture;
let organizationId: bigint;
let token: string;

beforeAll(async () => {
  await observer.connect();
  fixture = await setupStudioFixture();
  token = await accessToken(fixture.memberUserId);
  const rows = await prisma.$queryRaw<{ id: bigint }[]>`
    INSERT INTO public.core_organization (
      name, slug, is_active, is_deleted, is_parent, created_at, updated_at,
      max_concurrent_sessions
    ) VALUES (${slug}, ${slug}, false, false, false, now(), now(), 5)
    RETURNING id`;
  organizationId = rows[0].id;
});

afterAll(async () => {
  try {
    await observer.query(`DROP SCHEMA IF EXISTS ${namespace} CASCADE`);
    if (fixture) await teardownStudioFixture(fixture);
    if (organizationId) {
      await prisma.organization.delete({ where: { id: organizationId } });
    }
  } finally {
    await observer.end();
    await prisma.$disconnect();
  }
});

it('authenticates before requiring an organization, without querying variations or generating', async () => {
  await prisma.customUser.update({
    where: { id: BigInt(fixture.memberUserId) },
    data: { organizationId: null, currentOrganizationId: null },
  });
  jest.mocked(variations.listVariations).mockClear();
  jest.mocked(variations.countVariations).mockClear();
  const unauthenticated = await generate(studioRequest('/api/ad_copy_variation/variations/generate/', {
    body: {},
  }));
  expect(unauthenticated.status).toBe(401);
  for (const response of [
    await generate(studioRequest('/api/ad_copy_variation/variations/generate/', { token, body: {} })),
    await list(studioRequest('/api/ad_copy_variation/variations/', { token })),
  ]) {
    expect(response.status).toBe(403);
    expect(await readJson(response)).toEqual({ detail: 'An organization is required to use Studio.' });
  }
  expect(variations.listVariations).not.toHaveBeenCalled();
  expect(variations.countVariations).not.toHaveBeenCalled();
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

  await expectNotReady(); // Missing schema must not resolve public's table.
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

  // Synthetic negative history: a higher version cannot stand in for 002.
  await observer.query(`CREATE TABLE ${namespace}.studio_migrations (
    version text PRIMARY KEY, checksum text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
  )`);
  await observer.query(`INSERT INTO ${namespace}.studio_migrations (version, checksum) VALUES ($1, $2)`,
    ['999', 'synthetic-history-for-readiness-test']);
  await expectNotReady();

  // Remove only the synthetic history before running the real ordered chain.
  await observer.query(`DELETE FROM ${namespace}.studio_migrations WHERE version = '999'`);

  const migrated = await runMigrations({ command: 'apply', schemas: [schema], migrationsDir });
  expect(migrated.ok).toBe(true);
  expect(migrated.results[0].appliedNow).toEqual(['001', '002']);
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
