import { NextResponse } from 'next/server';

export class ApiError extends Error {
  status: number;
  field: 'error' | 'detail';

  constructor(status: number, message: string, field: 'error' | 'detail' = 'error') {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.field = field;
  }
}

export function jsonError(error: string, status: number) {
  return NextResponse.json({ error }, { status });
}

export function responseFromUnknown(err: unknown) {
  if (err instanceof ApiError) {
    return NextResponse.json({ [err.field]: err.message }, { status: err.status });
  }
  throw err;
}
