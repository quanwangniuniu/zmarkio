import { POST as generate } from '@/app/api/ad_copy_variation/variations/generate/route';
import { prisma } from '@/lib/prisma';
import { tenantTable } from '@/lib/tenant';
import { VARIATION_TABLE, findVariationById } from '@/lib/variationStore';

import {
  setupStudioFixture,
  teardownStudioFixture,
  type StudioFixture,
} from './support/fixtures';
import { readJson, studioRequest } from './support/requests';
import { accessToken } from './support/tokens';

jest.mock('@/src/ai/providers/gemini', () => ({
  callGeminiJson: jest.fn(async () => ({
    hook: 'Tenant hook',
    headline: 'Tenant headline',
    description: 'Tenant description',
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

describe('tenant schema isolation', () => {
  it('persists generate results in the org schema, not public', async () => {
    const response = await generate(
      studioRequest('/api/ad_copy_variation/variations/generate/', {
        token,
        body: {
          project_id: String(fixture.projectA),
          source_mode: 'custom',
          count: 1,
          base_copy: {
            hook: 'Base hook',
            headline: 'Base headline',
            description: 'Base description',
            cta: 'LEARN_MORE',
          },
        },
      })
    );

    expect(response.status).toBe(200);
    const body = await readJson(response);
    const id = BigInt((body.results as { id: number }[])[0].id);

    await expect(findVariationById(fixture.schema, id)).resolves.toMatchObject({
      id,
      sourceMode: 'custom',
    });

    const publicRows = await prisma.$queryRaw<{ id: bigint }[]>`
      SELECT id FROM ${tenantTable('public', VARIATION_TABLE)}
      WHERE id = ${id}`;
    expect(publicRows).toEqual([]);
  });
});
