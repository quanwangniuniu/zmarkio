import { randomUUID } from 'crypto';

import { GET as getBatch } from '@/app/api/ad_copy_variation/batches/[batch_id]/route';
import { prisma } from '@/lib/prisma';

import {
  createTestVariation,
  setupStudioFixture,
  teardownStudioFixture,
  type StudioFixture,
  type TestVariation,
} from './support/fixtures';
import { readJson, studioRequest } from './support/requests';
import { accessToken } from './support/tokens';

type BatchDetailResponse = {
  batch_id: string;
  count: number;
  results: {
    id: number;
    batch_id: string | null;
    batch_position: number | null;
  }[];
};

let fixture: StudioFixture;
let token: string;
let batchId: string;
let positionTwo: TestVariation;
let positionZero: TestVariation;
let positionOne: TestVariation;

beforeAll(async () => {
  fixture = await setupStudioFixture();
  token = await accessToken(fixture.memberUserId);
  batchId = randomUUID();

  // Intentionally create the records out of position order.
  positionTwo = await createTestVariation({
    schema: fixture.schema,
    projectId: fixture.projectA,
    userId: fixture.memberUserId,
    status: 'draft',
    batchId,
    batchPosition: 2,
  });

  positionZero = await createTestVariation({
    schema: fixture.schema,
    projectId: fixture.projectA,
    userId: fixture.memberUserId,
    status: 'draft',
    batchId,
    batchPosition: 0,
  });

  positionOne = await createTestVariation({
    schema: fixture.schema,
    projectId: fixture.projectA,
    userId: fixture.memberUserId,
    status: 'reviewed',
    batchId,
    batchPosition: 1,
  });
});

afterAll(async () => {
  await teardownStudioFixture(fixture);
  await prisma.$disconnect();
});

function getBatchRequest(
  projectId: string,
  requestedBatchId = batchId
) {
  return getBatch(
    studioRequest(
      `/api/ad_copy_variation/batches/${requestedBatchId}?project_id=${projectId}`,
      { token }
    ),
    { params: { batch_id: requestedBatchId } }
  );
}

describe('batch detail', () => {
  it('returns only the requested batch, ordered by batch_position', async () => {
    const response = await getBatchRequest(String(fixture.projectA));

    expect(response.status).toBe(200);

    const body = (await readJson(response)) as BatchDetailResponse;

    expect(body.batch_id).toBe(batchId);
    expect(body.count).toBe(3);
    expect(body.results.map((row) => row.batch_position)).toEqual([0, 1, 2]);
    expect(body.results.map((row) => row.id)).toEqual([
      Number(positionZero.id),
      Number(positionOne.id),
      Number(positionTwo.id),
    ]);
    expect(body.results.every((row) => row.batch_id === batchId)).toBe(true);
  });

  it('denies access to a project the user is not a member of', async () => {
    const response = await getBatchRequest(String(fixture.projectB));

    expect(response.status).toBe(403);
  });

  it('rejects an invalid batch UUID', async () => {
    const response = await getBatchRequest(
      String(fixture.projectA),
      'not-a-uuid'
    );

    expect(response.status).toBe(400);
  });

  it('rejects a missing project_id', async () => {
    const response = await getBatch(
      studioRequest(`/api/ad_copy_variation/batches/${batchId}`, { token }),
      { params: { batch_id: batchId } }
    );

    expect(response.status).toBe(400);
  });
});
