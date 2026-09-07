import { GET as getVariation } from '@/app/api/ad_copy_variation/variations/[id]/route';
import { POST as generate } from '@/app/api/ad_copy_variation/variations/generate/route';
import { GET as latestBatch } from '@/app/api/ad_copy_variation/variations/latest_batch/route';
import { prisma } from '@/lib/prisma';

import {
  setupStudioFixture,
  teardownStudioFixture,
  type StudioFixture,
} from './support/fixtures';
import { readJson, studioRequest } from './support/requests';
import { accessToken } from './support/tokens';

// Guards run before any model call, but mocking keeps a regression from
// reaching the real Vertex endpoint.
jest.mock('@/src/ai/providers/gemini');

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

function latestBatchFor(projectId: string) {
  return latestBatch(
    studioRequest(
      `/api/ad_copy_variation/variations/latest_batch/?project_id=${projectId}`,
      { token }
    )
  );
}

describe('project membership resolves against the tenant schema', () => {
  // The fixture writes core_projectmember into the org schema only, the same
  // way Django does. Reading membership from `public` would 403 here.
  it('allows a member whose membership row lives in the org schema', async () => {
    const response = await latestBatchFor(String(fixture.projectA));

    expect(response.status).toBe(200);
  });

  it('denies a project the user is not a member of', async () => {
    const response = await latestBatchFor(String(fixture.projectB));

    expect(response.status).toBe(403);
    await expect(readJson(response)).resolves.toEqual({
      error: 'You are not a member of this project.',
    });
  });

  it('rejects a missing project_id', async () => {
    const response = await latestBatch(
      studioRequest('/api/ad_copy_variation/variations/latest_batch/', { token })
    );

    expect(response.status).toBe(400);
  });

  // _require_project resolves a numeric project_id straight to a pk and then
  // runs get_object_or_404, so an unknown number is a 404 while an unknown
  // slug never resolves and falls through to the 400 above.
  it('returns 404 for a numeric project_id that does not exist', async () => {
    const response = await latestBatchFor('999999999');

    expect(response.status).toBe(404);
    await expect(readJson(response)).resolves.toEqual({ detail: 'Not found.' });
  });

  it('returns 400 for a project slug that does not exist', async () => {
    const response = await latestBatchFor('no-such-project');

    expect(response.status).toBe(400);
  });
});

describe('generate requires membership', () => {
  it('denies generating into a project the user is not a member of', async () => {
    const response = await generate(
      studioRequest('/api/ad_copy_variation/variations/generate/', {
        token,
        body: {
          project_id: String(fixture.projectB),
          source_mode: 'custom',
          count: 1,
          base_copy: { hook: 'h', headline: 'H', description: 'd', cta: 'LEARN_MORE' },
        },
      })
    );

    expect(response.status).toBe(403);
  });
});

describe('single variation access', () => {
  function get(idOrSlug: string) {
    return getVariation(
      studioRequest(`/api/ad_copy_variation/variations/${idOrSlug}/`, { token }),
      { params: { id: idOrSlug } }
    );
  }

  it('returns a variation from a project the user belongs to', async () => {
    const response = await get(fixture.draftA.slug);

    expect(response.status).toBe(200);
    await expect(readJson(response)).resolves.toMatchObject({
      id: Number(fixture.draftA.id),
      slug: fixture.draftA.slug,
      project: Number(fixture.projectA),
    });
  });

  it('denies a variation belonging to another project', async () => {
    const response = await get(fixture.draftB.slug);

    expect(response.status).toBe(403);
  });

  it('returns 404 for an unknown slug', async () => {
    const response = await get('no-such-variation');

    expect(response.status).toBe(404);
  });

  // core.slug_mixins.SlugLookupViewSetMixin: "Numeric identifiers are not
  // resolved and yield 404." Resolving them would reopen enumeration by pk.
  it('refuses to resolve a numeric id, even a real one', async () => {
    const response = await get(String(fixture.draftA.id));

    expect(response.status).toBe(404);
  });
});
