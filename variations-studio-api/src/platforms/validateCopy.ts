import type { PlatformSpec } from './types';

const violationUnits = { max_words: 'words', max_chars: 'characters' } as const;

export type CopyViolation = {
  field: string;
  rule: keyof typeof violationUnits;
  actual: number;
  limit: number;
};

export function formatCopyViolation({ field, rule, actual, limit }: CopyViolation): string {
  return `${field}: ${actual} ${violationUnits[rule]} (maximum ${limit})`;
}

/** Count whitespace-delimited words and Unicode code points in the supplied fields. */
export function validateCopy(
  spec: PlatformSpec,
  copy: Readonly<Record<string, string | undefined>>
): CopyViolation[] {
  const violations: CopyViolation[] = [];
  for (const field of spec.fields) {
    const value = copy[field.key];
    if (value === undefined) continue;
    if (field.type !== 'string' || field.limits.characterCounting === 'double-width') {
      throw new Error(`Unsupported copy validation rules for ${spec.id}.${field.key}`);
    }

    const { maxWords, maxChars } = field.limits;
    if (maxWords !== undefined) {
      const actual = value.match(/\S+/g)?.length ?? 0;
      if (actual > maxWords) {
        violations.push({ field: field.key, rule: 'max_words', actual, limit: maxWords });
      }
    }
    if (maxChars !== undefined) {
      const actual = Array.from(value).length;
      if (actual > maxChars) {
        violations.push({ field: field.key, rule: 'max_chars', actual, limit: maxChars });
      }
    }
  }
  return violations;
}
