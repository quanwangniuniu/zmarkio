import { NextResponse } from 'next/server';

import { isAuthFailure } from '@/lib/auth';
import { requireProjectForUser } from '@/lib/projects';
import { requireStudioContext } from '@/lib/tenant';
import { findBatchVariations } from '@/lib/variationStore';
import { serializeVariation } from '@/lib/variations';

export const dynamic = 'force-dynamic';

type RouteContext = {
  params: { batch_id: string };
};

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function GET(request: Request, context: RouteContext) {
  const auth = await requireStudioContext(request);

  if (isAuthFailure(auth)) {
    return NextResponse.json(
      { detail: auth.error },
      { status: auth.status }
    );
  }

  const url = new URL(request.url);
  const projectParam = url.searchParams.get('project_id');

  const project = await requireProjectForUser(
    auth.schema,
    auth.userId,
    projectParam
  );

  if (!project.ok) {
    return NextResponse.json(
      { [project.field]: project.error },
      { status: project.status }
    );
  }

  const batchId = context.params.batch_id;

  if (!UUID_PATTERN.test(batchId)) {
    return NextResponse.json(
      { detail: 'Invalid batch_id.' },
      { status: 400 }
    );
  }

  const rows = await findBatchVariations(
    auth.schema,
    project.projectId,
    batchId
  );

  return NextResponse.json({
    batch_id: batchId,
    count: rows.length,
    results: rows.map(serializeVariation),
  });
}
