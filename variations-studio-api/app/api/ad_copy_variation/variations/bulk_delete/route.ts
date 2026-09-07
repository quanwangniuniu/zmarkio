import { NextResponse } from 'next/server';

import { ApiError, jsonError, responseFromUnknown, startBulkPost } from '@/lib/bulk';
import { prisma } from '@/lib/prisma';
import { deleteVariationsByIds, findVariationsByIds } from '@/lib/variationStore';

export const dynamic = 'force-dynamic';

const ALLOWED_STATUSES = new Set(['draft', 'reviewed']);

export async function POST(request: Request) {
  const ctx = await startBulkPost(request);
  if (!ctx.ok) return ctx.response;

  const expectedStatus = ctx.body.status;
  if (typeof expectedStatus !== 'string' || !ALLOWED_STATUSES.has(expectedStatus)) {
    return jsonError('status must be draft or reviewed', 400);
  }

  try {
    const payload = await prisma.$transaction(async (tx) => {
      const rows = await findVariationsByIds(
        ctx.schema,
        ctx.projectId,
        ctx.selectedBigIds,
        tx
      );
      if (rows.length !== ctx.selectedIds.length) {
        throw new ApiError(400, 'selected_ids must all belong to the current project');
      }
      if (rows.some((row) => row.status !== expectedStatus)) {
        throw new ApiError(400, `selected_ids must all be ${expectedStatus} rows`);
      }

      const deletedCount = await deleteVariationsByIds(
        ctx.schema,
        ctx.selectedBigIds,
        tx
      );

      return {
        deleted_count: deletedCount,
        deleted_ids: ctx.selectedIds,
      };
    });

    return NextResponse.json(payload);
  } catch (err) {
    return responseFromUnknown(err);
  }
}
