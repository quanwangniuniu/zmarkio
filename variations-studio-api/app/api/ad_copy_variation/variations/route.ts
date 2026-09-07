import { NextResponse } from 'next/server';

import { isAuthFailure } from '@/lib/auth';
import { projectIdParam, readJsonBody, responseFromUnknown } from '@/lib/bulk';
import { requireProjectForUser } from '@/lib/projects';
import { requireStudioContext } from '@/lib/tenant';
import { createVariation } from '@/lib/variationCreate';
import { countVariations, listVariations } from '@/lib/variationStore';
import { serializeVariation } from '@/lib/variations';
import { parseListRequest } from '@/src/domains/list';

export const dynamic = 'force-dynamic';

export async function GET(request: Request) {
  const auth = await requireStudioContext(request);
  if (isAuthFailure(auth)) {
    return NextResponse.json({ detail: auth.error }, { status: auth.status });
  }

  const result = await parseListRequest(
    new URL(request.url),
    auth.schema,
    auth.userId
  );
  if (!result.ok) return result.response;

  const { filter, page, pageSize } = result.params;

  const [count, rows] = await Promise.all([
    countVariations(auth.schema, filter),
    listVariations(auth.schema, filter, page, pageSize),
  ]);

  return NextResponse.json({
    count,
    page,
    page_size: pageSize,
    results: rows.map(serializeVariation),
  });
}

export async function POST(request: Request) {
  const auth = await requireStudioContext(request);
  if (isAuthFailure(auth)) {
    return NextResponse.json({ detail: auth.error }, { status: auth.status });
  }

  const json = await readJsonBody(request);
  if (!json.ok) return json.response;

  const project = await requireProjectForUser(
    auth.schema,
    auth.userId,
    projectIdParam(json.body.project)
  );
  if (!project.ok) {
    return NextResponse.json(
      { [project.field]: project.error },
      { status: project.status }
    );
  }

  try {
    const row = await createVariation({
      schema: auth.schema,
      projectId: project.projectId,
      userId: auth.userId,
      body: json.body,
    });
    return NextResponse.json(serializeVariation(row), { status: 201 });
  } catch (err) {
    return responseFromUnknown(err);
  }
}
