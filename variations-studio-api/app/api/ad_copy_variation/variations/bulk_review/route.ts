import { NextResponse } from 'next/server';

import { ApiError, responseFromUnknown, startBulkPost } from '@/lib/bulk';
import { prisma } from '@/lib/prisma';
import {
  findVariationsByIds,
  markReviewed,
} from '@/lib/variationStore';
import { serializeVariation } from '@/lib/variations';

export const dynamic = 'force-dynamic';

export async function POST(request: Request) {
  const ctx = await startBulkPost(request);
  if (!ctx.ok) return ctx.response;

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
      if (rows.some((row) => row.status !== 'draft')) {
        throw new ApiError(400, 'selected_ids must all be draft rows');
      }

      const reviewedCount = await markReviewed(
        ctx.schema,
        ctx.projectId,
        ctx.selectedBigIds,
        tx
      );
      const updatedRows = await findVariationsByIds(
        ctx.schema,
        ctx.projectId,
        ctx.selectedBigIds,
        tx
      );
      updatedRows.sort((a, b) => {
        const byDate = b.createdAt.getTime() - a.createdAt.getTime();
        if (byDate !== 0) return byDate;
        return Number(b.id - a.id);
      });

      return {
        reviewed_count: reviewedCount,
        results: updatedRows.map(serializeVariation),
      };
    });

    return NextResponse.json(payload);
  } catch (err) {
    return responseFromUnknown(err);
  }
}
