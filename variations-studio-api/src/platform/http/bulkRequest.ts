import { NextResponse } from 'next/server';

import { isAuthFailure } from '@/lib/auth';
import { requireProjectForUser } from '@/lib/projects';
import { requireStudioContext } from '@/lib/tenant';

import { jsonError } from './errors';
import { readJsonBody } from './body';
import { parseSelectedIds, projectIdParam } from './params';

export async function startBulkPost(request: Request): Promise<
  | { ok: false; response: NextResponse }
  | {
      ok: true;
      schema: string;
      projectId: bigint;
      body: Record<string, unknown>;
      selectedIds: number[];
      selectedBigIds: bigint[];
    }
> {
  const auth = await requireStudioContext(request);
  if (isAuthFailure(auth)) {
    return {
      ok: false,
      response: NextResponse.json({ detail: auth.error }, { status: auth.status }),
    };
  }

  const json = await readJsonBody(request);
  if (!json.ok) return json;

  const project = await requireProjectForUser(
    auth.schema,
    auth.userId,
    projectIdParam(json.body.project_id)
  );
  if (!project.ok) {
    return {
      ok: false,
      response: NextResponse.json(
        { [project.field]: project.error },
        { status: project.status }
      ),
    };
  }

  const selected = parseSelectedIds(json.body.selected_ids);
  if (!selected.ok) {
    return { ok: false, response: jsonError(selected.error, 400) };
  }

  return {
    ok: true,
    schema: auth.schema,
    projectId: project.projectId,
    body: json.body,
    selectedIds: selected.ids,
    selectedBigIds: selected.ids.map((id) => BigInt(id)),
  };
}
