import {
  Prisma,
  COLUMNS,
  table,
  idList,
  prisma,
  type SqlClient,
  type VariationRow,
} from './internals';

export type BatchSummaryRow = {
  batchId: string;
  createdAt: Date;
  createdById: bigint | null;
  sourceMode: string;
  instruction: string;
  modelName: string;
  draftCount: bigint;
  reviewedCount: bigint;
};

export async function findBatchVariations(
  schema: string,
  projectId: bigint,
  batchId: string,
  db: SqlClient = prisma
): Promise<VariationRow[]> {
  return db.$queryRaw<VariationRow[]>`
    SELECT ${COLUMNS} FROM ${table(schema)}
    WHERE project_id = ${projectId}
    AND batch_id = ${batchId}::uuid
    AND is_deleted = false
    ORDER BY batch_position ASC, id ASC`;
}

export async function findLatestBatchRow(
  schema: string,
  projectId: bigint,
  db: SqlClient = prisma
): Promise<VariationRow | null> {
  const rows = await db.$queryRaw<VariationRow[]>`
    SELECT ${COLUMNS} FROM ${table(schema)}
    WHERE project_id = ${projectId} AND batch_id IS NOT NULL
    ORDER BY created_at DESC, id DESC
    LIMIT 1`;
  return rows[0] ?? null;
}

export async function listBatchSummaries(
  schema: string,
  projectId: bigint,
  page: number,
  pageSize: number,
  db: SqlClient = prisma
): Promise<BatchSummaryRow[]> {
  const offset = (page - 1) * pageSize;

  return db.$queryRaw<BatchSummaryRow[]>`
    WITH representative_variations AS (
      SELECT DISTINCT ON (batch_id)
        batch_id,
        created_at,
        created_by_id,
        source_mode,
        instruction,
        model_name
      FROM ${table(schema)}
      WHERE project_id = ${projectId}
        AND batch_id IS NOT NULL
        AND is_deleted = false
      ORDER BY
        batch_id,
        batch_position ASC NULLS LAST,
        id ASC
    ),
    paginated_batches AS (
      SELECT
        batch_id,
        created_at,
        created_by_id,
        source_mode,
        instruction,
        model_name
      FROM representative_variations
      ORDER BY created_at DESC, batch_id DESC
      OFFSET ${offset}
      LIMIT ${pageSize}
    ),
    page_counts AS (
      SELECT
        v.batch_id,
        COUNT(*) FILTER (WHERE v.status = 'draft')::bigint AS "draftCount",
        COUNT(*) FILTER (WHERE v.status = 'reviewed')::bigint AS "reviewedCount"
      FROM ${table(schema)} v
      INNER JOIN paginated_batches p
        ON p.batch_id = v.batch_id
      WHERE v.project_id = ${projectId}
        AND v.is_deleted = false
      GROUP BY v.batch_id
    )
    SELECT
      p.batch_id AS "batchId",
      p.created_at AS "createdAt",
      p.created_by_id AS "createdById",
      p.source_mode AS "sourceMode",
      p.instruction AS instruction,
      p.model_name AS "modelName",
      c."draftCount",
      c."reviewedCount"
    FROM paginated_batches p
    INNER JOIN page_counts c
      ON c.batch_id = p.batch_id
    ORDER BY p.created_at DESC, p.batch_id DESC`;
}

export async function countBatchSummaries(
  schema: string,
  projectId: bigint,
  db: SqlClient = prisma
): Promise<number> {
  const rows = await db.$queryRaw<{ count: bigint }[]>`
    SELECT COUNT(DISTINCT batch_id)::bigint AS count
    FROM ${table(schema)}
    WHERE project_id = ${projectId}
      AND batch_id IS NOT NULL
      AND is_deleted = false`;

  return Number(rows[0]?.count ?? 0);
}

export async function markReviewed(
  schema: string,
  projectId: bigint,
  ids: bigint[],
  db: SqlClient = prisma
): Promise<number> {
  if (!ids.length) return 0;
  const rows = await db.$queryRaw<{ count: bigint }[]>`
    WITH updated AS (
      UPDATE ${table(schema)}
      SET status = 'reviewed', updated_at = ${new Date()}
      WHERE project_id = ${projectId} AND id IN (${idList(ids)})
      RETURNING id
    )
    SELECT COUNT(*)::bigint AS count FROM updated`;
  return Number(rows[0]?.count ?? 0);
}

export async function markBatchReviewed(
  schema: string,
  projectId: bigint,
  batchId: string,
  ids: bigint[],
  db: SqlClient = prisma
): Promise<number> {
  if (!ids.length) return 0;
  const rows = await db.$queryRaw<{ count: bigint }[]>`
    WITH updated AS (
      UPDATE ${table(schema)}
      SET status = 'reviewed', updated_at = ${new Date()}
      WHERE project_id = ${projectId}
        AND batch_id = ${batchId}::uuid
        AND status = 'draft'
        AND id IN (${idList(ids)})
      RETURNING id
    )
    SELECT COUNT(*)::bigint AS count FROM updated`;
  return Number(rows[0]?.count ?? 0);
}

export async function deleteUnselectedBatchDrafts(
  schema: string,
  projectId: bigint,
  batchId: string,
  keepIds: bigint[],
  db: SqlClient = prisma
): Promise<number> {
  const keepClause = keepIds.length
    ? Prisma.sql`AND id NOT IN (${idList(keepIds)})`
    : Prisma.empty;
  const rows = await db.$queryRaw<{ count: bigint }[]>`
    WITH deleted AS (
      DELETE FROM ${table(schema)}
      WHERE project_id = ${projectId}
        AND batch_id = ${batchId}::uuid
        AND status = 'draft'
        ${keepClause}
      RETURNING id
    )
    SELECT COUNT(*)::bigint AS count FROM deleted`;
  return Number(rows[0]?.count ?? 0);
}
