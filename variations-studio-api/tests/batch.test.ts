import { randomUUID } from 'crypto';
import { setImmediate } from 'node:timers/promises';
import { POST as generate } from '@/app/api/ad_copy_variation/variations/generate/route';
import { callOllamaJson, getOllamaErrorMessage, } from '@/src/ai/providers/ollama';
import type { CopyJson } from '@/src/ai/types';
import { prisma } from '@/lib/prisma';
import {
  countVariations,
  findVariationsByIdsAnyProject,
  findBatchVariations,
  insertVariations,
} from '@/lib/variationStore';

import {
  setupStudioFixture,
  teardownStudioFixture,
  type StudioFixture,
} from './support/fixtures';
import { readJson, studioRequest } from './support/requests';
import { accessToken } from './support/tokens';

jest.mock('@/src/ai/providers/ollama', () => {
  const actual = jest.requireActual('@/src/ai/providers/ollama');

  return {
    ...actual,
    callOllamaJson: jest.fn(),
    getOllamaConfig: jest.fn(() => ({
      baseUrl: 'http://ollama.test:11434',
      model: 'test-model',
      timeoutMs: 5000,
    })),
    getOllamaErrorMessage: jest.fn(),
  };
});

const ollamaMock = callOllamaJson as jest.MockedFunction<
  typeof callOllamaJson
>;

const errorMessageMock = getOllamaErrorMessage as jest.MockedFunction<
  typeof getOllamaErrorMessage
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
  ollamaMock.mockReset();
  errorMessageMock.mockImplementation((error) =>
    error instanceof Error ? error.message : null
  );
});

function copy(label: string) {
  return {
    hook: `${label} hook`,
    headline: `${label} headline`,
    description: `${label} description`,
    cta: 'LEARN_MORE',
  };
}

// Workers send first attempts in request order with the same user prompt.
// Retries append field feedback to that prompt, so their responses can be matched in any order.
function mockBatchReplies(firstPass: CopyJson[], retry: (feedback: string) => CopyJson | Promise<CopyJson>) {
  let nextItem = 0;
  ollamaMock.mockImplementation(async (_system, prompt) => {
    const original = ollamaMock.mock.calls[0][1];
    if (prompt === original) return firstPass[nextItem++];
    return retry(prompt.slice(original.length));
  });
}

function generateBatch(count: number, options: { platform?: unknown } = {}) {
  return generate(
    studioRequest('/api/ad_copy_variation/variations/generate/', {
      token,
      body: {
        project_id: String(fixture.projectA),
        source_mode: 'custom',
        ...options,
        count,
        base_copy: copy('Base'),
      },
    })
  );
}

describe('generate platform selection', () => {
  it.each([null, ''])('uses Meta for platform %j', async (platform) => {
    ollamaMock.mockResolvedValue(copy('Generated'));
    const response = await generateBatch(1, { platform });
    expect(response.status).toBe(200);
    await expect(readJson(response)).resolves.toMatchObject({ results: [{ platform: 'meta' }] });
  });

  it.each(['unregistered', 42])('rejects platform %j before calling the model', async (platform) => {
    const response = await generateBatch(1, { platform });
    expect(response.status).toBe(400);
    await expect(readJson(response)).resolves.toEqual({ error: 'platform must be a registered platform' });
    expect(ollamaMock).not.toHaveBeenCalled();
  });
});

describe('batch generate failure handling', () => {
  it('reports a partial failure as 200 and persists only what succeeded', async () => {
    // This case checks provider failure counts regardless of which item succeeds.
    let call = 0;
    ollamaMock.mockImplementation(async () => {
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
    expect(body.results).toEqual([expect.objectContaining({ platform: 'meta' })]);
    expect(body.failed_indices).toHaveLength(3);
    expect(body).not.toHaveProperty('field_failures');

    const results = body.results as { id: number }[];
    const persisted = await findVariationsByIdsAnyProject(
      fixture.schema,
      results.map((row) => BigInt(row.id))
    );
    expect(persisted).toHaveLength(1);
  });

  it('retries only over-limit items and saves the two compliant copies', async () => {
    const direct = copy('Direct');
    const corrected = copy('Corrected');
    mockBatchReplies([
      direct,
      { ...copy('Rejected'), hook: Array(11).fill('a').join(' ') },
      { ...copy('First draft'), headline: 'x'.repeat(41) },
    ], feedback => feedback.includes('hook:')
      ? { ...copy('Rejected again'), description: 'x'.repeat(126) }
      : corrected);

    const response = await generateBatch(3);
    expect(response.status).toBe(200);
    const body = await readJson(response);
    expect(body).toMatchObject({
      count_succeeded: 2, count_failed: 1, failed_indices: [1],
      field_failures: [
        { index: 1, violations: [{ field: 'description', rule: 'max_chars', actual: 126, limit: 125 }] },
      ],
    });
    expect(ollamaMock).toHaveBeenCalledTimes(5);
    const [system, original] = ollamaMock.mock.calls[0];
    const retries = ollamaMock.mock.calls.filter(([, prompt]) => prompt !== original);
    const feedback = retries.map(([systemPrompt, prompt]) => {
      expect(systemPrompt).toBe(system);
      expect(prompt.startsWith(original)).toBe(true);
      return prompt.slice(original.length);
    });
    const hookFeedback = feedback.find(text => text.includes('hook:'));
    const headlineFeedback = feedback.find(text => text.includes('headline:'));
    expect(hookFeedback).toContain('hook: 11 words (maximum 10)');
    expect(hookFeedback).not.toMatch(/headline|description|cta/);
    expect(headlineFeedback).toContain('headline: 41 characters (maximum 40)');
    expect(headlineFeedback).not.toMatch(/hook|description|cta/);

    const rows = await findBatchVariations(fixture.schema, fixture.projectA, String(body.batch_id));
    expect(rows).toMatchObject([{ ...direct, batchPosition: 0 }, { ...corrected, batchPosition: 1 }]);
  });

  it('returns 502 with both hook violations when only length checks fail', async () => {
    ollamaMock.mockResolvedValue({ ...copy('Long'), hook: Array(11).fill('word').join(' ') });
    const response = await generateBatch(1);
    expect(response.status).toBe(502);
    const body = await readJson(response);
    expect(body).toMatchObject({
      count_succeeded: 0, count_failed: 1, results: [], failed_indices: [0],
      field_failures: [{ index: 0, violations: [
        { field: 'hook', rule: 'max_words', actual: 11, limit: 10 },
        { field: 'hook', rule: 'max_chars', actual: 54, limit: 50 },
      ] }],
    });
    expect(body).not.toHaveProperty('error');
    expect(ollamaMock).toHaveBeenCalledTimes(2);
    await expect(findBatchVariations(fixture.schema, fixture.projectA, String(body.batch_id)))
      .resolves.toHaveLength(0);
  });

  it('returns 502 with sorted failures and saves no drafts after mixed retry failures', async () => {
    const longHeadline = { ...copy('First'), headline: 'a'.repeat(41) };
    const longDescription = { ...copy('Third'), description: 'b'.repeat(127) };
    mockBatchReplies([
      longHeadline,
      { ...copy('Second'), hook: Array(11).fill('a').join(' ') },
      longDescription,
    ], async feedback => {
      if (feedback.includes('headline:')) {
        // Let the other retries finish before item 0 returns its headline.
        await setImmediate();
        return longHeadline;
      }
      if (feedback.includes('description:')) return longDescription;
      throw new Error('Ollama disconnected on retry');
    });

    const response = await generateBatch(3);
    expect(response.status).toBe(502);
    const body = await readJson(response);
    expect(body).toMatchObject({
      count_succeeded: 0, count_failed: 3, results: [], failed_indices: [0, 1, 2],
      field_failures: [
        { index: 0, violations: [{ field: 'headline', rule: 'max_chars', actual: 41, limit: 40 }] },
        { index: 2, violations: [{ field: 'description', rule: 'max_chars', actual: 127, limit: 125 }] },
      ],
      error: 'Ollama disconnected on retry',
    });
    expect(ollamaMock).toHaveBeenCalledTimes(6);
    await expect(findBatchVariations(fixture.schema, fixture.projectA, String(body.batch_id)))
      .resolves.toHaveLength(0);
  });

  it('returns 502 and persists nothing when the whole batch fails', async () => {
    ollamaMock.mockRejectedValue(new Error('model exploded'));
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

  it('surfaces the Ollama provider error', async () => {
    ollamaMock.mockRejectedValue(
      new Error('Unable to connect to Ollama.')
    );
    errorMessageMock.mockReturnValue(
      'Unable to connect to Ollama.'
    );

    const response = await generateBatch(2);

    expect(response.status).toBe(502);
    const body = await readJson(response);

    expect(body).toMatchObject({
      count_requested: 2,
      count_succeeded: 0,
      count_failed: 2,
      results: [],
      error: 'Unable to connect to Ollama.',
    });
  });

  it('keeps every batch member on one shared batch_id', async () => {
    ollamaMock
      .mockResolvedValueOnce(copy('Zebra'))
      .mockResolvedValueOnce(copy('Apple'))
      .mockResolvedValueOnce(copy('Mango'));

    const response = await generateBatch(3);

    expect(response.status).toBe(200);
    const body = await readJson(response);
    const results = body.results as { id: number; batch_position: number }[];
    expect(results.map((row) => row.batch_position)).toEqual([0, 1, 2]);
    const rows = await findVariationsByIdsAnyProject(
      fixture.schema,
      results.map((row) => BigInt(row.id))
    );

    expect(new Set(rows.map((row) => row.batchId)).size).toBe(1);
    expect(rows[0].batchId).toBe(body.batch_id);
    expect(rows.map((row) => row.batchPosition).sort()).toEqual([0, 1, 2]);
  });
});

describe('batch slug allocation', () => {
  it('persists two concurrent identical 50-item batches with unique slugs', async () => {
    ollamaMock.mockResolvedValue(copy('Same'));

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
    results.forEach((row) => expect(row.slug).toMatch(/\S/));
    expect(new Set(results.map((row) => row.slug)).size).toBe(100);
  });
});

describe('batch persistence atomicity', () => {
  it('persists nothing when row 3 of 5 fails', async () => {
    const batchId = randomUUID();
    const rows = Array.from({ length: 5 }, (_, index) => ({
      platform: 'meta',
      fields: copy(`Atomic ${index}`),
      sourceMode: 'custom',
      sourceRef: '',
      instruction: '',
      modelName: 'test-model',
      promptVersion: 'v1',
      batchId,
      batchPosition: index,
      status: 'draft',
      createdById: BigInt(fixture.memberUserId),
      creativeId: null,
      projectId: fixture.projectA,
      slug: `atomic-${batchId}-${index}`,
    }));
    rows[2].fields.cta = 'X'.repeat(65);

    await expect(insertVariations(fixture.schema, rows)).rejects.toThrow();
    await expect(
      findBatchVariations(fixture.schema, fixture.projectA, batchId)
    ).resolves.toHaveLength(0);
  });
});
