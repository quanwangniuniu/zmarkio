import { randomUUID } from 'crypto';

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
