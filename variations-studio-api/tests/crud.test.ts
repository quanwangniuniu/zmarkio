import { DELETE as deleteVariation } from '@/app/api/ad_copy_variation/variations/[id]/route';
import { POST as createVariation } from '@/app/api/ad_copy_variation/variations/route';
import { prisma } from '@/lib/prisma';
import { findVariationById } from '@/lib/variationStore';

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

beforeAll(async () => {
  fixture = await setupStudioFixture();
  token = await accessToken(fixture.memberUserId);
});

afterAll(async () => {
  await teardownStudioFixture(fixture);
  await prisma.$disconnect();
});

function create(body: Record<string, unknown>) {
  return createVariation(
    studioRequest('/api/ad_copy_variation/variations/', { token, body })
  );
}

// Django's create reads the project from `project`, not `project_id`.
function payload(overrides: Record<string, unknown> = {}) {
  return {
    project: Number(fixture.projectA),
    source_mode: 'custom',
    hook: 'Created hook',
    headline: 'Created headline',
    description: 'Created description',
    cta: 'LEARN_MORE',
    ...overrides,
  };
}

describe('POST /variations/', () => {
  it('creates a draft and stamps the requesting user', async () => {
    const response = await create(payload());

    expect(response.status).toBe(201);
    const body = await readJson(response);
    expect(body).toMatchObject({
      project: Number(fixture.projectA),
      created_by: fixture.memberUserId,
      status: 'draft',
      hook: 'Created hook',
    });
    expect(typeof body.slug).toBe('string');

    const row = await findVariationById(fixture.schema, BigInt(body.id as number));
    expect(row?.createdById).toBe(BigInt(fixture.memberUserId));
    expect(row?.modelName).toBe('gemini-2.5-flash-lite');
  });

  it('rejects a missing project', async () => {
    const response = await create(payload({ project: undefined }));

    expect(response.status).toBe(400);
  });

  it('denies a project the user is not a member of', async () => {
    const response = await create(payload({ project: Number(fixture.projectB) }));

    expect(response.status).toBe(403);
  });

  it('rejects an unknown source_mode', async () => {
    const response = await create(payload({ source_mode: 'telepathy' }));

    expect(response.status).toBe(400);
  });

  it('rejects a creative owned by another project', async () => {
    const response = await create(payload({ creative: Number(fixture.creativeB) }));

    expect(response.status).toBe(400);
    await expect(readJson(response)).resolves.toEqual({
      error: 'creative does not belong to project',
    });
  });

  it('accepts a creative reachable from the project', async () => {
    const response = await create(payload({ creative: Number(fixture.creativeA) }));

    expect(response.status).toBe(201);
    await expect(readJson(response)).resolves.toMatchObject({
      creative: Number(fixture.creativeA),
    });
  });

  it('returns 404 for a creative that does not exist', async () => {
    const response = await create(payload({ creative: 999_999_999 }));

    expect(response.status).toBe(404);
  });

  it('rejects a status outside the allowed set', async () => {
    const response = await create(payload({ status: 'published' }));

    expect(response.status).toBe(400);
  });

  it('rejects an unauthenticated create', async () => {
    const response = await createVariation(
      studioRequest('/api/ad_copy_variation/variations/', { body: payload() })
    );

    expect(response.status).toBe(401);
  });
});

describe('DELETE /variations/{slug}/', () => {
  function remove(slug: string) {
    return deleteVariation(
      studioRequest(`/api/ad_copy_variation/variations/${slug}/`, {
        token,
        method: 'DELETE',
      }),
      { params: { id: slug } }
    );
  }

  it('deletes a variation in the user\u2019s project', async () => {
    const row = await createTestVariation({
      schema: fixture.schema,
      projectId: fixture.projectA,
      userId: fixture.memberUserId,
      status: 'draft',
    });

    const response = await remove(row.slug);

    expect(response.status).toBe(204);
    const found = await findVariationById(fixture.schema, row.id);
    expect(found).toBeNull();
  });

  it('refuses to delete a variation from another project', async () => {
    const response = await remove(fixture.draftB.slug);

    expect(response.status).toBe(403);
    const found = await findVariationById(fixture.schema, fixture.draftB.id);
    expect(found).not.toBeNull();
  });

  it('returns 404 for a numeric id instead of deleting by pk', async () => {
    const response = await remove(String(fixture.draftA.id));

    expect(response.status).toBe(404);
    const found = await findVariationById(fixture.schema, fixture.draftA.id);
    expect(found).not.toBeNull();
  });
});
