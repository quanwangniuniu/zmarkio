import { join } from 'node:path';
import { NextResponse } from 'next/server';

import { isAuthFailure } from '@/lib/auth';
import { projectIdParam, readJsonBody } from '@/lib/bulk';
import { runMigrations } from '@/lib/migrate';
import { requireProjectForUser } from '@/lib/projects';
import { inspectStudioSchema, isStudioReady, resolveStudioContext } from '@/lib/tenant';

export const dynamic = 'force-dynamic';

export async function POST(request: Request) {
  const context = await resolveStudioContext(request);
  if (isAuthFailure(context)) {
    return NextResponse.json({ detail: context.error }, { status: context.status });
  }

  const json = await readJsonBody(request);
  if (!json.ok) return json.response;

  const state = await inspectStudioSchema(context.schema);
  const unavailable = NextResponse.json(
    { detail: 'Studio is being initialized or upgraded. Please try again shortly.' },
    { status: 503 }
  );
  if (!state.schemaExists) return unavailable;

  const project = await requireProjectForUser(
    context.schema,
    context.userId,
    projectIdParam(json.body.project_id)
  );
  if (!project.ok) {
    return NextResponse.json(
      { [project.field]: project.error },
      { status: project.status }
    );
  }

  // Project membership authorizes creating the org's shared variation table.
  // Existing tables are upgraded by the deployment CLI, not by this endpoint.
  let report: Awaited<ReturnType<typeof runMigrations>> | undefined;
  if (!state.variationTableExists) {
    report = await runMigrations({
      command: 'apply',
      schemas: [context.schema],
      migrationsDir: join(process.cwd(), 'migrations'),
    });
  }

  if (!(await isStudioReady(context.schema))) {
    if (report) {
      const { status, error } = report.results[0];
      console.error('Studio initialization did not complete', { schema: context.schema, status, error });
    }
    return unavailable;
  }
  return NextResponse.json({ ready: true });
}
