import {
  assertPlatformSpec,
  createPlatformRegistry,
  getPlatformSpec,
  listPlatformIds,
  type PlatformSpec,
} from '@/src/platforms';
import { applyCtaPolicy } from '@/src/platforms/cta';
import { metaSpec } from '@/src/platforms/meta';

const rsaHeadlines = {
  key: 'headlines', label: 'Headlines', type: 'string[]', required: true,
  minItems: 3, maxItems: 15, itemLimits: { maxChars: 30, characterCounting: 'double-width' },
} as const;

/**
 * Design check: one RSA variation is a complete asset set.
 * https://support.google.com/google-ads/answer/7684791?hl=en
 */
const googleRsaSpec: PlatformSpec = {
  ...metaSpec,
  id: 'google_rsa',
  displayName: 'Google Responsive Search Ads',
  fields: [
    rsaHeadlines,
    { key: 'descriptions', label: 'Descriptions', type: 'string[]', required: true,
      minItems: 2, maxItems: 4, itemLimits: { maxChars: 90, characterCounting: 'double-width' } },
  ],
  cta: { kind: 'none' },
  aspectRatios: [],
  language: { mode: 'source', excludedFields: [] },
  promptFragment: '',
  slugSource: { field: 'headlines', index: 0 },
};

const freeTextSpec: PlatformSpec = {
  ...metaSpec,
  id: 'custom_free_text',
  fields: [
    { key: 'title', label: 'Title', type: 'string', required: true, limits: {} },
    { key: 'body', label: 'Body', type: 'string', required: false, limits: { maxWords: 100 } },
    { key: 'action', label: 'Action', type: 'string', required: true, limits: { maxChars: 20 } },
  ],
  cta: { kind: 'freeText', field: 'action' },
  language: { mode: 'source', excludedFields: [] },
  promptFragment: '',
  slugSource: { field: 'title' },
};

describe('platform registry', () => {
  it('exposes only Meta in production and rejects unknown ids', () => {
    expect(listPlatformIds()).toEqual(['meta']);
    expect(getPlatformSpec('meta')).toBe(metaSpec);
    expect(() => getPlatformSpec('google_rsa')).toThrow('Unknown platform "google_rsa"');
  });

  it('expresses Meta, RSA arrays, free-text CTA and no-CTA platforms', () => {
    const noCtaSpec: PlatformSpec = { ...freeTextSpec, id: 'custom_no_cta', cta: { kind: 'none' } };
    const registry = createPlatformRegistry([metaSpec, googleRsaSpec, freeTextSpec, noCtaSpec]);
    expect(registry.listPlatformIds())
      .toEqual(['meta', 'google_rsa', 'custom_free_text', 'custom_no_cta']);
  });

  it('rejects duplicate platform ids', () => {
    expect(() => createPlatformRegistry([metaSpec, metaSpec])).toThrow('Duplicate platform id');
  });
});

describe('platform spec validation', () => {
  const invalidCtaFields: [string, PlatformSpec['fields']][] = [
    ['missing', metaSpec.fields.filter((field) => field.key !== 'cta')],
    ['repeated', metaSpec.fields.map((field) => field.key === 'cta'
      ? { ...field, type: 'string[]', minItems: 1, maxItems: 2, itemLimits: {} }
      : field)],
    ['optional', metaSpec.fields.map((field) => field.key === 'cta'
      ? { ...field, required: false }
      : field)],
  ];

  it.each(invalidCtaFields)('rejects a %s CTA field for enum and free-text policies', (_name, fields) => {
    const policies: PlatformSpec['cta'][] = [metaSpec.cta, { kind: 'freeText', field: 'cta' }];
    for (const cta of policies) {
      expect(() => assertPlatformSpec({ ...metaSpec, fields, cta }))
        .toThrow('CTA must reference a required string field');
    }
  });

  it.each<[string, PlatformSpec, string]>([
    ['duplicate field keys', { ...metaSpec, fields: [...metaSpec.fields, metaSpec.fields[0]] },
      'duplicate field key'],
    ['a negative minimum', { ...googleRsaSpec, fields: [{ ...rsaHeadlines, minItems: -2 }] },
      'invalid cardinality'],
    ['a fractional minimum', { ...googleRsaSpec, fields: [{ ...rsaHeadlines, minItems: 1.5 }] },
      'invalid cardinality'],
    ['an infinite maximum', { ...googleRsaSpec, fields: [{ ...rsaHeadlines, maxItems: Infinity }] },
      'invalid cardinality'],
    ['a minimum above the maximum', { ...googleRsaSpec, fields: [{ ...rsaHeadlines, minItems: 16 }] },
      'invalid cardinality'],
    ['an enum fallback outside the values', { ...metaSpec, cta: { ...metaSpec.cta, fallback: 'UNKNOWN' } },
      'CTA fallback is not in the enum'],
    ['an undeclared slug source', { ...metaSpec, slugSource: { field: 'missing' } },
      'slugSource must reference a required field'],
    ['an array slug without an index', { ...googleRsaSpec, slugSource: { field: 'headlines' } },
      'slugSource index does not fit the field'],
    ['an array slug index beyond the minimum', { ...googleRsaSpec, slugSource: { field: 'headlines', index: 3 } },
      'slugSource index does not fit the field'],
  ])('rejects %s', (_name, spec, message) => {
    expect(() => assertPlatformSpec(spec)).toThrow(message);
  });
});

describe('applyCtaPolicy', () => {
  it.each<[string, PlatformSpec['cta'], string, string]>([
    ['keeps a Meta value', metaSpec.cta, 'LEARN_MORE', 'LEARN_MORE'],
    ['normalizes Meta case and spaces', metaSpec.cta, ' learn more ', 'LEARN_MORE'],
    ['falls back for an unknown Meta value', metaSpec.cta, 'not a known CTA', 'SHOP_NOW'],
    ['requires an exact match', { ...metaSpec.cta, normalization: 'exact' }, ' LEARN_MORE ', 'SHOP_NOW'],
    ['leaves free text unchanged', { kind: 'freeText', field: 'action' }, ' Learn More ', ' Learn More '],
  ])('%s', (_name, policy, raw, expected) => {
    expect(applyCtaPolicy(policy, raw)).toBe(expected);
  });
});
