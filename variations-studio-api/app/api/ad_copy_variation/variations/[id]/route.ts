import { NextResponse } from 'next/server';

import { isAuthFailure } from '@/lib/auth';
import { readJsonBody, responseFromUnknown } from '@/lib/bulk';
import { isActiveProjectMember } from '@/lib/projects';
import { requireStudioContext } from '@/lib/tenant';
import { readDraftFields } from '@/lib/variationCopy';
import {
  deleteVariationById,
  updateVariationFields,
  type VariationRow,
} from '@/lib/variationStore';
import { findVariationBySlug, serializeVariation } from '@/lib/variations';
import { getPlatformSpec, listPlatformIds, type PlatformSpec } from '@/src/platforms';
import { applyCtaPolicy } from '@/src/platforms/cta';
import { formatCopyViolation, validateCopy } from '@/src/platforms/validateCopy';

export const dynamic = 'force-dynamic';

const WRITABLE_STATUSES = new Set(['draft', 'reviewed']);

// The path segment is a slug; the param stays named `id` to keep the Django URL.
type RouteContext = { params: { id: string } };

type LoadedVariation =
  | { ok: false; response: NextResponse }
  | { ok: true; schema: string; row: VariationRow };

async function loadOwnedVariation(
  request: Request,
  idOrSlug: string
): Promise<LoadedVariation> {
  const auth = await requireStudioContext(request);
  if (isAuthFailure(auth)) {
    return {
      ok: false,
      response: NextResponse.json({ detail: auth.error }, { status: auth.status }),
    };
  }

  const row = await findVariationBySlug(auth.schema, idOrSlug);
  if (!row) {
    return {
      ok: false,
      response: NextResponse.json({ detail: 'Not found.' }, { status: 404 }),
    };
  }

  if (row.projectId) {
    const allowed = await isActiveProjectMember(
      auth.schema,
      auth.userId,
      row.projectId
    );
    if (!allowed) {
      return {
        ok: false,
        response: NextResponse.json(
          { error: 'You are not a member of this project.' },
          { status: 403 }
        ),
      };
    }
  }

  return { ok: true, schema: auth.schema, row };
}

export async function GET(request: Request, context: RouteContext) {
  const loaded = await loadOwnedVariation(request, context.params.id);
  if (!loaded.ok) return loaded.response;
  return NextResponse.json(serializeVariation(loaded.row));
}

function copyEditErrors(spec: PlatformSpec, copy: Readonly<Record<string, string | undefined>>): string[] {
  const errors = validateCopy(spec, copy).map(formatCopyViolation);
  const { cta } = spec;
  if (cta.kind === 'enum') {
    const value = copy[cta.field];
    if (value !== undefined && !cta.values.includes(value)) {
      const example = applyCtaPolicy(cta, value);
      errors.push(`${cta.field} must exactly match an allowed value (for example, ${example})`);
    }
  }
  return errors;
}

export async function PATCH(request: Request, context: RouteContext) {
  const loaded = await loadOwnedVariation(request, context.params.id);
  if (!loaded.ok) return loaded.response;

  const json = await readJsonBody(request);
  if (!json.ok) return json.response;
  const { body } = json;

  if ('project' in body) {
    return NextResponse.json(
      { error: 'project cannot be modified' },
      { status: 400 }
    );
  }

  const status = body.status;
  if (status !== undefined && (typeof status !== 'string' || !WRITABLE_STATUSES.has(status))) {
    return NextResponse.json({ error: 'status must be draft or reviewed' }, { status: 400 });
  }

  try {
    const { platform } = loaded.row;
    const spec = listPlatformIds().includes(platform) ? getPlatformSpec(platform) : undefined;
    const fields = readDraftFields(body, spec, 'patch');
    if (spec) {
      const errors = copyEditErrors(spec, fields);
      if (errors.length) {
        return NextResponse.json({ error: errors.join('. ') }, { status: 400 });
      }
    }
    // Unregistered historical platforms can still edit their legacy text columns.
    const patch = spec ? { fields, status } : { ...fields, status };
    const updated = await updateVariationFields(loaded.schema, loaded.row, patch);
    return NextResponse.json(serializeVariation(updated));
  } catch (error) {
    return responseFromUnknown(error);
  }
}

export async function DELETE(request: Request, context: RouteContext) {
  const loaded = await loadOwnedVariation(request, context.params.id);
  if (!loaded.ok) return loaded.response;

  await deleteVariationById(loaded.schema, loaded.row.id);
  return new NextResponse(null, { status: 204 });
}
