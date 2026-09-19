import type { CopyJson } from './types';

export type CopyField = 'hook' | 'headline' | 'description';
export type CopyViolationRule = 'max_chars' | 'max_words';

export type CopyLimit = {
  maxChars?: number;
  maxWords?: number;
};

export type CopyLimits = Partial<Record<CopyField, CopyLimit>>;

export type CopyViolation = {
  field: CopyField;
  rule: CopyViolationRule;
  limit: number;
  actual: number;
};

export const META_COPY_LIMITS: CopyLimits = {
  hook: {
    maxWords: 10,
    maxChars: 50,
  },
  headline: {
    maxChars: 40,
  },
  description: {
    maxChars: 125,
  },
};

function countWords(value: string): number {
  const trimmed = value.trim();
  return trimmed ? trimmed.split(/\s+/u).length : 0;
}

export function validateCopy(
  copy: CopyJson,
  limits: CopyLimits
): CopyViolation[] {
  const violations: CopyViolation[] = [];

  for (const field of Object.keys(limits) as CopyField[]) {
    const value = copy[field];
    const limit = limits[field];

    if (limit?.maxChars !== undefined && value.length > limit.maxChars) {
      violations.push({
        field,
        rule: 'max_chars',
        limit: limit.maxChars,
        actual: value.length,
      });
    }

    if (limit?.maxWords !== undefined) {
      const actual = countWords(value);

      if (actual > limit.maxWords) {
        violations.push({
          field,
          rule: 'max_words',
          limit: limit.maxWords,
          actual,
        });
      }
    }
  }

  return violations;
}