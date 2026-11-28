import { NextResponse } from 'next/server';

import { isAuthFailure } from '@/lib/auth';
import { serializePlatform } from '@/lib/platforms';
import { resolveStudioContext } from '@/lib/tenant';
import { getPlatformSpec, listPlatformIds } from '@/src/platforms';

export const dynamic = 'force-dynamic';

export async function GET(request: Request) {
  const context = await resolveStudioContext(request);
  if (isAuthFailure(context)) {
    return NextResponse.json({ detail: context.error }, { status: context.status });
  }

  const platforms = listPlatformIds().map(id => serializePlatform(getPlatformSpec(id)));
  return NextResponse.json({ platforms });
}
