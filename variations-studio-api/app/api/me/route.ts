import { NextResponse } from 'next/server';

import { isAuthFailure, requireAccessUser } from '@/lib/auth';

export async function GET(request: Request) {
  const result = await requireAccessUser(request);
  if (isAuthFailure(result)) {
    return NextResponse.json({ detail: result.error }, { status: result.status });
  }
  return NextResponse.json({ user_id: result.userId });
}
