import type { VariationRow } from '@/lib/variationStore';
import { findVariationBySlug as findBySlug } from '@/lib/variationStore';
import { toJsonSafe } from '@/lib/projects';

export function serializeVariation(row: VariationRow) {
  return {
    id: toJsonSafe(row.id),
    slug: row.slug,
    project: toJsonSafe(row.projectId),
    creative: toJsonSafe(row.creativeId),
    source_mode: row.sourceMode,
    source_ref: row.sourceRef,
    hook: row.hook,
    headline: row.headline,
    description: row.description,
    cta: row.cta,
    instruction: row.instruction,
    model_name: row.modelName,
    prompt_version: row.promptVersion,
    batch_id: row.batchId,
    batch_position: row.batchPosition,
    status: row.status,
    created_by: toJsonSafe(row.createdById),
    created_at: row.createdAt.toISOString(),
    updated_at: row.updatedAt.toISOString(),
  };
}

/**
 * Detail lookups are slug-only, matching core.slug_mixins.SlugLookupViewSetMixin:
 * "Numeric identifiers are not resolved and yield 404." lib/slugs.ts already
 * prefixes purely numeric slugs so the two halves cannot collide.
 */
export async function findVariationBySlug(schema: string, slug: string) {
  return findBySlug(schema, slug);
}
