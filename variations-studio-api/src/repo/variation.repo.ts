import {
  Prisma,
  COLUMNS,
  table,
  idList,
  prisma,
  type SqlClient,
  type VariationRow,
  type VariationInsert,
} from './internals';

export async function findVariationBySlug(
  schema: string,
  slug: string,
  db: SqlClient = prisma
): Promise<VariationRow | null> {
  const value = slug.trim();
  if (!value) return null;
  const rows = await db.$queryRaw<VariationRow[]>`
    SELECT ${COLUMNS} FROM ${table(schema)}
    WHERE slug = ${value}
    LIMIT 1`;
  return rows[0] ?? null;
}

export async function findVariationById(
  schema: string,
  id: bigint,
  db: SqlClient = prisma
): Promise<VariationRow | null> {
  const rows = await db.$queryRaw<VariationRow[]>`
    SELECT ${COLUMNS} FROM ${table(schema)}
    WHERE id = ${id}
    LIMIT 1`;
  return rows[0] ?? null;
}

export async function findVariationsByIds(
  schema: string,
  projectId: bigint,
  ids: bigint[],
  db: SqlClient = prisma
): Promise<VariationRow[]> {
  if (!ids.length) return [];
  return db.$queryRaw<VariationRow[]>`
    SELECT ${COLUMNS} FROM ${table(schema)}
    WHERE project_id = ${projectId} AND id IN (${idList(ids)})`;
}

export async function findVariationsByIdsAnyProject(
  schema: string,
  ids: bigint[],
  db: SqlClient = prisma
): Promise<VariationRow[]> {
  if (!ids.length) return [];
  return db.$queryRaw<VariationRow[]>`
    SELECT ${COLUMNS} FROM ${table(schema)}
    WHERE id IN (${idList(ids)})`;
}

export async function insertVariation(
  schema: string,
  row: VariationInsert,
  db: SqlClient = prisma
): Promise<VariationRow> {
  const now = new Date();
  const rows = await db.$queryRaw<VariationRow[]>`
    INSERT INTO ${table(schema)} (
      created_at, updated_at, is_deleted, source_mode, source_ref,
      hook, headline, description, cta, instruction, model_name, prompt_version,
      batch_id, batch_position, status, created_by_id, creative_id, project_id, slug
    ) VALUES (
      ${now}, ${now}, false, ${row.sourceMode}, ${row.sourceRef},
      ${row.hook}, ${row.headline}, ${row.description}, ${row.cta},
      ${row.instruction}, ${row.modelName}, ${row.promptVersion},
      ${row.batchId}::uuid, ${row.batchPosition}, ${row.status},
      ${row.createdById}, ${row.creativeId}, ${row.projectId}, ${row.slug}
    )
    RETURNING ${COLUMNS}`;
  return rows[0];
}

export async function insertVariations(
  schema: string,
  rows: VariationInsert[],
  db: SqlClient = prisma
): Promise<VariationRow[]> {
  const saved: VariationRow[] = [];
  for (const row of rows) {
    saved.push(await insertVariation(schema, row, db));
  }
  return saved;
}

export async function updateVariationFields(
  schema: string,
  id: bigint,
  patch: {
    hook?: string;
    headline?: string;
    description?: string;
    cta?: string;
    status?: string;
  },
  db: SqlClient = prisma
): Promise<VariationRow> {
  const sets: Prisma.Sql[] = [Prisma.sql`updated_at = ${new Date()}`];
  if (patch.hook !== undefined) sets.push(Prisma.sql`hook = ${patch.hook}`);
  if (patch.headline !== undefined) sets.push(Prisma.sql`headline = ${patch.headline}`);
  if (patch.description !== undefined) {
    sets.push(Prisma.sql`description = ${patch.description}`);
  }
  if (patch.cta !== undefined) sets.push(Prisma.sql`cta = ${patch.cta}`);
  if (patch.status !== undefined) sets.push(Prisma.sql`status = ${patch.status}`);

  const rows = await db.$queryRaw<VariationRow[]>`
    UPDATE ${table(schema)}
    SET ${Prisma.join(sets, ', ')}
    WHERE id = ${id}
    RETURNING ${COLUMNS}`;
  return rows[0];
}

export async function deleteVariationById(
  schema: string,
  id: bigint,
  db: SqlClient = prisma
): Promise<void> {
  await db.$executeRaw`DELETE FROM ${table(schema)} WHERE id = ${id}`;
}

export async function deleteVariationsByIds(
  schema: string,
  ids: bigint[],
  db: SqlClient = prisma
): Promise<number> {
  if (!ids.length) return 0;
  const rows = await db.$queryRaw<{ count: bigint }[]>`
    WITH deleted AS (
      DELETE FROM ${table(schema)} WHERE id IN (${idList(ids)})
      RETURNING id
    )
    SELECT COUNT(*)::bigint AS count FROM deleted`;
  return Number(rows[0]?.count ?? 0);
}

export async function deleteVariationsForProjects(
  schema: string,
  projectIds: bigint[],
  db: SqlClient = prisma
): Promise<void> {
  if (!projectIds.length) return;
  await db.$executeRaw`
    DELETE FROM ${table(schema)} WHERE project_id IN (${idList(projectIds)})`;
}

export async function setVariationStatus(
  schema: string,
  id: bigint,
  status: string,
  db: SqlClient = prisma
): Promise<void> {
  await db.$executeRaw`
    UPDATE ${table(schema)}
    SET status = ${status}, updated_at = ${new Date()}
    WHERE id = ${id}`;
}

export async function listSlugs(
  schema: string,
  db: SqlClient = prisma
): Promise<string[]> {
  const rows = await db.$queryRaw<{ slug: string }[]>`
    SELECT slug FROM ${table(schema)}`;
  return rows.map((row) => row.slug);
}
