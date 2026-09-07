import { NextResponse } from 'next/server';

import { isAuthFailure } from '@/lib/auth';
import { requireProjectForUser } from '@/lib/projects';
import { requireStudioContext } from '@/lib/tenant';
import { findBatchVariations, findLatestBatchRow } from '@/lib/variationStore';
import { serializeVariation } from '@/lib/variations';

export const dynamic = 'force-dynamic';

export async function GET(request: Request) {
  const auth = await requireStudioContext(request);
  if (isAuthFailure(auth)) {
    return NextResponse.json({ detail: auth.error }, { status: auth.status });
  }

  const projectParam = new URL(request.url).searchParams.get('project_id');
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

  const latest = await findLatestBatchRow(auth.schema, project.projectId);
  if (!latest?.batchId) {
    return NextResponse.json({ batch_id: null, count: 0, results: [] });
  }

  const rows = await findBatchVariations(
    auth.schema,
    project.projectId,
    latest.batchId
  );

  return NextResponse.json({
    batch_id: latest.batchId,
    count: rows.length,
    results: rows.map(serializeVariation),
  });
}
