/**
 * P0 tenant routing matrix (Jest → route handlers / requireStudioContext).
 *
 * 1. Forged X-Organization-Slug cannot override current_organization
 * 2. Dual-org: A cannot read/write B's tenant data
 * 3. Multi-org user: current_organization wins (deterministic)
 * 4. Header fallback only when both org FKs are null
 * 5. Unknown / inactive org header does not open a foreign schema
 */

import { GET as getVariation } from '@/app/api/ad_copy_variation/variations/[id]/route';
import { PATCH as patchVariation } from '@/app/api/ad_copy_variation/variations/[id]/route';
import { POST as createVariation } from '@/app/api/ad_copy_variation/variations/route';
import { POST as bulkDelete } from '@/app/api/ad_copy_variation/variations/bulk_delete/route';
import { isAuthFailure } from '@/lib/auth';
import { prisma } from '@/lib/prisma';
import { requireStudioContext } from '@/lib/tenant';
import { findVariationById } from '@/lib/variationStore';

import { readJson, studioRequest } from './support/requests';
import { accessToken } from './support/tokens';
import {
  countInSchema,
  setMultiOrgCurrent,
  setupTenantRoutingFixture,
  teardownTenantRoutingFixture,
  type TenantRoutingFixture,
} from './support/tenantRoutingFixtures';

let fixture: TenantRoutingFixture;
let tokenPinnedA: string;
let tokenMulti: string;
let tokenHeaderOnly: string;

beforeAll(async () => {
  fixture = await setupTenantRoutingFixture();
  tokenPinnedA = await accessToken(fixture.pinnedToAUserId);
  tokenMulti = await accessToken(fixture.multiOrgUserId);
  tokenHeaderOnly = await accessToken(fixture.headerOnlyUserId);
});

afterAll(async () => {
  await teardownTenantRoutingFixture(fixture);
  await prisma.$disconnect();
});

async function resolvedSchema(
  token: string,
  headers?: Record<string, string>
): Promise<string> {
  const auth = await requireStudioContext(
    studioRequest('/api/me', { token, headers })
  );
  if (isAuthFailure(auth)) {
    throw new Error(`auth failed: ${auth.error}`);
  }
  return auth.schema;
}

describe('1) forged X-Organization-Slug cannot override current_organization', () => {
  it('keeps schema A when pinned user sends org-B header', async () => {
    const schema = await resolvedSchema(tokenPinnedA, {
      'X-Organization-Slug': fixture.orgB.slug,
    });
    expect(schema).toBe(fixture.orgA.schema);
  });
});

describe('2) dual-org isolation: A cannot read/write B', () => {
  it('cannot GET a variation that only exists in org B', async () => {
    const response = await getVariation(
      studioRequest(`/api/ad_copy_variation/variations/${fixture.draftBSlug}/`, {
        token: tokenPinnedA,
      }),
      { params: { id: fixture.draftBSlug } }
    );
    // Resolved into schema A → slug lookup misses → Not found (not B's row).
    expect(response.status).toBe(404);
  });

  it('cannot PATCH a variation in org B by slug', async () => {
    const before = await countInSchema(fixture.orgB.schema, fixture.projectB);
    const response = await patchVariation(
      studioRequest(`/api/ad_copy_variation/variations/${fixture.draftBSlug}/`, {
        token: tokenPinnedA,
        method: 'PATCH',
        body: { headline: 'hijack' },
      }),
      { params: { id: fixture.draftBSlug } }
    );
    expect(response.status).toBe(404);
    const row = await findVariationById(fixture.orgB.schema, fixture.draftBId);
    expect(row?.headline).toBe('B headline');
    await expect(
      countInSchema(fixture.orgB.schema, fixture.projectB)
    ).resolves.toBe(before);
  });

  it('cannot create into org B by posting B project_id while pinned to A', async () => {
    const before = await countInSchema(fixture.orgB.schema, fixture.projectB);
    const response = await createVariation(
      studioRequest('/api/ad_copy_variation/variations/', {
        token: tokenPinnedA,
        body: {
          project: String(fixture.projectB),
          source_mode: 'custom',
          hook: 'x',
          headline: 'x',
          description: 'x',
          cta: 'LEARN_MORE',
        },
      })
    );
    // projectB is not visible in schema A → 404/400, never inserts into B.
    expect([400, 403, 404]).toContain(response.status);
    await expect(
      countInSchema(fixture.orgB.schema, fixture.projectB)
    ).resolves.toBe(before);
  });

  it('cannot bulk_delete org B ids while pinned to A', async () => {
    const before = await countInSchema(fixture.orgB.schema, fixture.projectB);
    const response = await bulkDelete(
      studioRequest('/api/ad_copy_variation/variations/bulk_delete/', {
        token: tokenPinnedA,
        body: {
          project_id: String(fixture.projectB),
          selected_ids: [Number(fixture.draftBId)],
          status: 'draft',
        },
      })
    );
    expect([400, 403, 404]).toContain(response.status);
    await expect(
      findVariationById(fixture.orgB.schema, fixture.draftBId)
    ).resolves.not.toBeNull();
    await expect(
      countInSchema(fixture.orgB.schema, fixture.projectB)
    ).resolves.toBe(before);
  });

  it('can still read own org A variation', async () => {
    const response = await getVariation(
      studioRequest(`/api/ad_copy_variation/variations/${fixture.draftASlug}/`, {
        token: tokenPinnedA,
      }),
      { params: { id: fixture.draftASlug } }
    );
    expect(response.status).toBe(200);
    const body = await readJson(response);
    expect(body.slug).toBe(fixture.draftASlug);
  });
});

describe('3) multi-org: current_organization wins deterministically', () => {
  afterEach(async () => {
    await setMultiOrgCurrent(fixture, 'A');
    tokenMulti = await accessToken(fixture.multiOrgUserId);
  });

  it('resolves to A when current_organization is A (organization_id may be B)', async () => {
    await setMultiOrgCurrent(fixture, 'A');
    tokenMulti = await accessToken(fixture.multiOrgUserId);
    await expect(resolvedSchema(tokenMulti)).resolves.toBe(fixture.orgA.schema);
  });

  it('resolves to B after switching current_organization to B', async () => {
    await setMultiOrgCurrent(fixture, 'B');
    tokenMulti = await accessToken(fixture.multiOrgUserId);
    await expect(resolvedSchema(tokenMulti)).resolves.toBe(fixture.orgB.schema);
  });

  it('ignores forged header while current_organization is set', async () => {
    await setMultiOrgCurrent(fixture, 'A');
    tokenMulti = await accessToken(fixture.multiOrgUserId);
    const schema = await resolvedSchema(tokenMulti, {
      'X-Organization-Slug': fixture.orgB.slug,
    });
    expect(schema).toBe(fixture.orgA.schema);
  });
});

describe('4) header fallback only when both org FKs are null', () => {
  it('uses X-Organization-Slug when user has no organization FKs', async () => {
    const schema = await resolvedSchema(tokenHeaderOnly, {
      'X-Organization-Slug': fixture.orgA.slug,
    });
    expect(schema).toBe(fixture.orgA.schema);
  });

  it('falls back to public when header-only user omits the header', async () => {
    await expect(resolvedSchema(tokenHeaderOnly)).resolves.toBe('public');
  });

  it('does not use header when current_organization is already set', async () => {
    const schema = await resolvedSchema(tokenPinnedA, {
      'X-Organization-Slug': fixture.orgA.slug,
    });
    expect(schema).toBe(fixture.orgA.schema);
  });
});

describe('5) unknown / inactive org header behavior', () => {
  it('does not enter a foreign schema for an unknown slug (falls to public)', async () => {
    const schema = await resolvedSchema(tokenHeaderOnly, {
      'X-Organization-Slug': 'no-such-org-studio-routing',
    });
    expect(schema).toBe('public');
    expect(schema).not.toBe(fixture.orgA.schema);
    expect(schema).not.toBe(fixture.orgB.schema);
  });

  it('does not enter schema for an inactive organization slug', async () => {
    const schema = await resolvedSchema(tokenHeaderOnly, {
      'X-Organization-Slug': fixture.inactiveOrgSlug,
    });
    expect(schema).toBe('public');
  });

  it('pinned user still stays on A if they send unknown header', async () => {
    const schema = await resolvedSchema(tokenPinnedA, {
      'X-Organization-Slug': 'no-such-org-studio-routing',
    });
    expect(schema).toBe(fixture.orgA.schema);
  });
});
