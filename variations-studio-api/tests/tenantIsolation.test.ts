import { POST as generate } from '@/app/api/ad_copy_variation/variations/generate/route';
import { POST as createVariation } from '@/app/api/ad_copy_variation/variations/route';
import { prisma } from '@/lib/prisma';
import { findVariationById } from '@/lib/variationStore';
import { callOllamaJson } from '@/src/ai/providers/ollama';

import { readJson, studioRequest } from './support/requests';
import {
  countInSchema,
  setupCreativeBindingFixture,
  teardownCreativeBindingFixture,
} from './support/tenantRoutingFixtures';
import { accessToken } from './support/tokens';

jest.mock('@/src/ai/providers/ollama', () => {
  const actual = jest.requireActual('@/src/ai/providers/ollama');

  return {
    ...actual,
    callOllamaJson: jest.fn(async () => ({
      hook: 'Tenant hook',
      headline: 'Tenant headline',
      description: 'Tenant description',
      cta: 'LEARN_MORE',
    })),
    getOllamaConfig: jest.fn(() => ({
      baseUrl: 'http://ollama.test:11434',
      model: 'test-model',
      timeoutMs: 5000,
    })),
  };
});

let fixture: Awaited<ReturnType<typeof setupCreativeBindingFixture>>;
let token: string;

beforeAll(async () => {
  fixture = await setupCreativeBindingFixture();
  token = await accessToken(fixture.memberUserId);
  expect(fixture.projectA).toBe(fixture.projectB);
  expect(fixture.orgA.id).not.toBe(fixture.orgB.id);
  expect(fixture.memberUserId).not.toBe(fixture.connectorUserId);
  await expect(prisma.customUser.findUniqueOrThrow({
    where: { id: BigInt(fixture.connectorUserId) },
    select: { currentOrganizationId: true },
  })).resolves.toEqual({ currentOrganizationId: fixture.orgB.id });
});

beforeEach(() => {
  jest.mocked(callOllamaJson).mockClear();
});

afterAll(async () => {
  if (fixture) await teardownCreativeBindingFixture(fixture);
  await prisma.$disconnect();
});

describe('tenant schema isolation', () => {
  it('persists generate results in the org schema', async () => {
    const response = await generate(
      studioRequest('/api/ad_copy_variation/variations/generate/', {
        token,
        body: {
          project_id: String(fixture.projectA),
          source_mode: 'custom',
          count: 1,
          base_copy: {
            hook: 'Base hook',
            headline: 'Base headline',
            description: 'Base description',
            cta: 'LEARN_MORE',
          },
        },
      })
    );

    expect(response.status).toBe(200);
    const body = await readJson(response);
    const id = BigInt((body.results as { id: number }[])[0].id);

    await expect(findVariationById(fixture.orgA.schema, id)).resolves.toMatchObject({
      id,
      sourceMode: 'custom',
    });
  });
});

function generateFromCreative(creativeId: bigint) {
  return generate(studioRequest('/api/ad_copy_variation/variations/generate/', {
    token,
    body: {
      project_id: String(fixture.projectA),
      source_mode: 'existing',
      creative_id: String(creativeId),
      count: 1,
    },
  }));
}

function createWithCreative(creativeId: bigint) {
  return createVariation(studioRequest('/api/ad_copy_variation/variations/', {
    token,
    body: {
      project: String(fixture.projectA),
      source_mode: 'existing',
      creative: String(creativeId),
      hook: 'Manual draft',
    },
  }));
}

async function expectOwnDraft(id: number) {
  await expect(findVariationById(fixture.orgA.schema, BigInt(id))).resolves.toMatchObject({
    projectId: fixture.projectA,
    creativeId: fixture.creatives.own,
    createdById: BigInt(fixture.memberUserId),
    sourceMode: 'existing',
    status: 'draft',
  });
}

function projectDraftCounts() {
  return Promise.all([
    countInSchema(fixture.orgA.schema, fixture.projectA),
    countInSchema(fixture.orgB.schema, fixture.projectB),
  ]);
}

describe('ad account binding for Studio creatives', () => {
  it('lets a project member generate with a bound creative after the connector switches orgs', async () => {
    const response = await generateFromCreative(fixture.creatives.own);
    expect(response.status).toBe(200);
    const body = await readJson(response);
    expect(body).toMatchObject({ count_succeeded: 1, count_failed: 0 });
    expect(callOllamaJson).toHaveBeenCalledTimes(1);
    await expectOwnDraft((body.results as { id: number }[])[0].id);
  });

  it('lets a project member save a draft with a bound creative after the connector switches orgs', async () => {
    const response = await createWithCreative(fixture.creatives.own);
    expect(response.status).toBe(201);
    const body = await readJson(response);
    await expectOwnDraft(body.id as number);
  });

  describe.each([
    { entry: 'existing-mode generate', submit: generateFromCreative },
    { entry: 'manual draft creation', submit: createWithCreative },
  ])('$entry', ({ submit }) => {
    it.each([
      { binding: 'unbound', reason: 'the account has no linked project' },
      { binding: 'otherOrg', reason: 'the same project number is bound in another org' },
      { binding: 'legacy', reason: 'the linked account has no recorded org' },
    ] as const)(
      'rejects the creative without creating a draft when $reason',
      async ({ binding }) => {
        const before = await projectDraftCounts();
        const response = await submit(fixture.creatives[binding]);
        expect(response.status).toBe(404);
        await expect(readJson(response)).resolves.toEqual({ detail: 'Not found.' });
        expect(callOllamaJson).not.toHaveBeenCalled();
        await expect(projectDraftCounts()).resolves.toEqual(before);
      }
    );
  });
});
