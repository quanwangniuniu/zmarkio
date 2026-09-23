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
  it('declares the Meta fields in order with their length caps', () => {
    expect(metaSpec.fields.map((field) => field.key)).toEqual(['hook', 'headline', 'description', 'cta']);
    expect(metaSpec.fields.map((field) => field.limits)).toEqual([
      { maxWords: 10, maxChars: 50 }, { maxChars: 40 }, { maxChars: 125 }, {},
    ]);
    expect(metaSpec.language).toEqual({ mode: 'source', excludedFields: ['cta'] });
  });

  it('models one Google RSA as a complete asset set with per-item limits', () => {
    expect(googleRsaSpec.fields).toMatchObject([
      { key: 'headlines', type: 'string[]',
        minItems: 3, maxItems: 15, itemLimits: { maxChars: 30, characterCounting: 'double-width' } },
      { key: 'descriptions', type: 'string[]',
        minItems: 2, maxItems: 4, itemLimits: { maxChars: 90, characterCounting: 'double-width' } },
    ]);
    expect(googleRsaSpec.cta).toEqual({ kind: 'none' });
  });

  it('registers optional fields and CTA policies without a platform prompt fragment', () => {
    const freeTextSpec: PlatformSpec = {
      ...metaSpec,
      id: 'custom_free_text',
      promptFragment: '',
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
  it.each([0, 1.5])('rejects a non-positive or non-integer limit: %s', (value) => {
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

  it('rejects a minimum cardinality greater than the maximum', () => {
    expect(() => assertPlatformSpec({
      ...googleRsaSpec,
      fields: [{ ...googleRsaSpec.fields[0], minItems: 16 }, googleRsaSpec.fields[1]],
    })).toThrow('invalid cardinality');
  });

  it('requires a character limit when a character counting mode is configured', () => {
    expect(() => assertPlatformSpec({
      ...googleRsaSpec,
      fields: [
        { ...googleRsaSpec.fields[0], itemLimits: { characterCounting: 'double-width' } },
        googleRsaSpec.fields[1],
      ],
    })).toThrow('invalid characterCounting');
  });

  const invalidMetaSpecs: [string, Partial<PlatformSpec>, string][] = [
    ['duplicate fields', { fields: [...metaSpec.fields, metaSpec.fields[0]] }, 'duplicate field'],
    ['missing CTA field', { cta: { ...metaSpec.cta, field: 'missing' } }, 'CTA must reference a scalar field'],
    ['unknown CTA fallback', { cta: { ...metaSpec.cta, fallback: 'UNKNOWN' } }, 'CTA fallback is not in the enum'],
    ['unknown language field', {
      language: { mode: 'source', excludedFields: ['missing'] },
    }, 'language rule references undeclared field'],
    ['missing slug field', { slugSource: { field: 'missing' } }, 'slugSource references an undeclared field'],
  ];

  it.each(invalidMetaSpecs)('rejects %s', (_name, overrides, message) => {
    expect(() => assertPlatformSpec({ ...metaSpec, ...overrides })).toThrow(message);
  });

  it('requires the slug index to exist within the minimum cardinality', () => {
    expect(() => assertPlatformSpec({ ...googleRsaSpec, slugSource: { field: 'headlines', index: 2 } }))
      .not.toThrow();
    expect(() => assertPlatformSpec({ ...googleRsaSpec, slugSource: { field: 'headlines', index: 3 } }))
      .toThrow('slugSource index must exist within the minimum cardinality');
  });
});

describe('platform presentation', () => {
  it.each([metaSpec, googleRsaSpec])('projects $id without internal prompt or slug configuration', (spec) => {
    const presentation = toPlatformPresentation(spec);
    expect(Object.keys(presentation).sort())
      .toEqual(['id', 'displayName', 'fields', 'cta', 'aspectRatios', 'language'].sort());
    expect(presentation.fields).toEqual(spec.fields);
    expect(presentation.cta).toEqual(spec.cta);
    expect(JSON.parse(JSON.stringify(presentation))).toEqual(presentation);
    expect(presentation).not.toHaveProperty('promptFragment');
    expect(presentation).not.toHaveProperty('promptVersion');
    expect(presentation).not.toHaveProperty('slugSource');
  });

  it('detaches nested presentation data from the server configuration', () => {
    const presentation = toPlatformPresentation(metaSpec);
    if (presentation.fields[0].type !== 'string' || presentation.cta.kind !== 'enum') {
      throw new Error('Expected the Meta scalar field and enum CTA');
    }
    expect(presentation.fields[0].limits).not.toBe(metaSpec.fields[0].limits);
    expect(presentation.cta.values).not.toBe(metaSpec.cta.values);
    expect(presentation.language.excludedFields).not.toBe(metaSpec.language.excludedFields);
  });
});
