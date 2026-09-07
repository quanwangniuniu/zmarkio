import {
  Prisma,
  COLUMNS,
  table,
  idList,
  prisma,
  type SqlClient,
  type VariationRow,
} from './internals';

export async function findBatchVariations(
  schema: string,
  projectId: bigint,
  batchId: string,
  db: SqlClient = prisma
): Promise<VariationRow[]> {
  return db.$queryRaw<VariationRow[]>`
    SELECT ${COLUMNS} FROM ${table(schema)}
    WHERE project_id = ${projectId} AND batch_id = ${batchId}::uuid
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
