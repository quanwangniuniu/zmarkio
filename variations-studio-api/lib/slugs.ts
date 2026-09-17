import { randomUUID } from 'crypto';

import { prisma } from '@/lib/prisma';
import { listSlugs, type SqlClient } from '@/lib/variationStore';

function slugify(value: string): string {
  return value
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, '')
    .trim()
    .replace(/[\s-]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

export function makeSlug(sourceValue: string, used: Set<string>): string {
  let base = slugify(sourceValue || '');
  if (!base) {
    base = `adcopyvariation-${randomUUID().slice(0, 8)}`;
  } else if (/^\d+$/.test(base)) {
    base = `adcopyvariation-${base}`;
  }
  base = base.slice(0, 200);

  let slug = base;
  let counter = 1;
  while (used.has(slug)) {
    slug = `${base}-${counter}`;
    counter += 1;
  }
  used.add(slug);
  return slug;
}

export async function allocateSlugs(
  schema: string,
  headlines: string[],
  db: SqlClient = prisma
): Promise<string[]> {
  const used = new Set(await listSlugs(schema, db));
  return headlines.map((headline) => makeSlug(headline, used));
}
