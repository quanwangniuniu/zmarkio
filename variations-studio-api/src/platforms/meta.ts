import { CTA_ENUM_ALLOWLIST, PROMPT_VERSION, SYSTEM_PROMPT } from '@/src/ai/prompts';
import type { PlatformSpec } from './types';

/**
 * C1 compatibility bridge: reuse the live Meta constants verbatim. C2 owns
 * moving their source here and switching existing consumers to the registry.
 */
export const metaSpec = {
  id: 'meta',
  displayName: 'Meta (Facebook + Instagram Feed)',
  fields: [
    { key: 'hook', label: 'Hook', type: 'string', required: true,
      limits: { maxWords: 10, maxChars: 50 } },
    { key: 'headline', label: 'Headline', type: 'string', required: true,
      limits: { maxChars: 40 } },
    { key: 'description', label: 'Description', type: 'string', required: true,
      limits: { maxChars: 125 } },
    { key: 'cta', label: 'CTA', type: 'string', required: true, limits: {} },
  ],
  cta: {
    kind: 'enum',
    field: 'cta',
    values: CTA_ENUM_ALLOWLIST.split(',').map((value) => value.trim()),
    normalization: 'trim-uppercase-underscores',
    fallback: 'SHOP_NOW',
  },
  // Feed creative presets for future M6 consumers; no crop/upload behavior changes.
  aspectRatios: ['1:1', '4:5'],
  language: { mode: 'source', excludedFields: ['cta'] },
  promptFragment: SYSTEM_PROMPT,
  promptVersion: PROMPT_VERSION,
  slugSource: { field: 'headline' },
} as const satisfies PlatformSpec;
