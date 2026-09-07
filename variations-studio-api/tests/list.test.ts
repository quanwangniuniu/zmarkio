import { randomUUID } from 'crypto';

import { GET as listVariations } from '@/app/api/ad_copy_variation/variations/route';
import { prisma } from '@/lib/prisma';
import { deleteVariationsByIds } from '@/lib/variationStore';

import {
  createTestVariation,
  setupStudioFixture,
  teardownStudioFixture,
  type StudioFixture,
  type TestVariation,
} from './support/fixtures';
import { readJson, studioRequest } from './support/requests';
import { accessToken } from './support/tokens';

let fixture: StudioFixture;
let token: string;
let batchId: string;
let draft: TestVariation;
let reviewed: TestVariation;
let external: TestVariation;
let withCreative: TestVariation;
let inOtherProject: TestVariation;

beforeAll(async () => {
  fixture = await setupStudioFixture();
  token = await accessToken(fixture.memberUserId);
  batchId = randomUUID();

  draft = await createTestVariation({
    schema: fixture.schema,
    projectId: fixture.projectA,
    userId: fixture.memberUserId,
    status: 'draft',
    batchId,
  });
  reviewed = await createTestVariation({
    schema: fixture.schema,
    projectId: fixture.projectA,
    userId: fixture.memberUserId,
    status: 'reviewed',
  });
  external = await createTestVariation({
    schema: fixture.schema,
    projectId: fixture.projectA,
    userId: fixture.memberUserId,
    status: 'draft',
    sourceMode: 'external_url',
  });
  withCreative = await createTestVariation({
    schema: fixture.schema,
    projectId: fixture.projectA,
    userId: fixture.memberUserId,
    status: 'draft',
    creativeId: fixture.creativeA,
  });
  inOtherProject = await createTestVariation({
    schema: fixture.schema,
    projectId: fixture.projectB,
    userId: fixture.memberUserId,
    status: 'draft',
  });
});

afterAll(async () => {
  await deleteVariationsByIds(
    fixture.schema,
    [draft, reviewed, external, withCreative, inOtherProject].map((row) => row.id)
  );
  await teardownStudioFixture(fixture);
  await prisma.$disconnect();
});

async function list(query: string) {
  const response = await listVariations(
    studioRequest(`/api/ad_copy_variation/variations/?${query}`, { token })
  );
  expect(response.status).toBe(200);
  const body = await readJson(response);
  const results = body.results as { id: number; slug: string }[];
  return { body, ids: results.map((row) => BigInt(row.id)) };
}

const projectA = () => `project_id=${fixture.projectA}`;

describe('list filters', () => {
  it('scopes results to the requested project', async () => {
    const { ids } = await list(projectA());

    expect(ids).toContain(draft.id);
    expect(ids).not.toContain(inOtherProject.id);
  });

  it('filters by a single status', async () => {
    const { ids } = await list(`${projectA()}&status=reviewed`);

    expect(ids).toContain(reviewed.id);
    expect(ids).not.toContain(draft.id);
  });

  it('accepts a comma-separated status list', async () => {
    const { ids } = await list(`${projectA()}&status=draft,reviewed`);

    expect(ids).toContain(draft.id);
    expect(ids).toContain(reviewed.id);
  });

  it('filters by source_mode', async () => {
    const { ids } = await list(`${projectA()}&source_mode=external_url`);

    expect(ids).toEqual([external.id]);
  });

  it('filters by creative', async () => {
    const { ids } = await list(`${projectA()}&creative=${fixture.creativeA}`);

    expect(ids).toEqual([withCreative.id]);
  });

  it('filters by batch_id', async () => {
    const { ids } = await list(`${projectA()}&batch_id=${batchId}`);

    expect(ids).toEqual([draft.id]);
  });

  it('paginates and reports the unpaginated count', async () => {
    const first = await list(`${projectA()}&page=1&page_size=2`);

    expect(first.ids).toHaveLength(2);
    expect(first.body.page).toBe(1);
    expect(first.body.page_size).toBe(2);
    expect(first.body.count as number).toBeGreaterThan(2);

    const second = await list(`${projectA()}&page=2&page_size=2`);
    expect(second.ids).not.toEqual(first.ids);
  });

  it('falls back to the projects the user belongs to when project_id is absent', async () => {
    const { ids } = await list('page_size=100');

    expect(ids).toContain(draft.id);
    expect(ids).not.toContain(inOtherProject.id);
  });
});
