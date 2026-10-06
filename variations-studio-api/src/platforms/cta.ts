import type { CtaPolicy } from './types';

/** Apply a registered policy; free-text and absent CTA policies leave text alone. */
export function applyCtaPolicy(policy: CtaPolicy, raw: string): string {
  if (policy.kind === 'none' || policy.kind === 'freeText') return raw;

  const value = policy.normalization === 'exact' ? raw : raw.trim();
  if (policy.values.includes(value)) return value;

  const normalized = policy.normalization === 'trim-uppercase-underscores'
    ? value.toUpperCase().replace(/\s+/g, '_')
    : value;
  return policy.values.includes(normalized) ? normalized : policy.fallback;
}
