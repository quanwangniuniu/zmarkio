import { randomUUID } from 'crypto';

import { GET as listBatches } from '@/app/api/ad_copy_variation/batches/route';
import { prisma } from '@/lib/prisma';

import {
  createTestVariation,
  setupStudioFixture,
  teardownStudioFixture,
  type StudioFixture,
} from './support/fixtures';
import { readJson, studioRequest } from './support/requests';
import { accessToken } from './support/tokens';

type BatchSummary = {
  batch_id: string;
  created_at: string;
  draft_count: number;
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

beforeAll(async () => {
  fixture = await setupStudioFixture();
  token = await accessToken(fixture.memberUserId);
  groupedBatchId = randomUUID();

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
