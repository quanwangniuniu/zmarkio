import { DELETE as deleteVariation, PATCH as patchVariation } from '@/app/api/ad_copy_variation/variations/[id]/route';
import { POST as createVariation } from '@/app/api/ad_copy_variation/variations/route';
import { prisma } from '@/lib/prisma';
import { tenantTable } from '@/lib/tenant';
import { findVariationById, VARIATION_TABLE } from '@/lib/variationStore';

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

async function storedFields(id: bigint) {
  const rows = await prisma.$queryRaw<{ fields: unknown }[]>`
    SELECT fields FROM ${tenantTable(fixture.schema, VARIATION_TABLE)} WHERE id = ${id}`;
  return rows[0].fields;
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
      platform: 'meta',
      hook: 'Created hook',
    });
    expect(typeof body.slug).toBe('string');

    const row = await findVariationById(fixture.schema, BigInt(body.id as number));
    expect(row?.createdById).toBe(BigInt(fixture.memberUserId));
    expect(row?.modelName).toBe('qwen3:4b');
    expect(await storedFields(BigInt(body.id as number))).toEqual({
      hook: 'Created hook', headline: 'Created headline',
      description: 'Created description', cta: 'LEARN_MORE',
    });
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

describe('PATCH /variations/{slug}/ copy storage', () => {
  function patch(slug: string, body: Record<string, unknown>) {
    return patchVariation(
      studioRequest(`/api/ad_copy_variation/variations/${slug}/`, { token, method: 'PATCH', body }),
      { params: { id: slug } }
    );
  }

  function newVariation() {
    return createTestVariation({
      schema: fixture.schema, projectId: fixture.projectA,
      userId: fixture.memberUserId, status: 'draft',
    });
  }

  it.each([null, { hook: 'Stale hook' }])('rebuilds legacy Meta fields (%p) after a partial edit', async fields => {
    const row = await newVariation();
    // A legacy writer may change an unpatched column without keeping JSON in sync.
    await prisma.$executeRaw`
      UPDATE ${tenantTable(fixture.schema, VARIATION_TABLE)}
      SET headline = 'Legacy headline', fields = ${fields === null ? null : JSON.stringify(fields)}::jsonb
      WHERE id = ${row.id}`;

    const response = await patch(row.slug, { hook: 'Edited hook', description: '' });

    expect(response.status).toBe(200);
    const copy = { hook: 'Edited hook', headline: 'Legacy headline', description: '', cta: 'LEARN_MORE' };
    await expect(readJson(response)).resolves.toMatchObject(copy);
    expect(await storedFields(row.id)).toEqual(copy);
  });

  it('leaves fields untouched when only status changes', async () => {
    const row = await newVariation();
    const fields = { hook: 'Earlier stored copy' };
    await prisma.$executeRaw`
      UPDATE ${tenantTable(fixture.schema, VARIATION_TABLE)}
      SET fields = ${JSON.stringify(fields)}::jsonb WHERE id = ${row.id}`;

    const response = await patch(row.slug, { status: 'reviewed' });

    expect(response.status).toBe(200);
    await expect(readJson(response)).resolves.toMatchObject({ status: 'reviewed' });
    expect(await storedFields(row.id)).toEqual(fields);
  });

  it('does not rebuild non-Meta fields from the legacy text columns', async () => {
    const row = await newVariation();
    const fields = { headlines: ['First headline', 'Second headline'], descriptions: ['First description', 'Second description'] };
    await prisma.$executeRaw`
      UPDATE ${tenantTable(fixture.schema, VARIATION_TABLE)}
      SET platform = 'google_rsa', fields = ${JSON.stringify(fields)}::jsonb WHERE id = ${row.id}`;

    const response = await patch(row.slug, { hook: 'Edited legacy hook' });

    expect(response.status).toBe(200);
    await expect(readJson(response)).resolves.toMatchObject({ platform: 'google_rsa' });
    expect(await storedFields(row.id)).toEqual(fields);
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
