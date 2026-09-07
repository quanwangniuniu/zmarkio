import { Prisma } from '@prisma/client';

import { prisma } from '@/lib/prisma';
import { tenantTable } from '@/lib/tenant';

export const VARIATION_TABLE = 'ad_copy_variation_adcopyvariation';

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

export const COLUMNS = Prisma.raw(`
  id,
  created_at AS "createdAt",
  updated_at AS "updatedAt",
  is_deleted AS "isDeleted",
  source_mode AS "sourceMode",
  source_ref AS "sourceRef",
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
`);

export function table(schema: string): Prisma.Sql {
  return tenantTable(schema, VARIATION_TABLE);
}

export function idList(ids: bigint[]): Prisma.Sql {
  return Prisma.join(ids);
}

export { Prisma };
