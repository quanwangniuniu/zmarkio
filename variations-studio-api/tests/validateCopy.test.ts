import { metaSpec } from '@/src/platforms/meta';
import type { PlatformField, PlatformSpec } from '@/src/platforms/types';
import { validateCopy } from '@/src/platforms/validateCopy';

describe('validateCopy', () => {
  it.each<[string, number]>([
    ['hook', 50],
    ['headline', 40],
    ['description', 125],
  ])('checks the Meta %s character cap in Unicode code points', (field, limit) => {
    const atLimit = '🔥'.repeat(limit);
    expect(validateCopy(metaSpec, { [field]: atLimit })).toEqual([]);
    expect(validateCopy(metaSpec, { [field]: atLimit + ' ' })).toEqual([
      { field, rule: 'max_chars', actual: limit + 1, limit },
    ]);
  });

  it('allows ten words in a Meta hook and flags the eleventh', () => {
    const tenWords = Array(10).fill('a').join(' ');
    expect(validateCopy(metaSpec, { hook: tenWords })).toEqual([]);
    expect(validateCopy(metaSpec, { hook: tenWords + ' b' })).toEqual([
      { field: 'hook', rule: 'max_words', actual: 11, limit: 10 },
    ]);
  });

  it('reports every exceeded cap across submitted fields', () => {
    const violations = validateCopy(metaSpec, {
      hook: Array(11).fill('word').join(' '),
      headline: 'x'.repeat(41),
    });
    expect(violations).toEqual([
      { field: 'hook', rule: 'max_words', actual: 11, limit: 10 },
      { field: 'hook', rule: 'max_chars', actual: 54, limit: 50 },
      { field: 'headline', rule: 'max_chars', actual: 41, limit: 40 },
    ]);
  });

  // A zero-word limit makes the exact count observable through the public validator.
  const taglineSpec: PlatformSpec = {
    ...metaSpec,
    id: 'tagline',
    fields: [{
      key: 'tagline', label: 'Tagline', type: 'string', required: true,
      limits: { maxWords: 0, maxChars: 36, characterCounting: 'unicode-code-points' },
    }],
    cta: { kind: 'none' },
    slugSource: { field: 'tagline' },
  };

  it.each<[string, number]>([
    ['', 0],
    [' \t\n', 0],
    ['  one\t\t two\nthree ', 3],
    ['限时五折，今天下单立享免费配送到家', 1],
    ["state-of-the-art don't 3.5x faster", 4],
    ['Save 50% today — free shipping', 6],
    ['🔥 Sale 🔥', 3],
  ])('counts whitespace-delimited words in %j', (tagline, words) => {
    const expected = words === 0 ? [] : [{ field: 'tagline', rule: 'max_words', actual: words, limit: 0 }];
    expect(validateCopy(taglineSpec, { tagline })).toEqual(expected);
  });

  it('reads the character limit for a field supplied by the spec', () => {
    expect(validateCopy(taglineSpec, { tagline: ' '.repeat(37) })).toEqual([
      { field: 'tagline', rule: 'max_chars', actual: 37, limit: 36 },
    ]);
  });

  it.each([
    {
      key: 'wide_title', label: 'Wide title', type: 'string', required: false,
      limits: { maxChars: 30, characterCounting: 'double-width' },
    },
    {
      key: 'headlines', label: 'Headlines', type: 'string[]', required: false,
      minItems: 1, maxItems: 3, itemLimits: { maxChars: 30 },
    },
  ] satisfies PlatformField[])('rejects unsupported rules for $key only when submitted', field => {
    const spec = { ...taglineSpec, fields: [...taglineSpec.fields, field] };
    expect(validateCopy(spec, { tagline: '' })).toEqual([]);
    expect(() => validateCopy(spec, { [field.key]: 'x' })).toThrow(`${spec.id}.${field.key}`);
  });
});
