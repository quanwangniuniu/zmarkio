import { randomUUID } from 'crypto';

import {
  BATCH_CONCURRENCY,
  MAX_BATCH,
  defaultCopyGenerator,
  type CopyGenerator,
  type CopyJson,
} from '@/src/ai';
import { buildRetryPrompt, buildSystemPrompt } from '@/src/ai/prompts';
import { ApiError, projectIdParam } from '@/src/platform/http';
import type { PlatformSpec } from '@/src/platforms';
import { applyCtaPolicy } from '@/src/platforms/cta';
import { validateCopy, type CopyViolation } from '@/src/platforms/validateCopy';
import { resolveRequestPlatform } from '@/lib/platforms';
import { requireProjectForUser } from '@/lib/projects';
import { makeSlug, readSlugSource } from '@/lib/slugs';
import { completeDraftFields } from '@/lib/variationCopy';
import { insertVariations } from '@/src/repo';
import { serializeVariation } from '@/lib/variations';

import { isEarlyReturn } from './types';
import { sourceModeRegistry } from './modes';

type FieldFailure = { index: number; violations: CopyViolation[] };

export type GenerateBatchResponse = {
  batch_id: string;
  count_requested: number;
  count_succeeded: number;
  count_failed: number;
  results: ReturnType<typeof serializeVariation>[];
  failed_indices: number[];
  field_failures?: FieldFailure[];
  error?: string;
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
  spec: PlatformSpec,
  userPrompt: string,
  count: number,
  generator: CopyGenerator
): Promise<{
  copies: CopyJson[];
  failedIndices: number[];
  fieldFailures: FieldFailure[];
  providerError?: string;
}> {
  const systemPrompt = buildSystemPrompt(spec);
  const ordered: Array<CopyJson | null> = Array.from({ length: count }, () => null);
  const failedIndices: number[] = [];
  const fieldFailures: FieldFailure[] = [];
  let providerError: string | undefined;
  let next = 0;

  async function requestCopy(index: number, prompt: string): Promise<CopyJson | null> {
    try {
      return await generator.generateCopy(systemPrompt, prompt, spec);
    } catch (err) {
      const message = generator.getErrorMessage(err);
      if (!providerError && message) providerError = message;
      failedIndices.push(index);
      return null;
    }
  }

  async function worker() {
    for (;;) {
      const index = next;
      next += 1;
      if (index >= count) return;
      const first = await requestCopy(index, userPrompt);
      if (!first) continue;
      // Keep validateCopy outside requestCopy so an invalid platform spec stops the batch before saving.
      const violations = validateCopy(spec, first);
      if (violations.length === 0) {
        ordered[index] = first;
        continue;
      }

      const retry = await requestCopy(index, buildRetryPrompt(userPrompt, violations));
      if (!retry) continue;
      const finalViolations = validateCopy(spec, retry);
      if (finalViolations.length > 0) {
        failedIndices.push(index);
        fieldFailures.push({ index, violations: finalViolations });
      } else {
        ordered[index] = retry;
      }
    }
  }

  const workers = Math.min(BATCH_CONCURRENCY, count);
  await Promise.all(Array.from({ length: workers }, () => worker()));
  failedIndices.sort((a, b) => a - b);
  fieldFailures.sort((a, b) => a.index - b.index);
  return {
    copies: ordered.filter((row): row is CopyJson => row !== null),
    failedIndices,
    fieldFailures,
    providerError,
  };
}

async function persistBatch(args: {
  schema: string;
  spec: PlatformSpec;
  copies: CopyJson[];
  batchId: string;
  projectId: bigint;
  userId: number;
  sourceMode: string;
  sourceRef: string;
  instruction: string;
  creativeId: bigint | null;
  modelName: string;
}) {
  const { spec } = args;
  const rows = args.copies.map((copy, index) => {
    const fields = completeDraftFields(spec, copy);
    if (spec.cta.kind !== 'none') {
      fields[spec.cta.field] = applyCtaPolicy(spec.cta, fields[spec.cta.field]);
    }
    return {
      sourceMode: args.sourceMode,
      sourceRef: args.sourceRef,
      platform: spec.id,
      fields,
      instruction: args.instruction,
      modelName: args.modelName,
      promptVersion: spec.promptVersion,
      batchId: args.batchId,
      batchPosition: index,
      status: 'draft',
      createdById: BigInt(args.userId),
      creativeId: args.creativeId,
      projectId: args.projectId,
      slug: makeSlug(readSlugSource(fields, spec)),
    };
  });

  // insertVariations opens the transaction itself when no client is passed.
  return insertVariations(args.schema, rows);
}

export async function runCustomGenerate(args: {
  schema: string;
  organizationId: bigint;
  userId: number;
  body: Record<string, unknown>;
  /** Optional inject for tests / alternate providers. Defaults to Ollama. */
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

  const spec = resolveRequestPlatform(args.body.platform);
  const handler = sourceModeRegistry.get(sourceMode)!;
  const modeResult = await handler.resolve(
    {
      schema: args.schema,
      organizationId: args.organizationId,
      userId: args.userId,
      projectId: project.projectId,
      count,
      instruction,
      spec,
    },
    args.body
  );

  if (isEarlyReturn(modeResult)) return modeResult.response;

  const batchId = randomUUID();
  const { copies, failedIndices, fieldFailures, providerError } = await generateCopies(
    spec,
    modeResult.userPrompt,
    count,
    generator
  );

  const saved = copies.length
    ? await persistBatch({
        schema: args.schema,
        spec,
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
  if (fieldFailures.length > 0) payload.field_failures = fieldFailures;
  if (failedIndices.length > 0 && providerError) {
    payload.error = providerError;
  }
  return payload;
}
