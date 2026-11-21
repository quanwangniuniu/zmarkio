/**
 * Re-export barrel — preserves backward compatibility.
 * Actual implementations live in src/repo/.
 */
export type { VariationRow, VariationInsert, SqlClient, VariationListFilter, BatchSummaryRow } from '@/src/repo';
export {
  VARIATION_TABLE,
  findVariationBySlug,
  findVariationById,
  findVariationsByIds,
  findVariationsByIdsForUpdate,
  findBatchVariationsByIdsForUpdate,
  findVariationsByIdsAnyProject,
  insertVariation,
  insertVariations,
  updateVariationFields,
  deleteVariationById,
  deleteVariationsByIds,
  deleteVariationsByIdsForProject,
  deleteVariationsForProjects,
  setVariationStatus,
  findBatchVariations,
  findLatestBatchRow,
  listBatchSummaries,
  countBatchSummaries,
  markReviewed,
  markBatchReviewed,
  deleteUnselectedBatchDrafts,
  countVariations,
  listVariations,
} from '@/src/repo';
