import { SignJWT } from 'jose';

// Mirrors the payload rest_framework_simplejwt puts in an access token, so the
// tests exercise lib/auth against the shape Django actually issues.

const ACCESS_LIFETIME_SECONDS = 5 * 60;

function signingKey(secret?: string): Uint8Array {
  const value = secret ?? process.env.SECRET_KEY;
  if (!value) {
    throw new Error(
      'SECRET_KEY is not set. Run these tests inside the variations-studio-api container.'
    );
  }
  return new TextEncoder().encode(value);
}

type TokenOptions = {
  /** When set, include token_type; when null, omit the claim entirely. */
  tokenType?: string | null;
  expiresInSeconds?: number;
  secret?: string;
  /** Mirrors Django build_user_refresh_token → access claim. Default 0. */
  authTokenVersion?: number;
};

export async function signToken(
  userId: number,
  options: TokenOptions = {}
): Promise<string> {
  const now = Math.floor(Date.now() / 1000);
  const lifetime = options.expiresInSeconds ?? ACCESS_LIFETIME_SECONDS;

  const claims: Record<string, unknown> = {
    user_id: userId,
    auth_token_version: options.authTokenVersion ?? 0,
    jti: Math.random().toString(16).slice(2),
  };
  if (options.tokenType !== null) {
    claims.token_type = options.tokenType ?? 'access';
  }

  return new SignJWT(claims)
    .setProtectedHeader({ alg: 'HS256', typ: 'JWT' })
    .setIssuedAt(now)
    .setExpirationTime(now + lifetime)
    .sign(signingKey(options.secret));
}

export function accessToken(
  userId: number,
  authTokenVersion = 0
): Promise<string> {
  return signToken(userId, { authTokenVersion });
}

/** Signed HS256 token that deliberately omits token_type (forgery / lax client). */
export function accessTokenWithoutType(userId: number): Promise<string> {
  return signToken(userId, { tokenType: null });
}

export function expiredAccessToken(userId: number): Promise<string> {
  return signToken(userId, { expiresInSeconds: -60 });
}

export function refreshToken(userId: number): Promise<string> {
  return signToken(userId, { tokenType: 'refresh' });
}

export function foreignlySignedToken(userId: number): Promise<string> {
  return signToken(userId, { secret: 'not-the-django-secret-key' });
}
