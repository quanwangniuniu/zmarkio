import { NextResponse } from 'next/server';

import { ApiError, jsonError, responseFromUnknown, startBulkPost } from '@/lib/bulk';
import { prisma } from '@/lib/prisma';
import {
  deleteUnselectedBatchDrafts,
  findBatchVariations,
  findBatchVariationsByIdsForUpdate,
  markBatchReviewed,
} from '@/lib/variationStore';
import { serializeVariation } from '@/lib/variations';

export const dynamic = 'force-dynamic';

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function POST(request: Request) {
  const ctx = await startBulkPost(request);
  if (!ctx.ok) return ctx.response;

  const batchId =
    typeof ctx.body.batch_id === 'string' ? ctx.body.batch_id.trim() : '';
  if (!batchId) {
    return jsonError('batch_id is required', 400);
  }
  if (!UUID_PATTERN.test(batchId)) {
    return jsonError('batch_id must be a UUID', 400);
  }

  try {
    const payload = await prisma.$transaction(async (tx) => {
      const inBatch = await findBatchVariationsByIdsForUpdate(
        ctx.schema,
        ctx.projectId,
        batchId,
        ctx.selectedBigIds,
        tx
      );
      if (inBatch.length !== ctx.selectedIds.length) {
        throw new ApiError(
          400,
          'selected_ids must all belong to the current project and batch'
        );
      }
      if (inBatch.some((row) => row.status !== 'draft')) {
        throw new ApiError(400, 'selected_ids must all be draft rows');
      }

      const reviewedCount = await markBatchReviewed(
        ctx.schema,
        ctx.projectId,
        batchId,
        ctx.selectedBigIds,
        tx
      );
      const deletedCount = await deleteUnselectedBatchDrafts(
        ctx.schema,
        ctx.projectId,
        batchId,
        ctx.selectedBigIds,
        tx
      );
      const remaining = await findBatchVariations(
        ctx.schema,
        ctx.projectId,
        batchId,
        tx
      );

      return {
        batch_id: batchId,
        reviewed_count: reviewedCount,
        deleted_count: deletedCount,
        results: remaining.map(serializeVariation),
      };
    });

    return NextResponse.json(payload);
  } catch (err) {
    return responseFromUnknown(err);
  }
}
