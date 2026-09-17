import { POST as generate } from '@/app/api/ad_copy_variation/variations/generate/route';
import { callGeminiJson, isGeminiQuotaError } from '@/src/ai/providers/gemini';
import { randomUUID } from 'crypto';

import { allocateSlugs } from '@/lib/slugs';
import { prisma } from '@/lib/prisma';
import {
  countVariations,
  findVariationsByIdsAnyProject,
  insertVariations,
  type SqlClient,
  type VariationInsert,
} from '@/lib/variationStore';

import {
  createTestVariation,
  setupStudioFixture,
  teardownStudioFixture,
  type StudioFixture,
} from './support/fixtures';
import { readJson, studioRequest } from './support/requests';
import { accessToken } from './support/tokens';

jest.mock('@/src/ai/providers/gemini', () => ({
  callGeminiJson: jest.fn(),
  isGeminiQuotaError: jest.fn(() => false),
}));

jest.mock('@/lib/slugs', () => {
  const actual = jest.requireActual('@/lib/slugs');
  return { ...actual, allocateSlugs: jest.fn(actual.allocateSlugs) };
});

const slugsMock = allocateSlugs as jest.MockedFunction<typeof allocateSlugs>;
const geminiMock = callGeminiJson as jest.MockedFunction<typeof callGeminiJson>;
const quotaMock = isGeminiQuotaError as jest.MockedFunction<
  typeof isGeminiQuotaError
>;

let fixture: StudioFixture;
let token: string;

beforeAll(async () => {
  fixture = await setupStudioFixture();
  token = await accessToken(fixture.memberUserId);
});

afterAll(async () => {
  await teardownStudioFixture(fixture);
  await prisma.$disconnect();
});

beforeEach(() => {
  jest.clearAllMocks();
  quotaMock.mockReturnValue(false);
});

function copy(label: string) {
  return {
    hook: `${label} hook`,
    headline: `${label} headline`,
    description: `${label} description`,
    cta: 'LEARN_MORE',
  };
}

function generateBatch(count: number) {
  return generate(
    studioRequest('/api/ad_copy_variation/variations/generate/', {
      token,
      body: {
        project_id: String(fixture.projectA),
        source_mode: 'custom',
        count,
        base_copy: copy('Base'),
      },
    })
  );
}

describe('batch generate failure handling', () => {
  it('reports a partial failure as 200 and persists only what succeeded', async () => {
    // Concurrency means call order does not map to a fixed index, so this
    // asserts on the tallies rather than on which positions failed.
    let call = 0;
    geminiMock.mockImplementation(async () => {
      call += 1;
      if (call > 1) throw new Error('model exploded');
      return copy('Generated');
    });

    const response = await generateBatch(4);

    expect(response.status).toBe(200);
    const body = await readJson(response);
    expect(body).toMatchObject({
      count_requested: 4,
      count_succeeded: 1,
      count_failed: 3,
    });
    expect(body.results).toHaveLength(1);
    expect(body.failed_indices).toHaveLength(3);

    const results = body.results as { id: number }[];
    const persisted = await findVariationsByIdsAnyProject(
      fixture.schema,
      results.map((row) => BigInt(row.id))
    );
    expect(persisted).toHaveLength(1);
  });

  it('returns 502 and persists nothing when the whole batch fails', async () => {
    geminiMock.mockRejectedValue(new Error('model exploded'));
    const before = await countVariations(fixture.schema, {
      projectId: fixture.projectA,
    });

    const response = await generateBatch(3);

    expect(response.status).toBe(502);
    await expect(readJson(response)).resolves.toMatchObject({
      count_requested: 3,
      count_succeeded: 0,
      count_failed: 3,
      results: [],
    });

    const after = await countVariations(fixture.schema, {
      projectId: fixture.projectA,
    });
    expect(after).toBe(before);
  });

  it('surfaces a quota exhaustion as its own message', async () => {
    geminiMock.mockRejectedValue(new Error('429 RESOURCE_EXHAUSTED'));
    quotaMock.mockReturnValue(true);

    const response = await generateBatch(2);

    expect(response.status).toBe(502);
    const body = await readJson(response);
    expect(body.count_succeeded).toBe(0);
    expect(typeof body.error).toBe('string');
    expect(body.error as string).toMatch(/quota/i);
  });

  it('keeps every batch member on one shared batch_id', async () => {
    geminiMock.mockResolvedValue(copy('Generated'));

    const response = await generateBatch(3);

    expect(response.status).toBe(200);
    const body = await readJson(response);
    const results = body.results as { id: number }[];
    const rows = await findVariationsByIdsAnyProject(
      fixture.schema,
      results.map((row) => BigInt(row.id))
    );

    expect(new Set(rows.map((row) => row.batchId)).size).toBe(1);
    expect(rows[0].batchId).toBe(body.batch_id);
    expect(rows.map((row) => row.batchPosition).sort()).toEqual([0, 1, 2]);
  });
});

describe('batch persistence', () => {
  function insertRow(position: number, label: string): VariationInsert {
    return {
      sourceMode: 'custom',
      sourceRef: '',
      hook: `${label} hook`,
      headline: `${label} headline`,
      description: `${label} description`,
      cta: 'LEARN_MORE',
      instruction: '',
      modelName: 'fixture',
      promptVersion: 'fixture',
      batchId: randomUUID(),
      batchPosition: position,
      status: 'draft',
      createdById: BigInt(fixture.memberUserId),
      creativeId: null,
      projectId: fixture.projectA,
      slug: `studio-test-${randomUUID()}`,
    };
  }

  it('persists nothing when row 3 of 5 fails to insert', async () => {
    geminiMock.mockResolvedValue(copy('Generated'));
    const existing = await createTestVariation({
      schema: fixture.schema,
      projectId: fixture.projectA,
      userId: fixture.memberUserId,
      status: 'draft',
    });
    // Collide row 3 with an existing slug so the unique constraint rejects it
    // after rows 1 and 2 would already have been written by a per-row insert.
    slugsMock.mockImplementationOnce(async (_schema, headlines) =>
      headlines.map((_, index) =>
        index === 2 ? existing.slug : `studio-test-${randomUUID()}`
      )
    );
    const before = await countVariations(fixture.schema, {
      projectId: fixture.projectA,
    });

    await expect(generateBatch(5)).rejects.toThrow();

    const after = await countVariations(fixture.schema, {
      projectId: fixture.projectA,
    });
    expect(after).toBe(before);
  });

  it('writes a whole batch in a single statement', async () => {
    let statements = 0;
    const countingClient: SqlClient = {
      $queryRaw: ((...args: Parameters<typeof prisma.$queryRaw>) => {
        statements += 1;
        return prisma.$queryRaw(...args);
      }) as typeof prisma.$queryRaw,
      $executeRaw: prisma.$executeRaw.bind(prisma),
    };
    const rows = [0, 1, 2, 3, 4].map((position) => insertRow(position, `Row ${position}`));

    const saved = await insertVariations(fixture.schema, rows, countingClient);

    expect(statements).toBe(1);
    expect(saved).toHaveLength(5);
  });

  it('returns rows in batch_position order regardless of input order', async () => {
    const rows = [4, 3, 2, 1, 0].map((position) => insertRow(position, `Row ${position}`));

    const saved = await insertVariations(fixture.schema, rows);

    expect(saved.map((row) => row.batchPosition)).toEqual([0, 1, 2, 3, 4]);
    expect(saved.map((row) => row.headline)).toEqual(
      [0, 1, 2, 3, 4].map((position) => `Row ${position} headline`)
    );
  });

  it('returns generate results in batch_position order', async () => {
    geminiMock.mockResolvedValue(copy('Generated'));

    const response = await generateBatch(5);

    expect(response.status).toBe(200);
    const body = await readJson(response);
    const results = body.results as { batch_position: number }[];
    expect(results.map((row) => row.batch_position)).toEqual([0, 1, 2, 3, 4]);
  });
});
