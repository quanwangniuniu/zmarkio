import { assignUniqueSlugs, slugLookup, slugWithSuffix } from '@/lib/slugs';

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

const MAX_SLUG_ATTEMPTS = 5;

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
  const baseSlug = row.slug;
  let counter = 0;

  while (true) {
    const slug = slugWithSuffix(baseSlug, counter);
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
        ${row.createdById}, ${row.creativeId}, ${row.projectId}, ${slug}
      )
      ON CONFLICT (slug) DO NOTHING
      RETURNING ${COLUMNS}`;

    if (rows[0]) {
      return rows[0];
    }

    counter += 1;
  }
}

function occupancyClause(base: string): { key: string; clause: Prisma.Sql } {
  const lookup = slugLookup(base);
  if (lookup.kind === 'short') {
    return {
      key: `short:${lookup.base}`,
      clause: Prisma.sql`(slug = ${lookup.base} OR slug LIKE ${lookup.pattern} ESCAPE '\\')`,
    };
  }
  return {
    key: `long:${lookup.pattern}`,
    clause: Prisma.sql`slug LIKE ${lookup.pattern} ESCAPE '\\'`,
  };
}

async function findUsedSlugs(
  schema: string,
  bases: string[],
  db: SqlClient
): Promise<Set<string>> {
  const seen = new Set<string>();
  const clauses: Prisma.Sql[] = [];
  for (const base of bases) {
    const occupancy = occupancyClause(base);
    if (seen.has(occupancy.key)) continue;
    seen.add(occupancy.key);
    clauses.push(occupancy.clause);
  }

  const rows = await db.$queryRaw<{ slug: string }[]>`
    SELECT slug FROM ${table(schema)}
    WHERE ${Prisma.join(clauses, ' OR ')}`;
  return new Set(rows.map((row) => row.slug));
}

async function insertVariationRows(
  schema: string,
  rows: VariationInsert[],
  db: SqlClient
): Promise<VariationRow[]> {
  // Slug order matches the unique-index lock order across concurrent batches.
  const ordered = [...rows].sort((left, right) =>
    left.slug < right.slug ? -1 : left.slug > right.slug ? 1 : 0
  );
  const now = new Date();
  const values = ordered.map((row) => Prisma.sql`(
    ${now}, ${now}, false, ${row.sourceMode}, ${row.sourceRef},
    ${row.hook}, ${row.headline}, ${row.description}, ${row.cta},
    ${row.instruction}, ${row.modelName}, ${row.promptVersion},
    ${row.batchId}::uuid, ${row.batchPosition}, ${row.status},
    ${row.createdById}, ${row.creativeId}, ${row.projectId}, ${row.slug}
  )`);

  return db.$queryRaw<VariationRow[]>`
    INSERT INTO ${table(schema)} (
      created_at, updated_at, is_deleted, source_mode, source_ref,
      hook, headline, description, cta, instruction, model_name, prompt_version,
      batch_id, batch_position, status, created_by_id, creative_id, project_id, slug
    ) VALUES ${Prisma.join(values)}
    ON CONFLICT (slug) DO NOTHING
    RETURNING ${COLUMNS}`;
}

export async function insertVariations(
  schema: string,
  rows: VariationInsert[],
  db: SqlClient = prisma
): Promise<VariationRow[]> {
  if (!rows.length) return [];

  const pending = rows.map((row, index) => ({
    index,
    baseSlug: row.slug,
    row,
  }));
  const saved: Array<VariationRow | undefined> = Array(rows.length);

  for (let attempt = 0; attempt < MAX_SLUG_ATTEMPTS && pending.length; attempt += 1) {
    const used = await findUsedSlugs(
      schema,
      pending.map((item) => item.baseSlug),
      db
    );
    const slugs = assignUniqueSlugs(
      pending.map((item) => item.baseSlug),
      used
    );
    const inserted = await insertVariationRows(
      schema,
      pending.map((item, index) => ({ ...item.row, slug: slugs[index] })),
      db
    );
    const insertedBySlug = new Map(inserted.map((row) => [row.slug, row]));

    for (let index = pending.length - 1; index >= 0; index -= 1) {
      const row = insertedBySlug.get(slugs[index]);
      if (!row) continue;
      saved[pending[index].index] = row;
      pending.splice(index, 1);
    }
  }

  if (pending.length) {
    throw new Error('Could not allocate unique variation slugs');
  }
  return saved as VariationRow[];
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
