import { assignUniqueSlugs, slugLookup, slugWithSuffix } from '@/lib/slugs';

const longBase = 'a'.repeat(200);

describe('slugWithSuffix', () => {
  it('keeps a 200-character base when no suffix is needed', () => {
    expect(slugWithSuffix(longBase, 0)).toBe(longBase);
    expect(slugWithSuffix(longBase, 0)).toHaveLength(200);
  });

  it('frees two characters for -1', () => {
    expect(slugWithSuffix(longBase, 1)).toBe(`${'a'.repeat(198)}-1`);
  });

  it('frees three characters for -10', () => {
    expect(slugWithSuffix(longBase, 10)).toBe(`${'a'.repeat(197)}-10`);
  });

  it('appends the suffix to a short base unchanged', () => {
    expect(slugWithSuffix('sale', 2)).toBe('sale-2');
  });
});

describe('assignUniqueSlugs', () => {
  it('numbers duplicate bases in the same batch', () => {
    const used = new Set<string>();

    expect(assignUniqueSlugs(['sale', 'sale', 'sale'], used)).toEqual([
      'sale',
      'sale-1',
      'sale-2',
    ]);
  });

  it('skips a truncated -1 that is already occupied', () => {
    const used = new Set([slugWithSuffix(longBase, 0), slugWithSuffix(longBase, 1)]);

    expect(assignUniqueSlugs([longBase], used)).toEqual([
      `${'a'.repeat(198)}-2`,
    ]);
  });

  it('uses the bare base when only a later suffix is taken', () => {
    const used = new Set(['sale-1']);

    expect(assignUniqueSlugs(['sale', 'sale'], used)).toEqual(['sale', 'sale-2']);
  });
});

describe('slugLookup', () => {
  it('matches a short base exactly and then its hyphen suffixes', () => {
    expect(slugLookup('sale')).toEqual({
      kind: 'short',
      base: 'sale',
      pattern: 'sale-%',
    });
  });

  it('treats a 190-character base as short', () => {
    const base = 'b'.repeat(190);

    expect(slugLookup(base)).toEqual({
      kind: 'short',
      base,
      pattern: `${base}-%`,
    });
  });

  it('matches a longer base by its first 190 characters', () => {
    expect(slugLookup('c'.repeat(191))).toEqual({
      kind: 'long',
      pattern: `${'c'.repeat(190)}%`,
    });
    expect(slugLookup(longBase)).toEqual({
      kind: 'long',
      pattern: `${'a'.repeat(190)}%`,
    });
  });

  it('escapes LIKE wildcards in the base', () => {
    expect(slugLookup('100%_off')).toEqual({
      kind: 'short',
      base: '100%_off',
      pattern: '100\\%\\_off-%',
    });
  });
});
