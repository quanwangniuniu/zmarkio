import { POST as generate } from '@/app/api/ad_copy_variation/variations/generate/route';
import { callOllamaJson, getOllamaErrorMessage, } from '@/src/ai/providers/ollama';
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
    ollamaMock.mockResolvedValue(copy('Generated'));

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
    expect(new Set(results.map((row) => row.slug)).size).toBe(100);
  });
});
