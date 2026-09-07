import { ApiError } from '@/lib/bulk';
import { prisma } from '@/lib/prisma';
import { MODEL_NAME, PROMPT_VERSION } from '@/lib/prompts';
import { allocateSlugs } from '@/lib/slugs';
import { insertVariation } from '@/lib/variationStore';

// Field rules come from AdCopyVariationSerializer plus the model defaults:
// source_mode is the only required field, project and created_by are set by the
// view, and slug is derived from headline.

const SOURCE_MODES = new Set(['existing', 'custom', 'external_url']);
const STATUSES = new Set(['draft', 'reviewed']);
const TEXT_FIELDS = [
  'source_ref',
  'hook',
  'headline',
  'description',
  'cta',
] as const;
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function optionalText(body: Record<string, unknown>, field: string): string {
  const value = body[field];
  if (value === undefined || value === null) return '';
  if (typeof value !== 'string') {
    throw new ApiError(400, `${field} must be a string`);
  }
  return value;
}

function optionalBatchId(raw: unknown): string | null {
  if (raw === undefined || raw === null || raw === '') return null;
  if (typeof raw !== 'string' || !UUID_PATTERN.test(raw.trim())) {
    throw new ApiError(400, 'batch_id must be a UUID');
  }
  return raw.trim();
}

function optionalBatchPosition(raw: unknown): number | null {
  if (raw === undefined || raw === null || raw === '') return null;
  const value =
    typeof raw === 'number'
      ? raw
      : typeof raw === 'string' && /^\d+$/.test(raw.trim())
        ? Number(raw.trim())
        : NaN;
  if (!Number.isInteger(value) || value < 0) {
    throw new ApiError(400, 'batch_position must be a non-negative integer');
  }
  return value;
}

async function resolveCreativeId(
  raw: unknown,
  projectId: bigint
): Promise<bigint | null> {
  if (raw === undefined || raw === null || raw === '') return null;

  const value =
    typeof raw === 'number' && Number.isInteger(raw) && raw > 0
      ? BigInt(raw)
      : typeof raw === 'string' && /^\d+$/.test(raw.trim())
        ? BigInt(raw.trim())
        : null;
  if (value === null) {
    throw new ApiError(404, 'Not found.', 'detail');
  }

  const creative = await prisma.metaAdCreative.findFirst({
    where: { id: value },
    select: { id: true, adAccountId: true },
  });
  if (!creative) {
    throw new ApiError(404, 'Not found.', 'detail');
  }

  const account = await prisma.metaAdAccount.findFirst({
    where: { id: creative.adAccountId },
    select: { projectId: true },
  });
  if (account?.projectId && account.projectId !== projectId) {
    throw new ApiError(400, 'creative does not belong to project');
  }

  return creative.id;
}

export async function createVariation(args: {
  schema: string;
  projectId: bigint;
  userId: number;
  body: Record<string, unknown>;
}) {
  const sourceMode = args.body.source_mode;
  if (typeof sourceMode !== 'string' || !SOURCE_MODES.has(sourceMode)) {
    throw new ApiError(400, 'source_mode must be existing, custom or external_url');
  }

  const status =
    args.body.status === undefined || args.body.status === null
      ? 'draft'
      : args.body.status;
  if (typeof status !== 'string' || !STATUSES.has(status)) {
    throw new ApiError(400, 'status must be draft or reviewed');
  }

  const text = Object.fromEntries(
    TEXT_FIELDS.map((field) => [field, optionalText(args.body, field)])
  ) as Record<(typeof TEXT_FIELDS)[number], string>;

  const creativeId = await resolveCreativeId(args.body.creative, args.projectId);
  const [slug] = await allocateSlugs(args.schema, [text.headline]);

  return insertVariation(args.schema, {
    sourceMode,
    sourceRef: text.source_ref,
    hook: text.hook,
    headline: text.headline,
    description: text.description,
    cta: text.cta,
    instruction: optionalText(args.body, 'instruction'),
    modelName: optionalText(args.body, 'model_name') || MODEL_NAME,
    promptVersion: optionalText(args.body, 'prompt_version') || PROMPT_VERSION,
    batchId: optionalBatchId(args.body.batch_id),
    batchPosition: optionalBatchPosition(args.body.batch_position),
    status,
    createdById: BigInt(args.userId),
    creativeId,
    projectId: args.projectId,
    slug,
  });
}
