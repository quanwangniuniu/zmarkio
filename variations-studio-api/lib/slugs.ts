import { randomUUID } from 'crypto';

import type { PlatformSpec } from '@/src/platforms';

const SLUG_MAX_LENGTH = 200;
// "-" plus 9 digits is 10 characters. A base this long still fits after that
// suffix, so the stored value is always `base` or `base-<n>`.
const SHORT_SLUG_MAX_LENGTH = SLUG_MAX_LENGTH - 10;

/** Ignores slugSource.index: copy fields are scalars. */
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

  return base.slice(0, SLUG_MAX_LENGTH);
}

export function allocateSlugs(headlines: string[]): string[] {
  return headlines.map((headline) => makeSlug(headline));
}

export function slugWithSuffix(base: string, counter: number): string {
  if (counter === 0) return base;
  const suffix = `-${counter}`;
  return `${base.slice(0, SLUG_MAX_LENGTH - suffix.length)}${suffix}`;
}

/** Assigns a free slug per base and adds each choice to `used`. */
export function assignUniqueSlugs(bases: string[], used: Set<string>): string[] {
  return bases.map((base) => {
    let counter = 0;
    let slug = slugWithSuffix(base, counter);
    while (used.has(slug)) {
      counter += 1;
      slug = slugWithSuffix(base, counter);
    }
    used.add(slug);
    return slug;
  });
}

function escapeLike(value: string): string {
  return value.replace(/[\\%_]/g, (char) => `\\${char}`);
}

export type SlugLookup =
  | { kind: 'short'; base: string; pattern: string }
  | { kind: 'long'; pattern: string };

/**
 * How to find slugs that already occupy this base.
 * Short bases keep their tail, so match `base` and `base-<n>`.
 * Longer bases are trimmed before the suffix, so match the first 190 characters.
 */
export function slugLookup(base: string): SlugLookup {
  if (base.length <= SHORT_SLUG_MAX_LENGTH) {
    return { kind: 'short', base, pattern: `${escapeLike(base)}-%` };
  }
  const prefix = escapeLike(base.slice(0, SHORT_SLUG_MAX_LENGTH));
  return { kind: 'long', pattern: `${prefix}%` };
}
