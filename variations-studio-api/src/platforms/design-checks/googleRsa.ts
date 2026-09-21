import type { PlatformSpec } from '../types';

/**
 * MED-372/MED-389 design check only, deliberately absent from platformRegistry.
 * One variation is a complete asset set, not one variation per headline.
 * https://support.google.com/google-ads/answer/7684791?hl=en
 */
export const googleRsaSpec = {
  id: 'google_rsa',
  displayName: 'Google Responsive Search Ads',
  fields: [
    {
      key: 'headlines', label: 'Headlines', type: 'string[]', required: true,
      minItems: 3, maxItems: 15,
      itemLimits: { maxChars: 30, characterCounting: 'double-width' },
    },
    {
      key: 'descriptions', label: 'Descriptions', type: 'string[]', required: true,
      minItems: 2, maxItems: 4,
      itemLimits: { maxChars: 90, characterCounting: 'double-width' },
    },
  ],
  cta: { kind: 'none' },
  aspectRatios: [],
  language: { mode: 'source', excludedFields: [] },
  promptFragment: 'Produce one complete responsive search ad asset set. '
    + 'Vary the benefits, wording and angles within the set; assets must work '
    + 'individually and in different combinations. Do not add a CTA field. '
    + 'For Google Ads character limits, double-width characters count as two.',
  promptVersion: 'v1',
  slugSource: { field: 'headlines', index: 0 },
} as const satisfies PlatformSpec;
