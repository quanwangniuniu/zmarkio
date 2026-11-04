import { randomUUID } from 'crypto';

import { prisma } from '@/lib/prisma';
import { slugWithSuffix } from '@/lib/slugs';
import { tenantTable } from '@/lib/tenant';
import {
  insertVariations,
  VARIATION_TABLE,
  type SqlClient,
  type VariationInsert,
} from '@/src/repo';

import {
  setupStudioFixture,
  teardownStudioFixture,
  type StudioFixture,
} from './support/fixtures';

const longBase = 'a'.repeat(200);

let fixture: StudioFixture;

beforeAll(async () => {
  fixture = await setupStudioFixture();
});

afterAll(async () => {
  await teardownStudioFixture(fixture);
  await prisma.$disconnect();
});

function queryCounter(): { db: SqlClient; queries: () => number } {
  let count = 0;
  const db: SqlClient = {
    $queryRaw: ((query: TemplateStringsArray, ...values: unknown[]) => {
      count += 1;
      return prisma.$queryRaw(query, ...values);
    }) as SqlClient['$queryRaw'],
    $executeRaw: prisma.$executeRaw.bind(prisma),
  };
  return { db, queries: () => count };
}

function variation(slug: string, batchId: string, batchPosition: number): VariationInsert {
  return {
    sourceMode: 'custom',
    sourceRef: '',
    hook: 'hook',
    headline: 'headline',
    description: 'description',
    cta: 'LEARN_MORE',
    instruction: '',
    modelName: 'fixture',
    promptVersion: 'fixture',
    batchId,
    batchPosition,
    status: 'draft',
    createdById: BigInt(fixture.memberUserId),
    creativeId: null,
    projectId: fixture.projectA,
    slug,
  };
}

async function plantSlug(slug: string): Promise<void> {
  await prisma.$executeRaw`
    INSERT INTO ${tenantTable(fixture.schema, VARIATION_TABLE)} (
      created_at, updated_at, is_deleted, source_mode, source_ref,
      hook, headline, description, cta, instruction, model_name, prompt_version,
      batch_id, batch_position, status, created_by_id, project_id, slug
    ) VALUES (
      now(), now(), false, 'custom', '',
      'hook', 'headline', 'description', 'LEARN_MORE', '', 'fixture', 'fixture',
      ${randomUUID()}::uuid, 0, 'draft', ${BigInt(fixture.memberUserId)},
      ${fixture.projectA}, ${slug}
    )`;
}

async function countBatch(batchId: string): Promise<number> {
  const rows = await prisma.$queryRaw<{ count: bigint }[]>`
    SELECT COUNT(*)::bigint AS count
    FROM ${tenantTable(fixture.schema, VARIATION_TABLE)}
    WHERE batch_id = ${batchId}::uuid`;
  return Number(rows[0]?.count ?? 0);
}

describe('insertVariations', () => {
  it('saves 50 identical bases with unique slugs in two queries', async () => {
    const base = `same-headline-${randomUUID().slice(0, 8)}`;
    const batchId = randomUUID();
    const counter = queryCounter();

    const saved = await insertVariations(
      fixture.schema,
      Array.from({ length: 50 }, (_, index) => variation(base, batchId, index)),
      counter.db
    );

    expect(saved.map((row) => row.slug)).toEqual(
      Array.from({ length: 50 }, (_, index) => slugWithSuffix(base, index))
    );
    expect(counter.queries()).toBe(2);
  });

  it('sees a trimmed -1 and assigns -2 in two queries', async () => {
    await plantSlug(longBase);
    await plantSlug(slugWithSuffix(longBase, 1));
    const counter = queryCounter();

    const saved = await insertVariations(
      fixture.schema,
      [variation(longBase, randomUUID(), 0)],
      counter.db
    );

    expect(saved.map((row) => row.slug)).toEqual([slugWithSuffix(longBase, 2)]);
    expect(counter.queries()).toBe(2);
  });

  it('rolls back the batch when the transaction throws', async () => {
    const batchId = randomUUID();
    const rows = [0, 1, 2].map((index) =>
      variation(`rollback-${batchId.slice(0, 8)}`, batchId, index)
    );

    await expect(
      prisma.$transaction(async (tx) => {
        await insertVariations(fixture.schema, rows, tx);
        throw new Error('boom');
      })
    ).rejects.toThrow('boom');

    expect(await countBatch(batchId)).toBe(0);
  });
});
