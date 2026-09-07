import {
  Prisma,
  COLUMNS,
  table,
  idList,
  prisma,
  type SqlClient,
  type VariationRow,
} from './internals';

export type VariationListFilter = {
  projectId?: bigint;
  accessibleProjectIds?: bigint[];
  statuses?: string[];
  sourceMode?: string;
  creativeId?: bigint;
  batchId?: string;
};

function listWhere(filter: VariationListFilter): Prisma.Sql {
  const parts: Prisma.Sql[] = [Prisma.sql`TRUE`];
  if (filter.projectId !== undefined) {
    parts.push(Prisma.sql`project_id = ${filter.projectId}`);
  } else if (filter.accessibleProjectIds) {
    parts.push(
      filter.accessibleProjectIds.length
        ? Prisma.sql`(project_id IN (${idList(filter.accessibleProjectIds)}) OR project_id IS NULL)`
        : Prisma.sql`project_id IS NULL`
    );
  }
  if (filter.statuses?.length) {
    parts.push(Prisma.sql`status IN (${Prisma.join(filter.statuses)})`);
  }
  if (filter.sourceMode) {
    parts.push(Prisma.sql`source_mode = ${filter.sourceMode}`);
  }
  if (filter.creativeId !== undefined) {
    parts.push(Prisma.sql`creative_id = ${filter.creativeId}`);
  }
  if (filter.batchId) {
    parts.push(Prisma.sql`batch_id = ${filter.batchId}::uuid`);
  }
  return Prisma.join(parts, ' AND ');
}

export async function countVariations(
  schema: string,
  filter: VariationListFilter,
  db: SqlClient = prisma
): Promise<number> {
  const rows = await db.$queryRaw<{ count: bigint }[]>`
    SELECT COUNT(*)::bigint AS count FROM ${table(schema)}
    WHERE ${listWhere(filter)}`;
  return Number(rows[0]?.count ?? 0);
}

export async function listVariations(
  schema: string,
  filter: VariationListFilter,
  page: number,
  pageSize: number,
  db: SqlClient = prisma
): Promise<VariationRow[]> {
  const offset = (page - 1) * pageSize;
  return db.$queryRaw<VariationRow[]>`
    SELECT ${COLUMNS} FROM ${table(schema)}
    WHERE ${listWhere(filter)}
    ORDER BY created_at DESC, id DESC
    OFFSET ${offset} LIMIT ${pageSize}`;
}
