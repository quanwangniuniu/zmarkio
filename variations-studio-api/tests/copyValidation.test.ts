import {
  META_COPY_LIMITS,
  validateCopy,
} from '@/src/ai/validation';

describe('validateCopy', () => {
  it('returns no violations for valid copy', () => {
    const result = validateCopy(
      {
        hook: 'A short useful hook',
        headline: 'A short headline',
        description: 'A short description.',
        cta: 'LEARN_MORE',
      },
      META_COPY_LIMITS
    );

    expect(result).toEqual([]);
  });

  it('detects hook character and word violations', () => {
    const result = validateCopy(
      {
        hook:
          'one two three four five six seven eight nine ten eleven extra long text',
        headline: 'Valid headline',
        description: 'Valid description',
        cta: 'LEARN_MORE',
      },
      META_COPY_LIMITS
    );

    expect(result).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ field: 'hook', rule: 'max_chars' }),
        expect.objectContaining({ field: 'hook', rule: 'max_words' }),
      ])
    );
  });

  it('accepts custom limits as data', () => {
    const result = validateCopy(
      {
        hook: 'four word hook here',
        headline: 'Headline',
        description: 'Description',
        cta: 'LEARN_MORE',
      },
      { hook: { maxWords: 3 } }
    );

    expect(result).toEqual([
      {
        field: 'hook',
        rule: 'max_words',
        limit: 3,
        actual: 4,
      },
    ]);
  });
});
