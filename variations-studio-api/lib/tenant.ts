import { Prisma } from '@prisma/client';

import {
  isAuthFailure,
  requireAccessUser,
  type AccessUser,
  type AuthFailure,
} from '@/lib/auth';
import { prisma } from '@/lib/prisma';

export type StudioContext = AccessUser & { schema: string };

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

function assertSchemaName(schema: string): string {
  if (!SCHEMA_NAME.test(schema)) {
    throw new Error('Invalid tenant schema name');
  }
  return schema;
}

/**
 * Prisma bakes the datasource schema into generated SQL and ignores
 * search_path, so per-request tenant tables cannot go through model queries.
 * Tenant-scoped tables (see backend/core/tenant_config.py) must be reached
 * with raw SQL qualified by this helper. Everything still declared in
 * schema.prisma lives in `public`. AdCopyVariation, core_project and
 * core_projectmember are tenant-scoped.
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
 * Match Django TenantSchemaMiddleware._resolve_schema, without the Fernet
 * X-Organization-Token path. Studio always has a user JWT; Django prefers
 * user.current_organization over that header anyway.
 */
async function schemaForUser(
  request: Request,
  user: UserOrgRow
): Promise<string> {
  const orgId = user.currentOrganizationId ?? user.organizationId;
  if (orgId) {
    const slug = await slugForOrgId(orgId);
    if (slug) return assertSchemaName(slugToSchemaName(slug));
  }

  const headerSlug = request.headers.get('x-organization-slug');
  if (headerSlug && (await isActiveOrgSlug(headerSlug))) {
    return assertSchemaName(slugToSchemaName(headerSlug));
  }

  return 'public';
}

export async function requireStudioContext(
  request: Request
): Promise<StudioContext | AuthFailure> {
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

  return {
    userId: auth.userId,
    schema: await schemaForUser(request, user),
  };
}
