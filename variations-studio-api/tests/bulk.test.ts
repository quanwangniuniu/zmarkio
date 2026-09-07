import { randomUUID } from 'crypto';

import { POST as bulkDelete } from '@/app/api/ad_copy_variation/variations/bulk_delete/route';
import { POST as bulkReview } from '@/app/api/ad_copy_variation/variations/bulk_review/route';
import { POST as reviewBatch } from '@/app/api/ad_copy_variation/variations/review_batch/route';
import { prisma } from '@/lib/prisma';
import {
  deleteVariationsByIds,
  findVariationsByIdsAnyProject,
  setVariationStatus,
} from '@/lib/variationStore';

import {
  createTestVariation,
  setupStudioFixture,
  teardownStudioFixture,
  type StudioFixture,
} from './support/fixtures';
import { readJson, studioRequest } from './support/requests';
import { accessToken } from './support/tokens';

let fixture: StudioFixture;
let token: string;

// Fresh rows per test: these suites delete and mutate what they select.
let batchId: string;
let mine: bigint;
let alsoMine: bigint;
let theirs: bigint;

beforeAll(async () => {
  fixture = await setupStudioFixture();
  token = await accessToken(fixture.memberUserId);
});

afterAll(async () => {
  await teardownStudioFixture(fixture);
  await prisma.$disconnect();
});

beforeEach(async () => {
  batchId = randomUUID();
  mine = (await createTestVariation({
    schema: fixture.schema,
    projectId: fixture.projectA,
    userId: fixture.memberUserId,
    status: 'draft',
    batchId,
  })).id;
  alsoMine = (await createTestVariation({
    schema: fixture.schema,
    projectId: fixture.projectA,
    userId: fixture.memberUserId,
    status: 'draft',
    batchId,
  })).id;
  theirs = (await createTestVariation({
    schema: fixture.schema,
    projectId: fixture.projectB,
    userId: fixture.memberUserId,
    status: 'draft',
  })).id;
});

afterEach(async () => {
  await deleteVariationsByIds(fixture.schema, [mine, alsoMine, theirs]);
});

function post(
  handler: (request: Request) => Promise<Response>,
  path: string,
  body: Record<string, unknown>
) {
  return handler(studioRequest(path, { token, body }));
}

async function survivingIds(): Promise<bigint[]> {
  const rows = await findVariationsByIdsAnyProject(fixture.schema, [
    mine,
    alsoMine,
    theirs,
  ]);
  return rows.map((row) => row.id).sort();
}

describe('bulk_delete', () => {
  it('refuses a selection containing another project id and deletes nothing', async () => {
    const response = await post(
      bulkDelete,
      '/api/ad_copy_variation/variations/bulk_delete/',
      {
        project_id: String(fixture.projectA),
        selected_ids: [Number(mine), Number(theirs)],
        status: 'draft',
      }
    );

    expect(response.status).toBe(400);
    await expect(readJson(response)).resolves.toEqual({
      error: 'selected_ids must all belong to the current project',
    });
    expect(await survivingIds()).toEqual([mine, alsoMine, theirs].sort());
  });

  it('refuses a selection whose status does not match and deletes nothing', async () => {
    await setVariationStatus(fixture.schema, alsoMine, 'reviewed');

    const response = await post(
      bulkDelete,
      '/api/ad_copy_variation/variations/bulk_delete/',
      {
        project_id: String(fixture.projectA),
        selected_ids: [Number(mine), Number(alsoMine)],
        status: 'draft',
      }
    );

    expect(response.status).toBe(400);
    expect(await survivingIds()).toEqual([mine, alsoMine, theirs].sort());
  });

  it('deletes a valid selection', async () => {
    const response = await post(
      bulkDelete,
      '/api/ad_copy_variation/variations/bulk_delete/',
      {
        project_id: String(fixture.projectA),
        selected_ids: [Number(mine)],
        status: 'draft',
      }
    );

    expect(response.status).toBe(200);
    await expect(readJson(response)).resolves.toMatchObject({ deleted_count: 1 });
    expect(await survivingIds()).toEqual([alsoMine, theirs].sort());
  });

  it('rejects an empty selection', async () => {
    const response = await post(
      bulkDelete,
      '/api/ad_copy_variation/variations/bulk_delete/',
      {
        project_id: String(fixture.projectA),
        selected_ids: [],
        status: 'draft',
      }
    );

    expect(response.status).toBe(400);
  });

  it('rejects non-integer ids', async () => {
    const response = await post(
      bulkDelete,
      '/api/ad_copy_variation/variations/bulk_delete/',
      {
        project_id: String(fixture.projectA),
        selected_ids: ['; DROP TABLE'],
        status: 'draft',
      }
    );

    expect(response.status).toBe(400);
    expect(await survivingIds()).toEqual([mine, alsoMine, theirs].sort());
  });
});

describe('bulk_review', () => {
  it('refuses a selection containing another project id and reviews nothing', async () => {
    const response = await post(
      bulkReview,
      '/api/ad_copy_variation/variations/bulk_review/',
      {
        project_id: String(fixture.projectA),
        selected_ids: [Number(mine), Number(theirs)],
      }
    );

    expect(response.status).toBe(400);

    const rows = await findVariationsByIdsAnyProject(fixture.schema, [
      mine,
      theirs,
    ]);
    expect(rows.every((row) => row.status === 'draft')).toBe(true);
  });

  it('reviews a valid selection', async () => {
    const response = await post(
      bulkReview,
      '/api/ad_copy_variation/variations/bulk_review/',
      {
        project_id: String(fixture.projectA),
        selected_ids: [Number(mine), Number(alsoMine)],
      }
    );

    expect(response.status).toBe(200);
    await expect(readJson(response)).resolves.toMatchObject({ reviewed_count: 2 });
  });
});

describe('review_batch', () => {
  it('refuses a selection containing another project id and keeps the batch intact', async () => {
    const response = await post(
      reviewBatch,
      '/api/ad_copy_variation/variations/review_batch/',
      {
        project_id: String(fixture.projectA),
        batch_id: batchId,
        selected_ids: [Number(mine), Number(theirs)],
      }
    );

    expect(response.status).toBe(400);
    expect(await survivingIds()).toEqual([mine, alsoMine, theirs].sort());
  });

  it('keeps the selection and drops the rest of the batch', async () => {
    const response = await post(
      reviewBatch,
      '/api/ad_copy_variation/variations/review_batch/',
      {
        project_id: String(fixture.projectA),
        batch_id: batchId,
        selected_ids: [Number(mine)],
      }
    );

    expect(response.status).toBe(200);
    await expect(readJson(response)).resolves.toMatchObject({
      reviewed_count: 1,
      deleted_count: 1,
    });
    expect(await survivingIds()).toEqual([mine, theirs].sort());
  });

  it('requires a batch_id', async () => {
    const response = await post(
      reviewBatch,
      '/api/ad_copy_variation/variations/review_batch/',
      {
        project_id: String(fixture.projectA),
        selected_ids: [Number(mine)],
      }
    );

    expect(response.status).toBe(400);
  });
});
