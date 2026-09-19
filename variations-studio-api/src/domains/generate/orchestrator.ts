import { randomUUID } from 'crypto';

import {
  AI_QUOTA_MESSAGE,
  BATCH_CONCURRENCY,
  MAX_BATCH,
  META_COPY_LIMITS,
  PROMPT_VERSION,
  SYSTEM_PROMPT,
  buildLengthRetryPrompt,
  defaultCopyGenerator,
  validateCopy,
  type CopyGenerator,
  type CopyJson,
  type CopyViolation,
} from '@/src/ai';

import { ApiError, projectIdParam } from '@/src/platform/http';
import { requireProjectForUser } from '@/lib/projects';
import { allocateSlugs } from '@/lib/slugs';
import { insertVariations } from '@/src/repo';
import { serializeVariation } from '@/lib/variations';

import { isEarlyReturn } from './types';
import { sourceModeRegistry } from './modes';

export type GenerateBatchResponse = {
  batch_id: string;
  count_requested: number;
  count_succeeded: number;
  count_failed: number;
  results: ReturnType<typeof serializeVariation>[];
  failed_indices: number[];
  error?: string;
};

type GeneratedCopy = {
  copy: CopyJson;
  validationWarnings: CopyViolation[];
};

function parseCount(raw: unknown): number {
  if (raw === undefined || raw === null || raw === '') return 1;
  if (typeof raw === 'number' && Number.isInteger(raw)) return raw;
  if (typeof raw === 'string' && /^-?\d+$/.test(raw.trim())) {
    return Number(raw.trim());
  }
  throw new ApiError(400, 'count must be an integer');
}

async function generateCopies(
  userPrompt: string,
  count: number,
  generator: CopyGenerator
): Promise<{
  copies: GeneratedCopy[];
  failedIndices: number[];
  quotaFailed: boolean;
}> {
  const ordered: Array<GeneratedCopy | null> = Array.from(
    { length: count },
    () => null
  );
  const failedIndices: number[] = [];
  let quotaFailed = false;
  let next = 0;

  async function worker() {
    for (;;) {
      const index = next;
      next += 1;
      if (index >= count) return;
      try {
        const firstCopy = await generator.generateCopy(
          SYSTEM_PROMPT,
          userPrompt
        );

        let finalCopy = firstCopy;
        let warnings = validateCopy(
          firstCopy,
          META_COPY_LIMITS
        );

        if (warnings.length > 0) {
          try {
            const retryPrompt = buildLengthRetryPrompt(
              userPrompt,
              firstCopy,
              warnings
            );

            finalCopy = await generator.generateCopy(
              SYSTEM_PROMPT,
              retryPrompt
            );

            warnings = validateCopy(
              finalCopy,
              META_COPY_LIMITS
            );
          } catch (retryError) {
            console.error('Copy length retry failed', retryError);
          }
        }

        ordered[index] = {
          copy: finalCopy,
          validationWarnings: warnings,
        };
      } catch (err) {
        if (generator.isQuotaError(err)) quotaFailed = true;
        failedIndices.push(index);
      }
    }
  }

  const workers = Math.min(BATCH_CONCURRENCY, count);
  await Promise.all(Array.from({ length: workers }, () => worker()));
  failedIndices.sort((a, b) => a - b);
  return {
    copies: ordered.filter(
      (row): row is GeneratedCopy => row !== null
    ),
    failedIndices,
    quotaFailed,
  };
}

async function persistBatch(args: {
  schema: string;
  copies: GeneratedCopy[];
  batchId: string;
  projectId: bigint;
  userId: number;
  sourceMode: string;
  sourceRef: string;
  instruction: string;
  creativeId: bigint | null;
  modelName: string;
}) {
  const slugs = allocateSlugs(
    args.copies.map(({ copy }) => copy.headline)
  );
  return insertVariations(
    args.schema,
    args.copies.map(({ copy, validationWarnings }, index) => ({
      sourceMode: args.sourceMode,
      sourceRef: args.sourceRef,
      hook: copy.hook,
      headline: copy.headline,
      description: copy.description,
      cta: copy.cta,
      validationWarnings,
      instruction: args.instruction,
      modelName: args.modelName,
      promptVersion: PROMPT_VERSION,
      batchId: args.batchId,
      batchPosition: index,
      status: 'draft',
      createdById: BigInt(args.userId),
      creativeId: args.creativeId,
      projectId: args.projectId,
      slug: slugs[index],
    }))
  );
}

export async function runCustomGenerate(args: {
  schema: string;
  userId: number;
  body: Record<string, unknown>;
  /** Optional inject for tests / alternate providers. Defaults to Gemini. */
  generator?: CopyGenerator;
}): Promise<GenerateBatchResponse> {
  const generator = args.generator ?? defaultCopyGenerator;
  const count = parseCount(args.body.count);
  if (count < 1 || count > MAX_BATCH) {
    throw new ApiError(400, `count must be between 1 and ${MAX_BATCH}`);
  }

  const sourceMode = args.body.source_mode;
  if (typeof sourceMode !== 'string' || !sourceModeRegistry.has(sourceMode)) {
    throw new ApiError(400, `unknown source_mode: ${sourceMode}`);
  }

  const project = await requireProjectForUser(
    args.schema,
    args.userId,
    projectIdParam(args.body.project_id)
  );
  if (!project.ok) {
    throw new ApiError(project.status, project.error, project.field);
  }

  const instruction =
    typeof args.body.instruction === 'string' ? args.body.instruction : '';

  const handler = sourceModeRegistry.get(sourceMode)!;
  const modeResult = await handler.resolve(
    { schema: args.schema, userId: args.userId, projectId: project.projectId, instruction },
    args.body
  );

  if (isEarlyReturn(modeResult)) return modeResult.response;

  const batchId = randomUUID();
  const { copies, failedIndices, quotaFailed } = await generateCopies(
    modeResult.userPrompt,
    count,
    generator
  );

  const saved = copies.length
    ? await persistBatch({
        schema: args.schema,
        copies,
        batchId,
        projectId: project.projectId,
        userId: args.userId,
        sourceMode,
        sourceRef: modeResult.sourceRef,
        instruction,
        creativeId: modeResult.creativeId,
        modelName: generator.modelName,
      })
    : [];

  const payload: GenerateBatchResponse = {
    batch_id: batchId,
    count_requested: count,
    count_succeeded: copies.length,
    count_failed: failedIndices.length,
    results: saved.map(serializeVariation),
    failed_indices: failedIndices,
  };
  if (!copies.length && quotaFailed) {
    payload.error = AI_QUOTA_MESSAGE;
  }
  return payload;
}
