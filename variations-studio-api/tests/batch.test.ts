import { POST as generate } from '@/app/api/ad_copy_variation/variations/generate/route';
import { callGeminiJson, isGeminiQuotaError } from '@/src/ai/providers/gemini';
import { prisma } from '@/lib/prisma';
import {
  countVariations,
  findVariationsByIdsAnyProject,
} from '@/lib/variationStore';

import {
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

describe('copy length validation retry', () => {
  it('retries once and persists the valid retry result', async () => {
    geminiMock
      .mockResolvedValueOnce({
        hook:
          'one two three four five six seven eight nine ten eleven',
        headline: 'First headline',
        description: 'First description',
        cta: 'LEARN_MORE',
      })
      .mockResolvedValueOnce(copy('Retried'));

    const response = await generateBatch(1);

    expect(response.status).toBe(200);
    expect(geminiMock).toHaveBeenCalledTimes(2);

    const body = await readJson(response);

    const results = body.results as Array<{
      hook: string;
      headline: string;
      validation_warnings: unknown[];
    }>;

    expect(body.results).toHaveLength(1);
    expect(results[0]).toMatchObject({
      hook: 'Retried hook',
      headline: 'Retried headline',
      validation_warnings: [],
    });
  });

  it('persists field warnings when the retry still exceeds limits', async () => {
    const overlongCopy = {
      hook:
        'one two three four five six seven eight nine ten eleven',
      headline: 'H'.repeat(41),
      description: 'D'.repeat(126),
      cta: 'LEARN_MORE',
    };

    geminiMock
      .mockResolvedValueOnce(overlongCopy)
      .mockResolvedValueOnce(overlongCopy);

    const response = await generateBatch(1);

    expect(response.status).toBe(200);
    expect(geminiMock).toHaveBeenCalledTimes(2);

    const body = await readJson(response);

    const results = body.results as Array<{
      validation_warnings: unknown[];
    }>;

    expect(body.results).toHaveLength(1);
    expect(results[0].validation_warnings).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          field: 'hook',
          rule: 'max_words',
          limit: 10,
          actual: 11,
        }),
        expect.objectContaining({
          field: 'headline',
          rule: 'max_chars',
          limit: 40,
          actual: 41,
        }),
        expect.objectContaining({
          field: 'description',
          rule: 'max_chars',
          limit: 125,
          actual: 126,
        }),
      ])
    );
  });
});

describe('batch slug allocation', () => {
  it('persists two concurrent identical 50-item batches with unique slugs', async () => {
    geminiMock.mockResolvedValue(copy('Same'));

    const [firstResponse, secondResponse] = await Promise.all([
      generateBatch(50),
      generateBatch(50),
    ]);

    expect(firstResponse.status).toBe(200);
    expect(secondResponse.status).toBe(200);

    const [firstBody, secondBody] = await Promise.all([
      readJson(firstResponse),
      readJson(secondResponse),
    ]);

    expect(firstBody.count_succeeded).toBe(50);
    expect(secondBody.count_succeeded).toBe(50);

    const results = [
      ...(firstBody.results as { slug: string }[]),
      ...(secondBody.results as { slug: string }[]),
    ];

    expect(results).toHaveLength(100);
    expect(new Set(results.map((row) => row.slug)).size).toBe(100);
  });
});
