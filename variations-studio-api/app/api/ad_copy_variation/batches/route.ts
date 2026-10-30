import { NextResponse } from 'next/server';

import { isAuthFailure } from '@/lib/auth';
import { requireProjectForUser } from '@/lib/projects';
import { requireStudioContext } from '@/lib/tenant';
import {
  countBatchSummaries,
  listBatchSummaries,
} from '@/lib/variationStore';

export const dynamic = 'force-dynamic';

export async function GET(request: Request) {
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

  const rawPage = url.searchParams.get('page');
  const parsedPage = Number(rawPage?.trim() ? rawPage : 1);
  const page =
    Number.isSafeInteger(parsedPage) && parsedPage > 0
      ? parsedPage
      : 1;

  const rawPageSize = url.searchParams.get('page_size');
  const parsedPageSize = Number(rawPageSize?.trim() ? rawPageSize : 20);
  const pageSize = Number.isSafeInteger(parsedPageSize)
    ? Math.min(100, Math.max(1, parsedPageSize))
    : 20;

  const [count, rows] = await Promise.all([
    countBatchSummaries(auth.schema, project.projectId),
    listBatchSummaries(auth.schema, project.projectId, page, pageSize),
  ]);

  return NextResponse.json({
    count,
    page,
    page_size: pageSize,
    results: rows.map((row) => ({
      batch_id: row.batchId,
      created_at: row.createdAt.toISOString(),
      created_by: row.createdById === null ? null : Number(row.createdById),
      source_mode: row.sourceMode,
      instruction: row.instruction,
      model: row.modelName,
      draft_count: Number(row.draftCount),
      reviewed_count: Number(row.reviewedCount),
    })),
  });
}