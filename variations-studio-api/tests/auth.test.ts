import { POST as bulkDelete } from '@/app/api/ad_copy_variation/variations/bulk_delete/route';
import { GET as latestBatch } from '@/app/api/ad_copy_variation/variations/latest_batch/route';
import { prisma } from '@/lib/prisma';

import {
  setupStudioFixture,
  teardownStudioFixture,
  type StudioFixture,
} from './support/fixtures';
import { studioRequest } from './support/requests';
import {
  accessToken,
  accessTokenWithoutType,
  expiredAccessToken,
  foreignlySignedToken,
  refreshToken,
} from './support/tokens';

let fixture: StudioFixture;

beforeAll(async () => {
  fixture = await setupStudioFixture();
});

afterAll(async () => {
  await teardownStudioFixture(fixture);
  await prisma.$disconnect();
});

function latestBatchRequest(authorization?: string) {
  return studioRequest(
    `/api/ad_copy_variation/variations/latest_batch/?project_id=${fixture.projectA}`,
    authorization === undefined ? {} : { authorization }
  );
}

describe('access token rejection', () => {
  it('rejects a request with no Authorization header', async () => {
    const response = await latestBatch(latestBatchRequest());

    expect(response.status).toBe(401);
    await expect(response.json()).resolves.toEqual({
      detail: 'Authentication credentials were not provided.',
    });
  });

  it('rejects a non-Bearer scheme', async () => {
    const token = await accessToken(fixture.memberUserId);
    const response = await latestBatch(latestBatchRequest(`Token ${token}`));

    expect(response.status).toBe(401);
  });

  it('rejects a token signed with a different secret', async () => {
    const token = await foreignlySignedToken(fixture.memberUserId);
    const response = await latestBatch(latestBatchRequest(`Bearer ${token}`));

    expect(response.status).toBe(401);
  });

  it('rejects an expired token', async () => {
    const token = await expiredAccessToken(fixture.memberUserId);
    const response = await latestBatch(latestBatchRequest(`Bearer ${token}`));

    expect(response.status).toBe(401);
  });

  it('rejects a refresh token used as an access token', async () => {
    const token = await refreshToken(fixture.memberUserId);
    const response = await latestBatch(latestBatchRequest(`Bearer ${token}`));

    expect(response.status).toBe(401);
    await expect(response.json()).resolves.toEqual({
      detail: 'Given token not valid for any token type',
    });
  });

  it('rejects a signed token that omits token_type', async () => {
    const token = await accessTokenWithoutType(fixture.memberUserId);
    const response = await latestBatch(latestBatchRequest(`Bearer ${token}`));

    expect(response.status).toBe(401);
    await expect(response.json()).resolves.toEqual({
      detail: 'Given token not valid for any token type',
    });
  });
});

describe('user state is re-checked on every request', () => {
  // Django's JWTAuthentication loads the user and checks is_active. A signature
  // check alone would let a disabled account keep working until its token expires.
  it('rejects a valid token belonging to a deactivated user', async () => {
    const token = await accessToken(fixture.inactiveUserId);
    const response = await latestBatch(latestBatchRequest(`Bearer ${token}`));

    expect(response.status).toBe(401);
  });

  it('rejects a valid token belonging to a deleted user', async () => {
    const ghostUserId = 2_000_000_000;
    const token = await accessToken(ghostUserId);
    const response = await latestBatch(latestBatchRequest(`Bearer ${token}`));

    expect(response.status).toBe(401);
  });

  // Mirrors backend/core/authentication.py auth_token_version check.
  it('rejects a token whose auth_token_version no longer matches the user', async () => {
    const token = await accessToken(fixture.memberUserId, 0);
    await prisma.$executeRaw`
      UPDATE public.core_customuser
      SET auth_token_version = auth_token_version + 1
      WHERE id = ${BigInt(fixture.memberUserId)}`;

    try {
      const response = await latestBatch(latestBatchRequest(`Bearer ${token}`));
      expect(response.status).toBe(401);
      await expect(response.json()).resolves.toEqual({
        detail: 'Token has been revoked.',
      });
    } finally {
      await prisma.$executeRaw`
        UPDATE public.core_customuser
        SET auth_token_version = 0
        WHERE id = ${BigInt(fixture.memberUserId)}`;
    }
  });

  it('accepts a token whose auth_token_version matches after rotation', async () => {
    await prisma.$executeRaw`
      UPDATE public.core_customuser
      SET auth_token_version = 3
      WHERE id = ${BigInt(fixture.memberUserId)}`;

    try {
      const token = await accessToken(fixture.memberUserId, 3);
      const response = await latestBatch(latestBatchRequest(`Bearer ${token}`));
      expect(response.status).not.toBe(401);
    } finally {
      await prisma.$executeRaw`
        UPDATE public.core_customuser
        SET auth_token_version = 0
        WHERE id = ${BigInt(fixture.memberUserId)}`;
    }
  });
});

describe('write endpoints are guarded too', () => {
  it('rejects an unauthenticated bulk_delete', async () => {
    const response = await bulkDelete(
      studioRequest('/api/ad_copy_variation/variations/bulk_delete/', {
        body: {
          project_id: String(fixture.projectA),
          selected_ids: [Number(fixture.draftA.id)],
          status: 'draft',
        },
      })
    );

    expect(response.status).toBe(401);
  });
});
