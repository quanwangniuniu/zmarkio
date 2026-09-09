import { randomUUID } from 'crypto';

import { prisma } from '@/lib/prisma';
import { slugToSchemaName, tenantTable } from '@/lib/tenant';
import {
  deleteVariationsForProjects,
  insertVariation,
  VARIATION_TABLE,
} from '@/lib/variationStore';

/**
 * Fixtures for the shared dev/CI Postgres. Every row created here hangs off a
 * project this module just created, so teardown can never touch seed data.
 *
 * Projects are written to both `public` and the org schema: meta_ad_accounts
 * still lives in public and FKs to public.core_project. Variations themselves
 * are tenant-scoped and are written to the org schema only.
 * Memberships are written to the org schema ONLY — that is what makes these
 * fixtures a regression test for reading membership out of the wrong schema.
 */

export type TestVariation = { id: bigint; slug: string };

export type StudioFixture = {
  schema: string;
  organizationId: bigint;
  /** Active user, member of projectA only. */
  memberUserId: number;
  /** Member of projectA but is_active = false. */
  inactiveUserId: number;
  projectA: bigint;
  projectB: bigint;
  draftA: TestVariation;
  reviewedA: TestVariation;
  draftB: TestVariation;
  connectionId: bigint;
  accountA: bigint;
  accountB: bigint;
  /** Creative reachable from projectA. */
  creativeA: bigint;
  /** Creative owned by projectB, for cross-project checks. */
  creativeB: bigint;
};

async function schemaExists(schema: string): Promise<boolean> {
  const rows = await prisma.$queryRaw<{ present: boolean }[]>`
    SELECT EXISTS (
      SELECT 1 FROM pg_namespace WHERE nspname = ${schema}
    ) AS present`;
  return rows[0]?.present ?? false;
}

async function tenantVariationTableExists(schema: string): Promise<boolean> {
  const rows = await prisma.$queryRaw<{ present: boolean }[]>`
    SELECT EXISTS (
      SELECT 1 FROM information_schema.tables
      WHERE table_schema = ${schema}
        AND table_name = ${VARIATION_TABLE}
    ) AS present`;
  return rows[0]?.present ?? false;
}

/**
 * Pick the first active org whose tenant schema has actually been provisioned.
 * A freshly migrated database can hold orgs with no schema yet, and those would
 * fail every membership lookup for reasons unrelated to the code under test.
 */
async function provisionedOrganization(): Promise<{
  id: bigint;
  slug: string;
  schema: string;
}> {
  const orgs = await prisma.$queryRaw<{ id: bigint; slug: string }[]>`
    SELECT id, slug FROM public.core_organization
    WHERE is_active = true
    ORDER BY id`;

  for (const org of orgs) {
    const schema = slugToSchemaName(org.slug);
    if (await schemaExists(schema) && (await tenantVariationTableExists(schema))) {
      return { ...org, schema };
    }
  }

  throw new Error(
    'No active organization has a provisioned tenant schema that includes ' +
      `${VARIATION_TABLE}. Run manage.py migrate_all_tenants against this database first.`
  );
}

async function createUser(organizationId: bigint, isActive: boolean): Promise<number> {
  const email = `studio-test-${randomUUID()}@example.test`;
  // '!' is Django's unusable-password marker, so this account can never log in.
  const rows = await prisma.$queryRaw<{ id: bigint }[]>`
    INSERT INTO public.core_customuser (
      password, is_superuser, username, first_name, last_name,
      is_staff, is_active, date_joined, is_verified, email,
      google_registered, password_set, job, department, location,
      organization_id, current_organization_id, auth_token_version
    ) VALUES (
      '!', false, ${email}, 'Studio', 'Test',
      false, ${isActive}, now(), true, ${email},
      false, false, '', '', '',
      ${organizationId}, ${organizationId}, 0
    )
    RETURNING id`;
  return Number(rows[0].id);
}

async function nextProjectId(schema: string): Promise<bigint> {
  const rows = await prisma.$queryRaw<{ id: bigint }[]>`
    SELECT GREATEST(
      COALESCE((SELECT MAX(id) FROM ${tenantTable('public', 'core_project')}), 0),
      COALESCE((SELECT MAX(id) FROM ${tenantTable(schema, 'core_project')}), 0)
    ) + 1 AS id`;
  return rows[0].id;
}

async function createProject(args: {
  schema: string;
  organizationId: bigint;
  ownerId: number;
  label: string;
}): Promise<bigint> {
  const id = await nextProjectId(args.schema);
  const slug = `studio-test-${args.label}-${randomUUID().slice(0, 8)}`;

  for (const schema of ['public', args.schema]) {
    await prisma.$executeRaw`
      INSERT INTO ${tenantTable(schema, 'core_project')} (
        id, created_at, updated_at, is_deleted, name, description,
        project_type, work_model, advertising_platforms, objectives, kpis,
        pacing_enabled, ai_analysis_enabled, budget_config, audience_targeting,
        organization_id, owner_id, slug
      ) VALUES (
        ${id}, now(), now(), false, ${`Studio test ${args.label}`}, '',
        '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '{}'::jsonb,
        false, true, '{}'::jsonb, '{}'::jsonb,
        ${args.organizationId}, ${BigInt(args.ownerId)}, ${slug}
      )`;
  }

  return id;
}

async function addMember(
  schema: string,
  projectId: bigint,
  userId: number
): Promise<void> {
  await prisma.$executeRaw`
    INSERT INTO ${tenantTable(schema, 'core_projectmember')} (
      created_at, updated_at, is_deleted, role, is_active, project_id, user_id
    ) VALUES (
      now(), now(), false, 'member', true, ${projectId}, ${BigInt(userId)}
    )`;
}

async function nextPublicId(table: string): Promise<bigint> {
  const rows = await prisma.$queryRaw<{ id: bigint }[]>`
    SELECT COALESCE(MAX(id), 0) + 1 AS id FROM ${tenantTable('public', table)}`;
  return rows[0].id;
}

async function createFacebookConnection(userId: number): Promise<bigint> {
  const id = await nextPublicId('facebook_connections');
  // encrypted_access_token is NOT NULL; an empty string keeps a credential-shaped
  // value out of the repo and out of the test database.
  await prisma.$executeRaw`
    INSERT INTO public.facebook_connections (
      id, fb_user_id, fb_user_name, business_id, business_name,
      encrypted_access_token, is_active, last_sync_error,
      created_at, updated_at, user_id
    ) VALUES (
      ${id}, 'studio-test', 'Studio Test', 'studio-test', 'Studio Test',
      '', true, '',
      now(), now(), ${BigInt(userId)}
    )`;
  return id;
}

async function createAdAccount(
  connectionId: bigint,
  projectId: bigint
): Promise<bigint> {
  const id = await nextPublicId('meta_ad_accounts');
  await prisma.$executeRaw`
    INSERT INTO public.meta_ad_accounts (
      id, meta_account_id, name, currency, timezone_name, business_id,
      is_owned, created_at, updated_at, connection_id, project_id
    ) VALUES (
      ${id}, ${`act_studio_test_${id}`}, 'Studio test account', 'USD', 'UTC',
      'studio-test', true, now(), now(), ${connectionId}, ${projectId}
    )`;
  return id;
}

async function createCreative(adAccountId: bigint): Promise<bigint> {
  const id = await nextPublicId('meta_ad_creatives');
  await prisma.$executeRaw`
    INSERT INTO public.meta_ad_creatives (
      id, created_at, updated_at, is_deleted, meta_creative_id, name,
      title, body, image_url, video_id, thumbnail_url, object_type,
      call_to_action_type, asset_feed_spec, ad_account_id, slug
    ) VALUES (
      ${id}, now(), now(), false, ${`studio-test-${id}`}, 'Studio test creative',
      'Fixture creative title', 'Fixture creative body', '', '', '', 'SHARE',
      'LEARN_MORE', '{}'::jsonb, ${adAccountId}, ${`studio-test-creative-${randomUUID().slice(0, 8)}`}
    )`;
  return id;
}

export async function createTestVariation(args: {
  schema: string;
  projectId: bigint;
  userId: number;
  status: string;
  batchId?: string;
  sourceMode?: string;
  creativeId?: bigint;
}): Promise<TestVariation> {
  const row = await insertVariation(args.schema, {
    sourceMode: args.sourceMode ?? 'custom',
    sourceRef: '',
    hook: 'Fixture hook',
    headline: 'Fixture headline',
    description: 'Fixture description',
    cta: 'LEARN_MORE',
    instruction: '',
    modelName: 'fixture',
    promptVersion: 'fixture',
    batchId: args.batchId ?? randomUUID(),
    batchPosition: 0,
    status: args.status,
    createdById: BigInt(args.userId),
    creativeId: args.creativeId ?? null,
    projectId: args.projectId,
    slug: `studio-test-${randomUUID()}`,
  });
  return { id: row.id, slug: row.slug };
}

export async function setupStudioFixture(): Promise<StudioFixture> {
  const organization = await provisionedOrganization();
  const { schema } = organization;

  const memberUserId = await createUser(organization.id, true);
  const inactiveUserId = await createUser(organization.id, false);

  const projectA = await createProject({
    schema,
    organizationId: organization.id,
    ownerId: memberUserId,
    label: 'a',
  });
  const projectB = await createProject({
    schema,
    organizationId: organization.id,
    ownerId: memberUserId,
    label: 'b',
  });

  await addMember(schema, projectA, memberUserId);
  await addMember(schema, projectA, inactiveUserId);
  // Nobody is a member of projectB, which is what makes it the "other project".

  const connectionId = await createFacebookConnection(memberUserId);
  const accountA = await createAdAccount(connectionId, projectA);
  const accountB = await createAdAccount(connectionId, projectB);

  return {
    schema,
    organizationId: organization.id,
    memberUserId,
    inactiveUserId,
    projectA,
    projectB,
    connectionId,
    accountA,
    accountB,
    creativeA: await createCreative(accountA),
    creativeB: await createCreative(accountB),
    draftA: await createTestVariation({
      schema,
      projectId: projectA,
      userId: memberUserId,
      status: 'draft',
    }),
    reviewedA: await createTestVariation({
      schema,
      projectId: projectA,
      userId: memberUserId,
      status: 'reviewed',
    }),
    draftB: await createTestVariation({
      schema,
      projectId: projectB,
      userId: memberUserId,
      status: 'draft',
    }),
  };
}

export async function teardownStudioFixture(fixture: StudioFixture): Promise<void> {
  const projectIds = [fixture.projectA, fixture.projectB];

  await deleteVariationsForProjects(fixture.schema, projectIds);

  for (const creativeId of [fixture.creativeA, fixture.creativeB]) {
    await prisma.$executeRaw`
      DELETE FROM public.meta_ad_creatives WHERE id = ${creativeId}`;
  }
  for (const accountId of [fixture.accountA, fixture.accountB]) {
    await prisma.$executeRaw`
      DELETE FROM public.meta_ad_accounts WHERE id = ${accountId}`;
  }
  await prisma.$executeRaw`
    DELETE FROM public.facebook_connections WHERE id = ${fixture.connectionId}`;

  for (const projectId of projectIds) {
    await prisma.$executeRaw`
      DELETE FROM ${tenantTable(fixture.schema, 'core_projectmember')}
      WHERE project_id = ${projectId}`;
    for (const schema of [fixture.schema, 'public']) {
      await prisma.$executeRaw`
        DELETE FROM ${tenantTable(schema, 'core_project')} WHERE id = ${projectId}`;
    }
  }

  for (const userId of [fixture.memberUserId, fixture.inactiveUserId]) {
    await prisma.$executeRaw`
      DELETE FROM public.core_customuser WHERE id = ${BigInt(userId)}`;
  }
}
