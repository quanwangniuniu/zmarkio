import { randomUUID } from 'crypto';

import { GET as listBatches } from '@/app/api/ad_copy_variation/batches/route';
import { prisma } from '@/lib/prisma';
import { tenantTable } from '@/lib/tenant';
import { VARIATION_TABLE } from '@/src/repo';
import {
  createTestVariation,
  setupStudioFixture,
  teardownStudioFixture,
  type StudioFixture,
  type TestVariation,
} from './support/fixtures';
import { readJson, studioRequest } from './support/requests';
import { accessToken } from './support/tokens';

type BatchSummary = {
  batch_id: string;
  created_at: string;
  draft_count: number;
  created_by_id: number | null;
  source_mode: string;
  instruction: string;
  model_name: string;
  reviewed_count: number;
};

type BatchListResponse = {
  count: number;
  page: number;
  page_size: number;
  results: BatchSummary[];
};

let fixture: StudioFixture;
let token: string;
let groupedBatchId: string;
let metadataBatchId: string;
let deletedVariation: TestVariation;
let fullyDeletedBatchId: string;
let softDeletedRepresentativeBatchId: string;

beforeAll(async () => {
  fixture = await setupStudioFixture();
  token = await accessToken(fixture.memberUserId);
  groupedBatchId = randomUUID();
  metadataBatchId = randomUUID();

  softDeletedRepresentativeBatchId = randomUUID();

  const originalRepresentative = await createTestVariation({
    schema: fixture.schema,
    projectId: fixture.projectA,
    userId: fixture.memberUserId,
    status: 'draft',
    batchId: softDeletedRepresentativeBatchId,
    batchPosition: 0,
    sourceMode: 'original',
    instruction: 'Original instruction',
    modelName: 'original-model',
  });

  const remainingVariation = await createTestVariation({
    schema: fixture.schema,
    projectId: fixture.projectA,
    userId: fixture.memberUserId,
    status: 'reviewed',
    batchId: softDeletedRepresentativeBatchId,
    batchPosition: 1,
    sourceMode: 'remaining',
    instruction: 'Remaining instruction',
    modelName: 'remaining-model',
  });

  await prisma.$executeRaw`
    UPDATE ${tenantTable(fixture.schema, VARIATION_TABLE)}
    SET created_at = '2026-01-01T10:00:00Z'
    WHERE id = ${originalRepresentative.id}
  `;

  await prisma.$executeRaw`
    UPDATE ${tenantTable(fixture.schema, VARIATION_TABLE)}
    SET created_at = '2026-01-01T10:10:00Z'
    WHERE id = ${remainingVariation.id}
  `;

  await prisma.$executeRaw`
    UPDATE ${tenantTable(fixture.schema, VARIATION_TABLE)}
    SET is_deleted = true
    WHERE id = ${originalRepresentative.id}
  `;

  await createTestVariation({
    schema: fixture.schema,
    projectId: fixture.projectA,
    userId: fixture.memberUserId,
    status: 'draft',
    batchId: metadataBatchId,
    batchPosition: 0,
    sourceMode: 'zulu',
    instruction: 'Alpha instruction',
    modelName: 'model-z',
  });

  await createTestVariation({
    schema: fixture.schema,
    projectId: fixture.projectA,
    userId: fixture.memberUserId,
    status: 'draft',
    batchId: metadataBatchId,
    batchPosition: 1,
    sourceMode: 'alpha',
    instruction: 'Zulu instruction',
    modelName: 'model-a',
  });

  await createTestVariation({
    schema: fixture.schema,
    projectId: fixture.projectA,
    userId: fixture.memberUserId,
    status: 'draft',
    batchId: groupedBatchId,
  });

  deletedVariation = await createTestVariation({
    schema: fixture.schema,
    projectId: fixture.projectA,
    userId: fixture.memberUserId,
    status: 'draft',
    batchId: groupedBatchId,
  });

  await prisma.$executeRaw`
    UPDATE ${tenantTable(fixture.schema, VARIATION_TABLE)}
    SET is_deleted = true
    WHERE id = ${deletedVariation.id}
  `;

  fullyDeletedBatchId = randomUUID();

  const fullyDeletedVariation = await createTestVariation({
    schema: fixture.schema,
    projectId: fixture.projectA,
    userId: fixture.memberUserId,
    status: 'draft',
    batchId: fullyDeletedBatchId,
  });

  await prisma.$executeRaw`
    UPDATE ${tenantTable(fixture.schema, VARIATION_TABLE)}
    SET is_deleted = true
    WHERE id = ${fullyDeletedVariation.id}
  `;

  await createTestVariation({
    schema: fixture.schema,
    projectId: fixture.projectA,
    userId: fixture.memberUserId,
    status: 'draft',
    batchId: groupedBatchId,
  });

  await createTestVariation({
    schema: fixture.schema,
    projectId: fixture.projectA,
    userId: fixture.memberUserId,
    status: 'reviewed',
    batchId: groupedBatchId,
  });
});

afterAll(async () => {
  await teardownStudioFixture(fixture);
  await prisma.$disconnect();
});

function list(query: string) {
  return listBatches(
    studioRequest(`/api/ad_copy_variation/batches?${query}`, { token })
  );
}

describe('batch history', () => {
  it('groups variations and counts draft and reviewed statuses', async () => {
    const response = await list(
      `project_id=${fixture.projectA}&page=1&page_size=100`
    );

    expect(response.status).toBe(200);

    const body = (await readJson(response)) as BatchListResponse;
    const batch = body.results.find(
      (row) => row.batch_id === groupedBatchId
    );

    expect(batch).toMatchObject({
      batch_id: groupedBatchId,
      draft_count: 2,
      reviewed_count: 1,
    });

    expect(
      body.results.some((row) => row.batch_id === fullyDeletedBatchId)
    ).toBe(false);

    expect(body.count).toBe(body.results.length);
  });

  it('uses metadata from one representative variation per batch', async () => {
    const response = await list(
      `project_id=${fixture.projectA}&page=1&page_size=100`
    );

    expect(response.status).toBe(200);

    const body = (await readJson(response)) as BatchListResponse;
    const batch = body.results.find(
      (row) => row.batch_id === metadataBatchId
    );

    expect(batch).toMatchObject({
      batch_id: metadataBatchId,
      source_mode: 'zulu',
      instruction: 'Alpha instruction',
      model_name: 'model-z',
      draft_count: 2,
      reviewed_count: 0,
    });
  });

  it('uses the remaining representative variation after the original is soft-deleted', async () => {
    const response = await list(
      `project_id=${fixture.projectA}&page=1&page_size=100`
    );

    expect(response.status).toBe(200);

    const body = (await readJson(response)) as BatchListResponse;
    const batch = body.results.find(
      (row) => row.batch_id === softDeletedRepresentativeBatchId
    );

    expect(batch).toMatchObject({
      batch_id: softDeletedRepresentativeBatchId,
      source_mode: 'remaining',
      instruction: 'Remaining instruction',
      model_name: 'remaining-model',
      reviewed_count: 1,
      draft_count: 0,
      created_at: '2026-01-01T10:10:00.000Z',
    });
  });

  it('paginates correctly and orders batches newest first', async () => {
    const allResponse = await list(
      `project_id=${fixture.projectA}&page=1&page_size=100`
    );
    expect(allResponse.status).toBe(200);

    const allBody = (await readJson(allResponse)) as BatchListResponse;
    expect(allBody.results.length).toBeGreaterThanOrEqual(2);
    expect(allBody.count).toBe(allBody.results.length);

    for (let i = 1; i < allBody.results.length; i++) {
      expect(Date.parse(allBody.results[i - 1].created_at))
        .toBeGreaterThanOrEqual(Date.parse(allBody.results[i].created_at));
    }

    const pageOneResponse = await list(
      `project_id=${fixture.projectA}&page=1&page_size=1`
    );
    expect(pageOneResponse.status).toBe(200);
    const pageOne = (await readJson(pageOneResponse)) as BatchListResponse;

    const pageTwoResponse = await list(
      `project_id=${fixture.projectA}&page=2&page_size=1`
    );
    expect(pageTwoResponse.status).toBe(200);
    const pageTwo = (await readJson(pageTwoResponse)) as BatchListResponse;

    expect(pageOne.count).toBe(allBody.count);
    expect(pageTwo.count).toBe(allBody.count);
    expect(pageOne.results).toHaveLength(1);
    expect(pageTwo.results).toHaveLength(1);
    expect(pageOne.results[0].batch_id).toBe(allBody.results[0].batch_id);
    expect(pageTwo.results[0].batch_id).toBe(allBody.results[1].batch_id);
  });

  it('normalizes invalid pagination values and caps page size', async () => {
    const decimalResponse = await list(
      `project_id=${fixture.projectA}&page=1.5&page_size=1.5`
    );

    expect(decimalResponse.status).toBe(200);
    const decimalBody = (await readJson(decimalResponse)) as BatchListResponse;
    expect(decimalBody.page).toBe(1);
    expect(decimalBody.page_size).toBe(20);

    const oversizedResponse = await list(
      `project_id=${fixture.projectA}&page=1e309&page_size=1000`
    );

    expect(oversizedResponse.status).toBe(200);
    const oversizedBody = (await readJson(oversizedResponse)) as BatchListResponse;
    expect(oversizedBody.page).toBe(1);
    expect(oversizedBody.page_size).toBe(100);

    const unsafeOffsetResponse = await list(
      `project_id=${fixture.projectA}&page=9007199254740991&page_size=100`
    );

    expect(unsafeOffsetResponse.status).toBe(200);
    const unsafeOffsetBody = (await readJson(unsafeOffsetResponse)) as BatchListResponse;
    expect(unsafeOffsetBody.page).toBe(1);
    expect(unsafeOffsetBody.page_size).toBe(100);
  });

  it('denies a user who is not a member of the requested project', async () => {
    const response = await list(`project_id=${fixture.projectB}`);

    expect(response.status).toBe(403);
  });

  it('rejects a missing project_id', async () => {
    const response = await listBatches(
      studioRequest('/api/ad_copy_variation/batches', { token })
    );

    expect(response.status).toBe(400);
  });
});
