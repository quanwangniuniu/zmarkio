import { Prisma } from '@prisma/client';

import {
  isAuthFailure,
  requireAccessUser,
  type AccessUser,
  type AuthFailure,
  type StudioContextFailure,
} from '@/lib/auth';
import { prisma } from '@/lib/prisma';

export type StudioContext = AccessUser & { schema: string };

export const REQUIRED_STUDIO_MIGRATION = '003';

const SCHEMA_NAME = /^(public|org_[A-Za-z0-9_]+)$/;

type UserOrgRow = {
  isActive: boolean;
  currentOrganizationId: bigint | null;
  organizationId: bigint | null;
};

/** Same rules as backend/core/services/tenant.py slug_to_schema_name. */
export function slugToSchemaName(slug: string): string {
  const safe = slug.replace(/[^a-zA-Z0-9]/g, '_');
  return `org_${safe}`;
}

export function assertSchemaName(schema: string): string {
  if (!SCHEMA_NAME.test(schema)) {
    throw new Error('Invalid tenant schema name');
  }
  return schema;
}

/**
 * Prisma bakes the datasource schema into generated SQL and ignores
 * search_path, so per-request tenant tables cannot go through model queries.
 * Tenant-scoped tables must be reached with raw SQL qualified by this helper.
 * Everything still declared in schema.prisma lives in `public`. AdCopyVariation,
 * core_project and core_projectmember are tenant-scoped.
 */
export function tenantTable(schema: string, table: string): Prisma.Sql {
  return Prisma.raw(`"${assertSchemaName(schema)}"."${table}"`);
}

async function slugForOrgId(orgId: bigint): Promise<string | null> {
  const org = await prisma.organization.findFirst({
    where: { id: orgId },
    select: { slug: true },
  });
  return org?.slug ?? null;
}

async function isActiveOrgSlug(slug: string): Promise<boolean> {
  const org = await prisma.organization.findFirst({
    where: { slug, isActive: true },
    select: { id: true },
  });
  return Boolean(org);
}

/**
 * Preserve Django's organization precedence, but require an organization
 * instead of falling back to public. Studio uses a user JWT, not Fernet.
 */
async function schemaForUser(
  request: Request,
  user: UserOrgRow
): Promise<string | null> {
  const orgId = user.currentOrganizationId ?? user.organizationId;
  if (orgId) {
    const slug = await slugForOrgId(orgId);
    if (slug) return assertSchemaName(slugToSchemaName(slug));
  }

  const headerSlug = request.headers.get('x-organization-slug');
  if (headerSlug && (await isActiveOrgSlug(headerSlug))) {
    return assertSchemaName(slugToSchemaName(headerSlug));
  }

  return null;
}

/** Check committed tenant state before any variation query or generation. */
export async function isStudioReady(schema: string): Promise<boolean> {
  const namespace = `"${assertSchemaName(schema)}"`;
  const relations = await prisma.$queryRaw<{ present: boolean }[]>`
    SELECT to_regclass(${`${namespace}.ad_copy_variation_adcopyvariation`}) IS NOT NULL
       AND to_regclass(${`${namespace}.studio_migrations`}) IS NOT NULL AS present`;
  if (!relations[0]?.present) return false;

  const rows = await prisma.$queryRaw<{ ready: boolean }[]>`
    SELECT EXISTS (
      SELECT 1 FROM ${tenantTable(schema, 'studio_migrations')}
      WHERE version = ${REQUIRED_STUDIO_MIGRATION}
    ) AS ready`;
  return rows[0]?.ready ?? false;
}

export async function requireStudioContext(
  request: Request
): Promise<StudioContext | AuthFailure | StudioContextFailure> {
  const auth = await requireAccessUser(request);
  if (isAuthFailure(auth)) return auth;

  const user = await prisma.customUser.findFirst({
    where: { id: BigInt(auth.userId) },
    select: {
      isActive: true,
      currentOrganizationId: true,
      organizationId: true,
    },
  });
  if (!user?.isActive) {
    return { error: 'Invalid or expired token.', status: 401 };
  }

  const schema = await schemaForUser(request, user);
  if (!schema) {
    return { error: 'An organization is required to use Studio.', status: 403 };
  }
  if (!(await isStudioReady(schema))) {
    return { error: 'Studio is being initialized or upgraded. Please try again shortly.', status: 503 };
  }
  return { userId: auth.userId, schema };
}
