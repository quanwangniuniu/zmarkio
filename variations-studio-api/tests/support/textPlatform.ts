import type { PlatformSpec } from '@/src/platforms/types';

export const testTextSpec = {
  id: 'test_text_generation',
  displayName: 'Test text platform',
  fields: [
    { key: 'ad_text', label: 'Ad text', type: 'string', required: true, limits: { maxChars: 30 } },
    { key: 'action', label: 'Action', type: 'string', required: true, limits: {} },
    { key: 'tagline', label: 'Tagline', type: 'string', required: false, limits: {} },
  ],
  cta: { kind: 'enum', field: 'action', values: ['JOIN_US', 'SKIP'], normalization: 'exact', fallback: 'SKIP' },
  aspectRatios: [],
  language: { mode: 'source', excludedFields: ['action'] },
  promptIntro: 'Write ad copy.\n\n',
  promptFragment: 'Use the alternate platform prompt.\n\n',
  promptVersion: 'alternate-v2',
  slugSource: { field: 'ad_text' },
} as const satisfies PlatformSpec;
