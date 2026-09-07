import { PATCH as patchVariation } from '@/app/api/ad_copy_variation/variations/[id]/route';
import { POST as generate } from '@/app/api/ad_copy_variation/variations/generate/route';
import { prisma } from '@/lib/prisma';
import { MAX_BATCH } from '@/lib/prompts';
import { findVariationById, findVariationsByIdsAnyProject } from '@/lib/variationStore';

import {
  setupStudioFixture,
  teardownStudioFixture,
  type StudioFixture,
} from './support/fixtures';
import { readJson, studioRequest } from './support/requests';
import { accessToken } from './support/tokens';

jest.mock('@/src/ai/providers/gemini', () => ({
  callGeminiJson: jest.fn(async () => ({
    hook: 'Mocked hook',
    headline: 'Mocked headline',
    description: 'Mocked description',
    cta: 'LEARN_MORE',
  })),
  isGeminiQuotaError: jest.fn(() => false),
}));

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

function generateRequest(body: Record<string, unknown>) {
  return generate(
    studioRequest('/api/ad_copy_variation/variations/generate/', { token, body })
  );
}

function customBody(overrides: Record<string, unknown> = {}) {
  return {
    project_id: String(fixture.projectA),
    source_mode: 'custom',
    count: 1,
    base_copy: {
      hook: 'Base hook',
      headline: 'Base headline',
      description: 'Base description',
      cta: 'LEARN_MORE',
    },
    ...overrides,
  };
}

describe('generate input validation', () => {
  it('rejects an unknown source_mode', async () => {
    const response = await generateRequest(customBody({ source_mode: 'telepathy' }));

    expect(response.status).toBe(400);
  });

  it('rejects a non-integer count', async () => {
    const response = await generateRequest(customBody({ count: 'a lot' }));

    expect(response.status).toBe(400);
    await expect(readJson(response)).resolves.toEqual({
      error: 'count must be an integer',
    });
  });

  it('rejects a count above the batch ceiling', async () => {
    const response = await generateRequest(customBody({ count: MAX_BATCH + 1 }));

    expect(response.status).toBe(400);
  });

  it('rejects a count below one', async () => {
    const response = await generateRequest(customBody({ count: 0 }));

    expect(response.status).toBe(400);
  });

  it('rejects a malformed JSON body', async () => {
    const request = new Request(
      'http://studio.test/api/ad_copy_variation/variations/generate/',
      {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: '{not json',
      }
    );

    expect((await generate(request)).status).toBe(400);
  });
});

describe('generate creative ownership', () => {
  it('rejects a creative owned by a different project', async () => {
    const response = await generateRequest(
      customBody({ source_mode: 'existing', creative_id: Number(fixture.creativeB) })
    );

    expect(response.status).toBe(400);
    await expect(readJson(response)).resolves.toEqual({
      error: 'creative_id does not belong to project_id',
    });
  });

  it('requires a creative_id when source_mode is existing', async () => {
    const response = await generateRequest(customBody({ source_mode: 'existing' }));

    expect(response.status).toBe(400);
  });

  it('returns 404 for a creative that does not exist', async () => {
    const response = await generateRequest(
      customBody({ source_mode: 'existing', creative_id: 999_999_999 })
    );

    expect(response.status).toBe(404);
  });

  it('accepts a creative reachable from the project', async () => {
    const response = await generateRequest(
      customBody({ source_mode: 'existing', creative_id: Number(fixture.creativeA) })
    );

    expect(response.status).toBe(200);
    const payload = await readJson(response);
    expect(payload).toMatchObject({ count_succeeded: 1, count_failed: 0 });

    const results = payload.results as { id: number }[];
    const saved = await findVariationsByIdsAnyProject(fixture.schema, [
      BigInt(results[0].id),
    ]);
    expect(saved).toHaveLength(1);
    expect(saved[0].createdById).toBe(BigInt(fixture.memberUserId));
    expect(saved[0].creativeId).toBe(fixture.creativeA);
  });
});

describe('PATCH field validation', () => {
  function patch(body: Record<string, unknown>) {
    return patchVariation(
      studioRequest(`/api/ad_copy_variation/variations/${fixture.draftA.slug}/`, {
        token,
        method: 'PATCH',
        body,
      }),
      { params: { id: fixture.draftA.slug } }
    );
  }

  it('refuses to move a variation to another project', async () => {
    const response = await patch({ project: Number(fixture.projectB) });

    expect(response.status).toBe(400);

    const row = await findVariationById(fixture.schema, fixture.draftA.id);
    expect(row?.projectId).toBe(fixture.projectA);
  });

  it('rejects a status outside the allowed set', async () => {
    const response = await patch({ status: 'published' });

    expect(response.status).toBe(400);
  });

  it('rejects a non-string writable field', async () => {
    const response = await patch({ headline: 42 });

    expect(response.status).toBe(400);
  });

  it('accepts an allowed edit', async () => {
    const response = await patch({ headline: 'Edited headline', status: 'reviewed' });

    expect(response.status).toBe(200);
    await expect(readJson(response)).resolves.toMatchObject({
      headline: 'Edited headline',
      status: 'reviewed',
    });
  });
});
