import { NextResponse } from 'next/server';

import { jsonError } from './errors';

export async function readJsonBody(
  request: Request
): Promise<
  | { ok: true; body: Record<string, unknown> }
  | { ok: false; response: NextResponse }
> {
  try {
    const body = await request.json();
    if (!body || typeof body !== 'object' || Array.isArray(body)) {
      return { ok: false, response: jsonError('Invalid JSON body', 400) };
    }
    return { ok: true, body: body as Record<string, unknown> };
  } catch {
    return { ok: false, response: jsonError('Invalid JSON body', 400) };
  }
}
