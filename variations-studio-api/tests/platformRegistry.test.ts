import {
  assertPlatformSpec,
  createPlatformRegistry,
  getPlatformSpec,
  platformRegistry,
  toPlatformPresentation,
  type PlatformSpec,
} from '@/src/platforms';
import { metaSpec } from '@/src/platforms/meta';
import { googleRsaSpec } from '@/src/platforms/design-checks/googleRsa';

describe('platform specifications', () => {
  it.each([metaSpec, googleRsaSpec])('$id is valid, serializable configuration', (spec) => {
    expect(() => assertPlatformSpec(spec)).not.toThrow();
    expect(JSON.parse(JSON.stringify(spec))).toEqual(spec);
  });

  it('declares the existing Meta fields in order with both hook limits', () => {
    expect(metaSpec.fields).toEqual([
      { key: 'hook', label: 'Hook', type: 'string', required: true,
        limits: { maxWords: 10, maxChars: 50 } },
      { key: 'headline', label: 'Headline', type: 'string', required: true,
        limits: { maxChars: 40 } },
      { key: 'description', label: 'Description', type: 'string', required: true,
        limits: { maxChars: 125 } },
      { key: 'cta', label: 'CTA', type: 'string', required: true, limits: {} },
    ]);
    expect(metaSpec.language).toEqual({ mode: 'source', excludedFields: ['cta'] });
    expect(metaSpec.aspectRatios).toEqual(['1:1', '4:5']);
    expect(metaSpec.slugSource).toEqual({ field: 'headline' });
  });

  it('models one Google RSA as a complete asset set with per-item limits', () => {
    expect(googleRsaSpec.fields).toEqual([
      { key: 'headlines', label: 'Headlines', type: 'string[]', required: true,
        minItems: 3, maxItems: 15, itemLimits: { maxChars: 30, characterCounting: 'double-width' } },
      { key: 'descriptions', label: 'Descriptions', type: 'string[]', required: true,
        minItems: 2, maxItems: 4, itemLimits: { maxChars: 90, characterCounting: 'double-width' } },
    ]);
    expect(googleRsaSpec.cta).toEqual({ kind: 'none' });
    expect(googleRsaSpec.aspectRatios).toEqual([]);
    expect(googleRsaSpec.language).toEqual({ mode: 'source', excludedFields: [] });
    expect(googleRsaSpec.slugSource).toEqual({ field: 'headlines', index: 0 });
  });

  it('registers optional body fields and free-text or absent CTA using only data', () => {
    const freeTextSpec: PlatformSpec = {
      ...metaSpec,
      id: 'custom_free_text',
      fields: [
        { key: 'title', label: 'Title', type: 'string', required: true, limits: {} },
        { key: 'body', label: 'Body', type: 'string', required: false,
          limits: { maxWords: 100 } },
        { key: 'action', label: 'Action', type: 'string', required: false,
          limits: { maxChars: 20 } },
      ],
      cta: { kind: 'freeText', field: 'action' },
      language: { mode: 'source', excludedFields: [] },
      slugSource: { field: 'title' },
    };
    const noCtaSpec: PlatformSpec = {
      ...freeTextSpec,
      id: 'custom_no_cta',
      fields: freeTextSpec.fields.slice(0, 2),
      cta: { kind: 'none' },
    };

    const registry = createPlatformRegistry([metaSpec, googleRsaSpec, freeTextSpec, noCtaSpec]);
    expect([...registry.keys()]).toEqual(['meta', 'google_rsa', 'custom_free_text', 'custom_no_cta']);
    expect(registry.get('custom_free_text')?.fields.map((field) => field.key))
      .toEqual(['title', 'body', 'action']);
    expect(registry.get('custom_no_cta')?.fields.map((field) => field.key))
      .toEqual(['title', 'body']);
  });
});

describe('platform registry', () => {
  it('exposes Meta and keeps the RSA design check out of the supported registry', () => {
    expect([...platformRegistry.keys()]).toEqual(['meta']);
    expect(getPlatformSpec('meta')).toBe(metaSpec);
    expect(() => getPlatformSpec('google_rsa')).toThrow('Unknown platform "google_rsa"');
    expect(() => getPlatformSpec('missing')).toThrow('Unknown platform "missing"');
  });

  it('rejects duplicate platform ids', () => {
    expect(() => createPlatformRegistry([metaSpec, { ...metaSpec }]))
      .toThrow('Duplicate platform id "meta"');
  });

  it('checks configuration when registering a platform', () => {
    expect(() => createPlatformRegistry([{ ...metaSpec, fields: [] }]))
      .toThrow('at least one field is required');
  });
});

describe('authored platform configuration validation', () => {
  it.each([0, -1, 1.5, NaN, Infinity])('rejects a non-positive or non-integer limit: %s', (value) => {
    const fields: PlatformSpec['fields'] = [
      { ...metaSpec.fields[0], limits: { maxChars: value } },
      ...metaSpec.fields.slice(1),
    ];
    expect(() => assertPlatformSpec({ ...metaSpec, fields })).toThrow('must be a positive integer');
    expect(() => assertPlatformSpec({
      ...googleRsaSpec,
      fields: [{ ...googleRsaSpec.fields[0], itemLimits: { maxWords: value } }, googleRsaSpec.fields[1]],
    })).toThrow('must be a positive integer');
  });

  it.each([[-1, 15], [3, 2], [1.5, 15], [3, 0], [3, 4.5]])(
    'rejects repeated-field cardinality %s..%s', (minItems, maxItems) => {
      expect(() => assertPlatformSpec({
        ...googleRsaSpec,
        fields: [{ ...googleRsaSpec.fields[0], minItems, maxItems }, googleRsaSpec.fields[1]],
      })).toThrow('invalid cardinality');
    },
  );

  it('requires a character limit when a character counting mode is configured', () => {
    expect(() => assertPlatformSpec({
      ...googleRsaSpec,
      fields: [
        { ...googleRsaSpec.fields[0], itemLimits: { characterCounting: 'double-width' } },
        googleRsaSpec.fields[1],
      ],
    })).toThrow('invalid characterCounting');
  });

  it('rejects an unsupported character counting mode in authored JSON', () => {
    const spec = JSON.parse(JSON.stringify(googleRsaSpec));
    spec.fields[0].itemLimits.characterCounting = 'bytes';
    expect(() => assertPlatformSpec(spec)).toThrow(/characterCounting/);
  });

  const invalidMetaSpecs: [string, Partial<PlatformSpec>, string][] = [
    ['duplicate fields', { fields: [...metaSpec.fields, metaSpec.fields[0]] }, 'duplicate field'],
    ['missing CTA field', { cta: { ...metaSpec.cta, field: 'missing' } }, 'CTA must reference a scalar field'],
    ['empty CTA enum', { cta: { ...metaSpec.cta, values: [] } }, 'unique non-empty values'],
    ['duplicate CTA enum', { cta: { ...metaSpec.cta, values: ['SHOP_NOW', 'SHOP_NOW'] } }, 'unique non-empty values'],
    ['blank CTA value', { cta: { ...metaSpec.cta, values: ['SHOP_NOW', ' '] } }, 'unique non-empty values'],
    ['unknown CTA fallback', { cta: { ...metaSpec.cta, fallback: 'UNKNOWN' } }, 'CTA fallback is not in the enum'],
    ['missing slug field', { slugSource: { field: 'missing' } }, 'slugSource references an undeclared field'],
    ['scalar slug index', { slugSource: { field: 'headline', index: 0 } }, 'scalar slugSource must not have an index'],
    ['optional slug field', {
      fields: metaSpec.fields.map((field) => field.key === 'headline' ? { ...field, required: false } : field),
    }, 'slugSource must reference a required field'],
    ['unknown language field', { language: { mode: 'source', excludedFields: ['missing'] } }, 'language rule references undeclared field'],
  ];

  it.each(invalidMetaSpecs)('rejects %s', (_name, overrides, message) => {
    expect(() => assertPlatformSpec({ ...metaSpec, ...overrides })).toThrow(message);
  });

  it('rejects a repeated field as a CTA source', () => {
    expect(() => assertPlatformSpec({
      ...googleRsaSpec,
      cta: { kind: 'freeText', field: 'headlines' },
    })).toThrow('CTA must reference a scalar field');
  });

  it.each([undefined, -1, 0.5, 3])('rejects an unavailable repeated slug index: %s', (index) => {
    expect(() => assertPlatformSpec({ ...googleRsaSpec, slugSource: { field: 'headlines', index } }))
      .toThrow('slugSource index must exist within the minimum cardinality');
  });

  it.each(['1', '0:1', '1:0', '-1:1', 'wide:short'])('rejects invalid aspect ratio %s', (ratio) => {
    expect(() => assertPlatformSpec({ ...metaSpec, aspectRatios: [ratio] })).toThrow('invalid aspect ratio');
  });
});

describe('platform presentation', () => {
  it.each([metaSpec, googleRsaSpec])('projects $id without internal prompt or slug configuration', (spec) => {
    const presentation = toPlatformPresentation(spec);
    expect(Object.keys(presentation)).toEqual(['id', 'displayName', 'fields', 'cta', 'aspectRatios', 'language']);
    expect(presentation.fields).toEqual(spec.fields);
    expect(presentation.cta).toEqual(spec.cta);
    expect(JSON.parse(JSON.stringify(presentation))).toEqual(presentation);
    expect(presentation).not.toHaveProperty('promptFragment');
    expect(presentation).not.toHaveProperty('promptVersion');
    expect(presentation).not.toHaveProperty('slugSource');
  });

  it('detaches nested presentation data from the server configuration', () => {
    const presentation = toPlatformPresentation(metaSpec);
    expect(presentation.fields).not.toBe(metaSpec.fields);
    expect(presentation.fields[0]).not.toBe(metaSpec.fields[0]);
    if (presentation.fields[0].type !== 'string' || presentation.cta.kind !== 'enum') {
      throw new Error('Expected the Meta scalar field and enum CTA');
    }
    expect(presentation.fields[0].limits).not.toBe(metaSpec.fields[0].limits);
    expect(presentation.cta).not.toBe(metaSpec.cta);
    expect(presentation.cta.values).not.toBe(metaSpec.cta.values);
    expect(presentation.aspectRatios).not.toBe(metaSpec.aspectRatios);
    expect(presentation.language).not.toBe(metaSpec.language);
    expect(presentation.language.excludedFields).not.toBe(metaSpec.language.excludedFields);
  });
});
