import { jwtVerify } from 'jose';

import { prisma } from '@/lib/prisma';

export type AccessUser = {
  userId: number;
};

export type AuthFailure = {
  error: string;
  status: 401;
};

/**
 * Studio JWT access checks (parity with Django simplejwt + TenantAwareJWTAuthentication).
 *
 * Aligned with backend/backend/settings.py SIMPLE_JWT and core/authentication.py:
 * - Algorithm: HS256 only
 * - Signing key: Django SECRET_KEY (same env as backend)
 * - Header: Authorization Bearer <access>
 * - Required claim token_type === "access" (missing / refresh / other → 401)
 * - user_id must resolve to an active CustomUser in public
 * - auth_token_version claim must match CustomUser.auth_token_version (revocation)
 * - Clock skew / leeway: none (jose default 0), matching unset SIMPLE_JWT leeway —
 *   already-expired tokens are rejected; see djangoIssuedAuth.test.ts for
 *   Django-minted expired + near-expiry cases.
 *
 * Cross-system proof: CI runs `manage.py issue_studio_jwt_fixtures` (Django
 * production mint path) and Jest consumes those tokens via DJANGO_JWT_FIXTURES_PATH.
 *
 * Intentionally NOT re-implemented (not configured / not needed for Studio access):
 * - refresh-token blacklist / ROTATE_REFRESH_TOKENS flows (Studio never accepts refresh)
 * - custom AUDIENCE / ISSUER (not set in SIMPLE_JWT)
 * - full AUTH_TOKEN_CLASSES plug-in surface from simplejwt
 */
function getSigningKey() {
  const secret = process.env.SECRET_KEY;
  if (!secret) {
    throw new Error('SECRET_KEY is not configured');
  }
  return new TextEncoder().encode(secret);
}

function bearerToken(request: Request): string | null {
  const header = request.headers.get('authorization');
  if (!header) return null;
  const [scheme, token] = header.split(' ');
  if (!scheme || !token || scheme.toLowerCase() !== 'bearer') return null;
  return token.trim() || null;
}

function claimVersion(raw: unknown): number {
  if (raw === undefined || raw === null) return 0;
  if (typeof raw === 'number' && Number.isInteger(raw)) return raw;
  if (typeof raw === 'string' && /^-?\d+$/.test(raw.trim())) {
    return Number(raw.trim());
  }
  return Number.NaN;
}

export async function requireAccessUser(
  request: Request
): Promise<AccessUser | AuthFailure> {
  const token = bearerToken(request);
  if (!token) {
    return { error: 'Authentication credentials were not provided.', status: 401 };
  }

  try {
    const { payload } = await jwtVerify(token, getSigningKey(), {
      algorithms: ['HS256'],
    });

    // simplejwt AccessToken always carries token_type="access". Require it
    // explicitly so a forged payload that omits the claim cannot slip through.
    if (payload.token_type !== 'access') {
      return { error: 'Given token not valid for any token type', status: 401 };
    }

    const rawId = payload.user_id;
    const userId = typeof rawId === 'number' ? rawId : Number(rawId);
    if (!Number.isInteger(userId) || userId <= 0) {
      return { error: 'Invalid token payload', status: 401 };
    }

    const tokenVersion = claimVersion(payload.auth_token_version);
    if (!Number.isInteger(tokenVersion) || tokenVersion < 0) {
      return { error: 'Invalid token payload', status: 401 };
    }

    const user = await prisma.customUser.findFirst({
      where: { id: BigInt(userId) },
      select: { isActive: true, authTokenVersion: true },
    });
    // Match prior Studio behavior for missing/inactive users (not Django's
    // "User is inactive" string) so existing clients keep the same 401 body.
    if (!user?.isActive) {
      return { error: 'Invalid or expired token.', status: 401 };
    }

    // Same rule as backend/core/authentication.py — password rotate / logout
    // bumps CustomUser.auth_token_version and invalidates older JWTs.
    if (tokenVersion !== user.authTokenVersion) {
      return { error: 'Token has been revoked.', status: 401 };
    }

    return { userId };
  } catch {
    return { error: 'Invalid or expired token.', status: 401 };
  }
}

export function isAuthFailure(
  value: AccessUser | AuthFailure
): value is AuthFailure {
  return 'status' in value;
}
