/**
 * Cross-system JWT parity: tokens minted by Django (`issue_studio_jwt_fixtures`)
 * must be accepted/rejected by Node the same way Django already validated in the
 * management command.
 *
 * CI writes fixtures to DJANGO_JWT_FIXTURES_PATH before `npm test`.
 * Locally, skip when the file/env is absent (no Django required for other suites).
 */

import { readFileSync, existsSync } from 'fs';

import { GET as me } from '@/app/api/me/route';
import { GET as latestBatch } from '@/app/api/ad_copy_variation/variations/latest_batch/route';
import { prisma } from '@/lib/prisma';
import { foreignlySignedToken } from './support/tokens';
import { studioRequest } from './support/requests';

type DjangoJwtFixtures = {
  user_id: number;
  org_slug: string;
  auth_token_version: number;
  access_token: string;
  near_expiry_access_token: string;
  expired_access_token: string;
  refresh_token: string;
  pre_rotation_access_token: string;
  django_accepts_access: boolean;
  django_rejects_expired: boolean;
  django_rejects_refresh_as_access: boolean;
  django_rejects_pre_rotation: boolean;
  django_accepts_near_expiry: boolean;
};

function loadFixtures(): DjangoJwtFixtures | null {
  const inline = process.env.DJANGO_JWT_FIXTURES_JSON;
  if (inline) {
    return JSON.parse(inline) as DjangoJwtFixtures;
  }
  const path = process.env.DJANGO_JWT_FIXTURES_PATH;
  if (path && existsSync(path)) {
    return JSON.parse(readFileSync(path, 'utf8')) as DjangoJwtFixtures;
  }
  return null;
}

const fixtures = loadFixtures();
const describeParity = fixtures ? describe : describe.skip;

describeParity('django-issued JWT → Node parity', () => {
  afterAll(async () => {
    await prisma.$disconnect();
  });

  it('has django-side validation flags set by the mint command', () => {
    expect(fixtures!.django_accepts_access).toBe(true);
    expect(fixtures!.django_rejects_expired).toBe(true);
    expect(fixtures!.django_rejects_refresh_as_access).toBe(true);
    expect(fixtures!.django_rejects_pre_rotation).toBe(true);
    expect(fixtures!.django_accepts_near_expiry).toBe(true);
  });

  it('accepts a Django-minted access token on /api/me', async () => {
    const response = await me(
      studioRequest('/api/me', {
        authorization: `Bearer ${fixtures!.access_token}`,
      })
    );

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({
      user_id: fixtures!.user_id,
    });
  });

  it('accepts a Django-minted near-expiry access token on /api/me', async () => {
    // Fixture lifetime is 30 minutes from mint. If this fails with 401 after a
    // long gap, re-run issue_studio_jwt_fixtures and retry.
    const response = await me(
      studioRequest('/api/me', {
        authorization: `Bearer ${fixtures!.near_expiry_access_token}`,
      })
    );

    if (response.status !== 200) {
      const body = await response.json().catch(() => ({}));
      throw new Error(
        `near-expiry token rejected (status=${response.status}, body=${JSON.stringify(body)}). ` +
          'Re-mint fixtures if more than ~30 minutes passed since issue_studio_jwt_fixtures.'
      );
    }
    await expect(response.json()).resolves.toEqual({
      user_id: fixtures!.user_id,
    });
  });

  it('rejects a Django-minted expired access token (Node + Django agree)', async () => {
    const response = await me(
      studioRequest('/api/me', {
        authorization: `Bearer ${fixtures!.expired_access_token}`,
      })
    );

    expect(fixtures!.django_rejects_expired).toBe(true);
    expect(response.status).toBe(401);
  });

  it('rejects a Django refresh token used as access (Node + Django agree)', async () => {
    const response = await me(
      studioRequest('/api/me', {
        authorization: `Bearer ${fixtures!.refresh_token}`,
      })
    );

    expect(fixtures!.django_rejects_refresh_as_access).toBe(true);
    expect(response.status).toBe(401);
    await expect(response.json()).resolves.toEqual({
      detail: 'Given token not valid for any token type',
    });
  });

  it('rejects a pre-rotation Django token after auth_token_version bump', async () => {
    const response = await me(
      studioRequest('/api/me', {
        authorization: `Bearer ${fixtures!.pre_rotation_access_token}`,
      })
    );

    expect(fixtures!.django_rejects_pre_rotation).toBe(true);
    expect(response.status).toBe(401);
    await expect(response.json()).resolves.toEqual({
      detail: 'Token has been revoked.',
    });
  });

  it('rejects a token signed with a non-Django SECRET_KEY (rotated/wrong signing key)', async () => {
    const token = await foreignlySignedToken(fixtures!.user_id);
    const response = await me(
      studioRequest('/api/me', {
        authorization: `Bearer ${token}`,
      })
    );

    expect(response.status).toBe(401);
  });

  it('passes Django access auth when hitting a variations route guard', async () => {
    // latest_batch still requires project_id membership; without a project this
    // returns 4xx after auth — but must not be 401 if the Django token is valid.
    const response = await latestBatch(
      studioRequest(
        '/api/ad_copy_variation/variations/latest_batch/?project_id=1',
        { authorization: `Bearer ${fixtures!.access_token}` }
      )
    );

    expect(response.status).not.toBe(401);
  });
});
