import { randomUUID } from 'crypto';

import type { PlatformSpec } from '@/src/platforms';

/** Current create/generate callers use scalar copy fields. */
export function readSlugSource(copy: Record<string, string>, spec: PlatformSpec): string {
  const field = spec.slugSource.field;
  if (!Object.prototype.hasOwnProperty.call(copy, field)) {
    throw new Error(`Copy is missing field "${field}"`);
  }
  return copy[field];
}

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

export function makeSlug(sourceValue: string): string {
  let base = slugify(sourceValue || '');

  if (!base) {
    base = `adcopyvariation-${randomUUID().slice(0, 8)}`;
  } else if (/^\d+$/.test(base)) {
    base = `adcopyvariation-${base}`;
  }

  return base.slice(0, 200);
}

export function allocateSlugs(headlines: string[]): string[] {
  return headlines.map((headline) => makeSlug(headline));
}
