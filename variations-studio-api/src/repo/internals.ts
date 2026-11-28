import { Prisma } from '@prisma/client';

import { prisma } from '@/lib/prisma';
import { tenantTable } from '@/lib/tenant';
import { LEGACY_COPY_PLATFORM_ID, VARIATION_TABLE } from '@/lib/variationSchema';

export { VARIATION_TABLE };

export type SqlClient = {
  $queryRaw: typeof prisma.$queryRaw;
  $executeRaw: typeof prisma.$executeRaw;
};

export { prisma };

export type VariationRow = {
  id: bigint;
  createdAt: Date;
  updatedAt: Date;
  isDeleted: boolean;
  sourceMode: string;
  sourceRef: string;
  platform: string;
  fields: Record<string, string | string[]>;
  hook: string;
  headline: string;
  description: string;
  cta: string;
  instruction: string;
  modelName: string;
  promptVersion: string;
  batchId: string | null;
  batchPosition: number | null;
  status: string;
  createdById: bigint | null;
  creativeId: bigint | null;
  projectId: bigint | null;
  slug: string;
};

export type VariationInsert = {
  sourceMode: string;
  sourceRef: string;
  platform: string;
  fields: Record<string, string>;
  instruction: string;
  modelName: string;
  promptVersion: string;
  batchId: string | null;
  batchPosition: number | null;
  status: string;
  createdById: bigint | null;
  creativeId: bigint | null;
  projectId: bigint | null;
  slug: string;
};

export const META_COLUMN_FIELDS = Prisma.sql`jsonb_build_object(
  'hook', hook, 'headline', headline, 'description', description, 'cta', cta)`;

export const COLUMNS = Prisma.sql`
  id,
  created_at AS "createdAt",
  updated_at AS "updatedAt",
  is_deleted AS "isDeleted",
  source_mode AS "sourceMode",
  source_ref AS "sourceRef",
  platform,
  CASE WHEN platform = ${LEGACY_COPY_PLATFORM_ID} THEN ${META_COLUMN_FIELDS}
       ELSE COALESCE(fields, '{}'::jsonb) END AS fields,
  hook,
  headline,
  description,
  cta,
  instruction,
  model_name AS "modelName",
  prompt_version AS "promptVersion",
  batch_id AS "batchId",
  batch_position AS "batchPosition",
  status,
  created_by_id AS "createdById",
  creative_id AS "creativeId",
  project_id AS "projectId",
  slug
`;

export function table(schema: string): Prisma.Sql {
  return tenantTable(schema, VARIATION_TABLE);
}

export function idList(ids: bigint[]): Prisma.Sql {
  return Prisma.join(ids);
}

export { Prisma };
