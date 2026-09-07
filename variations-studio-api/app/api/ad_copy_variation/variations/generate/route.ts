import { NextResponse } from 'next/server';

import { isAuthFailure } from '@/lib/auth';
import { readJsonBody, responseFromUnknown } from '@/lib/bulk';
import { runCustomGenerate } from '@/lib/generate';
import { requireStudioContext } from '@/lib/tenant';

export const dynamic = 'force-dynamic';

export async function POST(request: Request) {
  const auth = await requireStudioContext(request);
  if (isAuthFailure(auth)) {
    return NextResponse.json({ detail: auth.error }, { status: auth.status });
  }

  const json = await readJsonBody(request);
  if (!json.ok) return json.response;

  try {
    const payload = await runCustomGenerate({
      schema: auth.schema,
      userId: auth.userId,
      body: json.body,
    });
    const status = payload.count_succeeded === 0 ? 502 : 200;
    return NextResponse.json(payload, { status });
  } catch (err) {
    return responseFromUnknown(err);
  }
}
