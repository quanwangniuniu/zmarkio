import { randomUUID } from 'node:crypto';

import { DELETE as deleteVariation, GET as getVariation, PATCH as patchVariation } from '@/app/api/ad_copy_variation/variations/[id]/route';
import { GET as listVariations, POST as createVariation } from '@/app/api/ad_copy_variation/variations/route';
import { prisma } from '@/lib/prisma';
import { tenantTable } from '@/lib/tenant';
import { findBatchVariations, findVariationById, VARIATION_TABLE } from '@/lib/variationStore';

import {
  createTestVariation,
  setupStudioFixture,
  teardownStudioFixture,
  type StudioFixture,
} from './support/fixtures';
import { readJson, studioRequest } from './support/requests';
import { accessToken } from './support/tokens';

jest.mock('@/src/platforms', () => {
  const platforms = jest.requireActual<typeof import('@/src/platforms')>('@/src/platforms');
  const registered = platforms.listPlatformIds().map(platforms.getPlatformSpec);
  return {
    ...platforms,
    ...platforms.createPlatformRegistry([...registered, {
      ...platforms.getPlatformSpec('meta'),
      id: 'test_text',
      fields: [
        { key: 'ad_text', label: 'Ad text', type: 'string', required: true, limits: { maxChars: 20 } },
        { key: 'cta', label: 'CTA', type: 'string', required: true, limits: {} },
      ],
      cta: { kind: 'enum', field: 'cta', values: ['JOIN'], normalization: 'exact', fallback: 'JOIN' },
      slugSource: { field: 'ad_text' },
    }]),
  };
});

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
    const fields = {
      hook: 'Created hook', headline: 'Created headline',
      description: 'Created description', cta: 'LEARN_MORE',
    };
    expect(body).toMatchObject({
      project: Number(fixture.projectA),
      created_by: fixture.memberUserId,
      status: 'draft',
      platform: 'meta',
      ...fields,
      fields,
    });
    expect(typeof body.slug).toBe('string');

    const row = await findVariationById(fixture.schema, BigInt(body.id as number));
    expect(row?.createdById).toBe(BigInt(fixture.memberUserId));
    expect(row?.modelName).toBe('qwen3:4b');
    expect(await storedFields(BigInt(body.id as number))).toEqual(fields);
  });

  it('rejects a missing project', async () => {
    const response = await create(payload({ project: undefined }));

    expect(response.status).toBe(400);
  });

  it('preserves empty copy for a null top-level field in legacy Meta drafts', async () => {
    const response = await create(payload({ headline: null }));
    expect(response.status).toBe(201);
    await expect(readJson(response)).resolves.toMatchObject({
      platform: 'meta', headline: '', fields: { headline: '' },
    });
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

    expect(response.status).toBe(404);
    await expect(readJson(response)).resolves.toEqual({ detail: 'Not found.' });
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

describe('manual variation copy storage', () => {
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

  function detail(slug: string) {
    return getVariation(
      studioRequest(`/api/ad_copy_variation/variations/${slug}/`, { token }), { params: { id: slug } }
    );
  }

  it.each([null, ''])('defaults platform %j to Meta and maps nested copy to the legacy columns', async platform => {
    const fields = { hook: '', headline: 'H'.repeat(41), description: '', cta: 'SHOP_NOW' };
    const created = await create({
      project: Number(fixture.projectA), source_mode: 'custom', platform,
      fields: { headline: fields.headline, cta: fields.cta },
    });
    expect(created.status).toBe(201);
    const body = await readJson(created);
    expect(body).toMatchObject({ platform: 'meta', ...fields, fields });

    // Manual creation accepts a long headline. A PATCH validates only its own fields.
    const updated = await patch(body.slug as string, { fields: { cta: 'LEARN_MORE' } });
    expect(updated.status).toBe(200);
    const edited = { ...fields, cta: 'LEARN_MORE' };
    await expect(readJson(updated)).resolves.toMatchObject({ ...edited, fields: edited });
    expect(await storedFields(BigInt(body.id as number))).toEqual(edited);
    expect((await patch(body.slug as string, { fields: { headline: fields.headline } })).status).toBe(400);
  });

  it.each([
    { name: 'mixed legacy and nested copies', input: { cta: 'SIGN_UP', fields: { cta: 'SIGN_UP' } } },
    { name: 'a null fields object', input: { fields: null } },
    { name: 'an array fields object', input: { fields: [] } },
    { name: 'an unknown field', input: { fields: { headline: 'Edited', ad_text: 'Wrong platform' } } },
    { name: 'a non-string value', input: { fields: { headline: 'Edited', description: 3 } } },
  ])('rejects $name before creating or modifying a row', async ({ input }) => {
    const batchId = randomUUID();
    const creation = await create({
      project: Number(fixture.projectA), source_mode: 'custom', batch_id: batchId, ...input,
    });
    expect(creation.status).toBe(400);
    expect(await findBatchVariations(fixture.schema, fixture.projectA, batchId)).toEqual([]);

    const row = await newVariation();
    const before = await findVariationById(fixture.schema, row.id);
    expect((await patch(row.slug, { status: 'reviewed', ...input })).status).toBe(400);
    expect(await findVariationById(fixture.schema, row.id)).toEqual(before);
  });

  it('rejects an unregistered target at creation', async () => {
    expect((await create(payload({ platform: 'unregistered' }))).status).toBe(400);
  });

  it('saves and retrieves registered non-Meta fields by slug and platform', async () => {
    const common = { project: Number(fixture.projectA), source_mode: 'custom', platform: 'test_text' };
    const legacy = { hook: '', headline: '', description: '', cta: '' };
    expect((await create({ ...common, cta: 'JOIN' })).status).toBe(400);
    const created = await create({ ...common, fields: { cta: 'JOIN' } });
    expect(created.status).toBe(201);
    const body = await readJson(created);
    expect(body.slug).toMatch(/\S/);
    expect(body).toMatchObject({ ...legacy, fields: { ad_text: '', cta: 'JOIN' } });

    const slug = body.slug as string;
    expect((await patch(slug, { cta: 'JOIN' })).status).toBe(400);
    expect((await patch(slug, { fields: { cta: 'SHOP_NOW' } })).status).toBe(400);
    const updated = await patch(slug, { fields: { ad_text: 'Edited ad text' } });
    expect(updated.status).toBe(200);
    const fields = { ad_text: 'Edited ad text', cta: 'JOIN' };
    const expected = { id: body.id, slug, platform: 'test_text', ...legacy, fields };
    await expect(readJson(updated)).resolves.toMatchObject(expected);
    expect((await patch(slug, { status: 'reviewed' })).status).toBe(200);
    expect(await storedFields(BigInt(body.id as number))).toEqual(fields);

    const found = await detail(slug);
    expect(found.status).toBe(200);
    await expect(readJson(found)).resolves.toMatchObject({ ...expected, status: 'reviewed' });
    const listed = await listVariations(studioRequest(
      `/api/ad_copy_variation/variations/?project_id=${fixture.projectA}&platform=test_text`, { token }
    ));
    expect(listed.status).toBe(200);
    expect((await readJson(listed)).results).toContainEqual(expect.objectContaining(expected));
  });

  it.each([
    { headline: '!!!', expectedSlug: /\S/ },
    { headline: 'Generate', expectedSlug: /^adcopyvariation-generate(?:-[1-9]\d*)?$/ },
  ])('updates a draft titled $headline by its returned slug', async ({ headline, expectedSlug }) => {
    const created = await create(payload({ headline }));
    expect(created.status).toBe(201);
    const { id, slug } = await readJson(created);
    expect(slug).toMatch(expectedSlug);

    const updated = await patch(slug as string, { headline: 'Edited headline' });
    expect(updated.status).toBe(200);
    await expect(readJson(updated)).resolves.toMatchObject({ id, slug, headline: 'Edited headline' });
  });

  it.each([null, { hook: 'Stale hook' }])('rebuilds legacy Meta fields (%p) after a partial edit', async fields => {
    const row = await newVariation();
    // A legacy writer may change an unpatched column without keeping JSON in sync.
    await prisma.$executeRaw`
      UPDATE ${tenantTable(fixture.schema, VARIATION_TABLE)}
      SET headline = 'Legacy headline', fields = ${fields === null ? null : JSON.stringify(fields)}::jsonb
      WHERE id = ${row.id}`;

    const before = await detail(row.slug);
    expect(before.status).toBe(200);
    expect((await readJson(before)).fields).toEqual({
      hook: 'Fixture hook', headline: 'Legacy headline', description: 'Fixture description', cta: 'LEARN_MORE',
    });
    const response = await patch(row.slug, { hook: 'Edited hook', description: '' });

    expect(response.status).toBe(200);
    const copy = { hook: 'Edited hook', headline: 'Legacy headline', description: '', cta: 'LEARN_MORE' };
    await expect(readJson(response)).resolves.toMatchObject({ ...copy, fields: copy });
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
    await expect(readJson(response)).resolves.toMatchObject({ platform: 'google_rsa', fields });
    expect(await storedFields(row.id)).toEqual(fields);
  });

  it.each([
    { name: 'a status-only edit', edit: { status: 'reviewed' } },
    { name: 'an edit to another copy field', edit: { description: 'Updated description' } },
    { name: 'a corrected headline and exact CTA', edit: { headline: 'New headline', cta: 'SIGN_UP' } },
  ])('accepts $name on a draft with a legacy over-limit headline', async ({ edit }) => {
    const row = await newVariation();
    const headline = 'H'.repeat(41);
    await prisma.$executeRaw`
      UPDATE ${tenantTable(fixture.schema, VARIATION_TABLE)}
      SET headline = ${headline}, fields = ${JSON.stringify({ headline })}::jsonb WHERE id = ${row.id}`;

    const response = await patch(row.slug, edit);

    expect(response.status).toBe(200);
    const { status = 'draft', ...copyEdit } = edit;
    const expectedCopy = { headline, ...copyEdit };
    expect(await findVariationById(fixture.schema, row.id)).toMatchObject({ status, ...expectedCopy });
    expect(await storedFields(row.id)).toMatchObject(expectedCopy);
  });

  it('accepts edits beyond the Meta hook limit on an unregistered platform', async () => {
    const row = await newVariation();
    await prisma.$executeRaw`
      UPDATE ${tenantTable(fixture.schema, VARIATION_TABLE)}
      SET platform = 'legacy_unregistered' WHERE id = ${row.id}`;

    const hook = 'h'.repeat(51); // One character over the Meta hook limit.
    const response = await patch(row.slug, { hook });

    expect(response.status).toBe(200);
    expect(await findVariationById(fixture.schema, row.id)).toMatchObject({ platform: 'legacy_unregistered', hook });
  });

  it.each([
    {
      name: 'an over-limit headline',
      body: { headline: 'H'.repeat(41) },
      details: [/headline/i, /40/, /character/i],
    },
    {
      name: 'a noncanonical CTA',
      body: { cta: 'learn more' },
      details: [/cta/i, /\bLEARN_MORE\b/],
    },
    {
      name: 'an over-limit hook and invalid CTA alongside valid headline and status edits',
      body: {
        hook: Array(11).fill('a').join(' '),
        cta: 'unknown action',
        headline: 'New headline',
        status: 'reviewed',
      },
      details: [/hook/i, /10/, /word/i, /cta/i, /\bSHOP_NOW\b/],
    },
  ])('rejects $name with a field error and preserves the entire row', async ({ body, details }) => {
    const row = await newVariation();
    const before = await findVariationById(fixture.schema, row.id);
    const fields = await storedFields(row.id);

    const response = await patch(row.slug, body);

    expect(response.status).toBe(400);
    const { error } = await readJson(response);
    for (const detail of details) expect(error).toMatch(detail);
    expect(await findVariationById(fixture.schema, row.id)).toEqual(before);
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
