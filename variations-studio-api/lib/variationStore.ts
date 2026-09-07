/**
 * Re-export barrel — preserves backward compatibility.
 * Actual implementations live in src/repo/.
 */
export type { VariationRow, VariationInsert, SqlClient, VariationListFilter } from '@/src/repo';
export {
  VARIATION_TABLE,
  findVariationBySlug,
  findVariationById,
  findVariationsByIds,
  findVariationsByIdsAnyProject,
  insertVariation,
  insertVariations,
  updateVariationFields,
  deleteVariationById,
  deleteVariationsByIds,
  deleteVariationsForProjects,
  setVariationStatus,
  listSlugs,
  findBatchVariations,
  findLatestBatchRow,
  markReviewed,
  markBatchReviewed,
  deleteUnselectedBatchDrafts,
  countVariations,
  listVariations,
} from '@/src/repo';
