import { randomUUID } from 'crypto';

import { prisma } from '@/lib/prisma';
import { slugToSchemaName, tenantTable } from '@/lib/tenant';
import {
  countVariations,
  deleteVariationsForProjects,
  insertVariation,
  VARIATION_TABLE,
} from '@/lib/variationStore';

/**
 * Dual-org fixtures for tenant routing / header / cross-tenant write tests.
 * Requires at least two active orgs with provisioned tenant schemas.
 */

export type TenantRoutingFixture = {
  orgA: { id: bigint; slug: string; schema: string };
  orgB: { id: bigint; slug: string; schema: string };
  /** is_active=false; used only for header rejection/fallback cases. */
  inactiveOrgSlug: string;
  inactiveOrgId: bigint;
  /** current_organization = A (header must not override). */
  pinnedToAUserId: number;
  /**
   * Belongs to both orgs via organization_id=B and current_organization_id=A
   * initially — selection must follow current_organization, not at random.
   */
  multiOrgUserId: number;
  /** Both org FKs null — only X-Organization-Slug can select a tenant. */
  headerOnlyUserId: number;
  projectA: bigint;
  projectB: bigint;
  draftASlug: string;
  draftAId: bigint;
  draftBSlug: string;
  draftBId: bigint;
};

async function schemaReady(schema: string): Promise<boolean> {
  const ns = await prisma.$queryRaw<{ present: boolean }[]>`
    SELECT EXISTS (
      SELECT 1 FROM pg_namespace WHERE nspname = ${schema}
    ) AS present`;
  if (!ns[0]?.present) return false;
  const table = await prisma.$queryRaw<{ present: boolean }[]>`
    SELECT EXISTS (
      SELECT 1 FROM information_schema.tables
      WHERE table_schema = ${schema}
        AND table_name = ${VARIATION_TABLE}
    ) AS present`;
  return table[0]?.present ?? false;
}

async function listProvisionedOrgs(): Promise<
  Array<{ id: bigint; slug: string; schema: string }>
> {
  const orgs = await prisma.$queryRaw<{ id: bigint; slug: string }[]>`
    SELECT id, slug FROM public.core_organization
    WHERE is_active = true
    ORDER BY id`;
  const ready: Array<{ id: bigint; slug: string; schema: string }> = [];
  for (const org of orgs) {
    const schema = slugToSchemaName(org.slug);
    if (await schemaReady(schema)) {
      ready.push({ ...org, schema });
    }
  }
  return ready;
}

async function createUser(args: {
  currentOrganizationId: bigint | null;
  organizationId: bigint | null;
}): Promise<number> {
  const email = `studio-routing-${randomUUID()}@example.test`;
  const rows = await prisma.$queryRaw<{ id: bigint }[]>`
    INSERT INTO public.core_customuser (
      password, is_superuser, username, first_name, last_name,
      is_staff, is_active, date_joined, is_verified, email,
      google_registered, password_set, job, department, location,
      organization_id, current_organization_id, auth_token_version
    ) VALUES (
      '!', false, ${email}, 'Studio', 'Routing',
      false, true, now(), true, ${email},
      false, false, '', '', '',
      ${args.organizationId}, ${args.currentOrganizationId}, 0
    )
    RETURNING id`;
  return Number(rows[0].id);
}

async function setUserOrgs(
  userId: number,
  args: { currentOrganizationId: bigint | null; organizationId: bigint | null }
): Promise<void> {
  await prisma.$executeRaw`
    UPDATE public.core_customuser
    SET current_organization_id = ${args.currentOrganizationId},
        organization_id = ${args.organizationId}
    WHERE id = ${BigInt(userId)}`;
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
  const slug = `studio-routing-${args.label}-${randomUUID().slice(0, 8)}`;
  for (const schema of ['public', args.schema]) {
    await prisma.$executeRaw`
      INSERT INTO ${tenantTable(schema, 'core_project')} (
        id, created_at, updated_at, is_deleted, name, description,
        project_type, work_model, advertising_platforms, objectives, kpis,
        pacing_enabled, ai_analysis_enabled, budget_config, audience_targeting,
        organization_id, owner_id, slug
      ) VALUES (
        ${id}, now(), now(), false, ${`Routing ${args.label}`}, '',
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

async function ensureInactiveOrg(): Promise<{ id: bigint; slug: string }> {
  const slug = `studio-routing-inactive-${randomUUID().slice(0, 8)}`;
  const name = `Studio Routing Inactive ${slug}`;
  const rows = await prisma.$queryRaw<{ id: bigint }[]>`
    INSERT INTO public.core_organization (
      name, slug, is_active, is_deleted, is_parent, created_at, updated_at
    ) VALUES (
      ${name}, ${slug}, false, false, false, now(), now()
    )
    RETURNING id`;
  return { id: rows[0].id, slug };
}

export async function setupTenantRoutingFixture(): Promise<TenantRoutingFixture> {
  const orgs = await listProvisionedOrgs();
  if (orgs.length < 2) {
    throw new Error(
      'Tenant routing tests need at least two active orgs with provisioned ' +
        `tenant schemas including ${VARIATION_TABLE}. CI provisions ` +
        'ci-studio-org and ci-studio-org-b; locally run migrate_all_tenants ' +
        'for two orgs.'
    );
  }
  const [orgA, orgB] = orgs;

  const pinnedToAUserId = await createUser({
    currentOrganizationId: orgA.id,
    organizationId: orgA.id,
  });
  const multiOrgUserId = await createUser({
    currentOrganizationId: orgA.id,
    organizationId: orgB.id,
  });
  const headerOnlyUserId = await createUser({
    currentOrganizationId: null,
    organizationId: null,
  });

  const projectA = await createProject({
    schema: orgA.schema,
    organizationId: orgA.id,
    ownerId: pinnedToAUserId,
    label: 'a',
  });
  const projectB = await createProject({
    schema: orgB.schema,
    organizationId: orgB.id,
    ownerId: pinnedToAUserId,
    label: 'b',
  });

  await addMember(orgA.schema, projectA, pinnedToAUserId);
  await addMember(orgA.schema, projectA, multiOrgUserId);
  await addMember(orgB.schema, projectB, multiOrgUserId);
  // pinnedToAUserId is intentionally NOT a member of projectB.

  const draftA = await insertVariation(orgA.schema, {
    sourceMode: 'custom',
    sourceRef: '',
    hook: 'A hook',
    headline: 'A headline',
    description: 'A description',
    cta: 'LEARN_MORE',
    instruction: '',
    modelName: 'fixture',
    promptVersion: 'fixture',
    batchId: randomUUID(),
    batchPosition: 0,
    status: 'draft',
    createdById: BigInt(pinnedToAUserId),
    creativeId: null,
    projectId: projectA,
    slug: `routing-a-${randomUUID()}`,
  });
  const draftB = await insertVariation(orgB.schema, {
    sourceMode: 'custom',
    sourceRef: '',
    hook: 'B hook',
    headline: 'B headline',
    description: 'B description',
    cta: 'LEARN_MORE',
    instruction: '',
    modelName: 'fixture',
    promptVersion: 'fixture',
    batchId: randomUUID(),
    batchPosition: 0,
    status: 'draft',
    createdById: BigInt(multiOrgUserId),
    creativeId: null,
    projectId: projectB,
    slug: `routing-b-${randomUUID()}`,
  });

  const inactive = await ensureInactiveOrg();

  return {
    orgA,
    orgB,
    inactiveOrgSlug: inactive.slug,
    inactiveOrgId: inactive.id,
    pinnedToAUserId,
    multiOrgUserId,
    headerOnlyUserId,
    projectA,
    projectB,
    draftASlug: draftA.slug,
    draftAId: draftA.id,
    draftBSlug: draftB.slug,
    draftBId: draftB.id,
  };
}

export async function setMultiOrgCurrent(
  fixture: TenantRoutingFixture,
  which: 'A' | 'B'
): Promise<void> {
  const current = which === 'A' ? fixture.orgA.id : fixture.orgB.id;
  await setUserOrgs(fixture.multiOrgUserId, {
    currentOrganizationId: current,
    organizationId: fixture.orgB.id,
  });
}

export async function countInSchema(
  schema: string,
  projectId: bigint
): Promise<number> {
  return countVariations(schema, { projectId });
}

export async function teardownTenantRoutingFixture(
  fixture: TenantRoutingFixture
): Promise<void> {
  await deleteVariationsForProjects(fixture.orgA.schema, [fixture.projectA]);
  await deleteVariationsForProjects(fixture.orgB.schema, [fixture.projectB]);

  for (const [schema, projectId] of [
    [fixture.orgA.schema, fixture.projectA],
    [fixture.orgB.schema, fixture.projectB],
  ] as const) {
    await prisma.$executeRaw`
      DELETE FROM ${tenantTable(schema, 'core_projectmember')}
      WHERE project_id = ${projectId}`;
    for (const s of [schema, 'public']) {
      await prisma.$executeRaw`
        DELETE FROM ${tenantTable(s, 'core_project')} WHERE id = ${projectId}`;
    }
  }

  for (const userId of [
    fixture.pinnedToAUserId,
    fixture.multiOrgUserId,
    fixture.headerOnlyUserId,
  ]) {
    await prisma.$executeRaw`
      DELETE FROM public.core_customuser WHERE id = ${BigInt(userId)}`;
  }

  await prisma.$executeRaw`
    DELETE FROM public.core_organization WHERE id = ${fixture.inactiveOrgId}`;
}
