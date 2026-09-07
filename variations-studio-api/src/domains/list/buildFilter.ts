import { NextResponse } from 'next/server';

import {
  activeProjectIdsForUser,
  isActiveProjectMember,
  resolveProjectId,
} from '@/lib/projects';
import type { VariationListFilter } from '@/src/repo';

const DEFAULT_PAGE_SIZE = 20;
const MAX_PAGE_SIZE = 100;

export type ListParams = {
  filter: VariationListFilter;
  page: number;
  pageSize: number;
};

export type ListParseResult =
  | { ok: true; params: ListParams }
  | { ok: false; response: NextResponse };

export async function parseListRequest(
  url: URL,
  schema: string,
  userId: number
): Promise<ListParseResult> {
  const projectParam = url.searchParams.get('project_id');
  const statusParam = url.searchParams.get('status');
  const sourceMode = url.searchParams.get('source_mode');
  const creativeParam = url.searchParams.get('creative');
  const batchId = url.searchParams.get('batch_id');
  const page = Math.max(1, Number(url.searchParams.get('page') || 1) || 1);
  const pageSize = Math.min(
    MAX_PAGE_SIZE,
    Math.max(1, Number(url.searchParams.get('page_size') || DEFAULT_PAGE_SIZE) || DEFAULT_PAGE_SIZE)
  );

  const filter: VariationListFilter = {};

  if (projectParam) {
    const projectId = await resolveProjectId(schema, projectParam);
    if (!projectId) {
      return {
        ok: false,
        response: NextResponse.json({ error: 'project_id is required' }, { status: 400 }),
      };
    }
    const allowed = await isActiveProjectMember(schema, userId, projectId);
    if (!allowed) {
      return {
        ok: false,
        response: NextResponse.json(
          { error: 'You are not a member of this project.' },
          { status: 403 }
        ),
      };
    }
    filter.projectId = projectId;
  } else {
    filter.accessibleProjectIds = await activeProjectIdsForUser(schema, userId);
  }

  if (statusParam) {
    const statuses = statusParam.split(',').map((s) => s.trim()).filter(Boolean);
    if (statuses.length) filter.statuses = statuses;
  }
  if (sourceMode) filter.sourceMode = sourceMode;
  if (creativeParam && /^\d+$/.test(creativeParam)) {
    filter.creativeId = BigInt(creativeParam);
  }
  if (batchId) filter.batchId = batchId;

  return { ok: true, params: { filter, page, pageSize } };
}
