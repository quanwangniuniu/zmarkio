import {
  DECISION_LIMITS,
  FIELD_MESSAGES,
  charCount,
  clip,
  extractFieldErrors,
  formatCounter,
  isConfidenceOutOfRange,
  isOptionTextBlank,
  lengthState,
  toTopicKey,
  validateNewTopicName,
} from '@/components/decisions/decisionValidation';

describe('charCount / clip', () => {
  it('counts emoji as one character', () => {
    expect('👍'.length).toBe(2);
    expect(charCount('a👍b')).toBe(3);
    expect(charCount(null)).toBe(0);
  });

  it('leaves text within the limit untouched', () => {
    expect(clip('abc', 3)).toEqual({ text: 'abc', cut: false });
  });

  it('cuts by code point without splitting an emoji', () => {
    expect(clip('ab👍👍', 3)).toEqual({ text: 'ab👍', cut: true });
  });

  it('cuts a title to 255 characters', () => {
    const result = clip('x'.repeat(300), DECISION_LIMITS.title);
    expect(charCount(result.text)).toBe(255);
    expect(result.cut).toBe(true);
  });
});

describe('lengthState / formatCounter', () => {
  it('hides the counter below the threshold', () => {
    expect(lengthState('x'.repeat(229), 255, 230)).toMatchObject({ showCounter: false, atLimit: false });
  });

  it('shows the counter from the threshold and flags the limit', () => {
    expect(lengthState('x'.repeat(230), 255, 230)).toMatchObject({ count: 230, showCounter: true, atLimit: false });
    expect(lengthState('x'.repeat(255), 255, 230).atLimit).toBe(true);
  });

  it('formats counts with thousands separators', () => {
    expect(formatCounter(lengthState('x'.repeat(936), 1000, 900))).toBe('936 / 1,000');
    expect(FIELD_MESSAGES.pastedCut(1000)).toBe('Pasted text was cut to fit 1,000 characters.');
  });
});

describe('isOptionTextBlank / isConfidenceOutOfRange', () => {
  it('treats whitespace-only option text as blank', () => {
    expect(isOptionTextBlank('   ')).toBe(true);
    expect(isOptionTextBlank(undefined)).toBe(true);
    expect(isOptionTextBlank(' a ')).toBe(false);
  });

  it('flags only stored values outside 1–5', () => {
    expect(isConfidenceOutOfRange(null)).toBe(false);
    expect(isConfidenceOutOfRange(1)).toBe(false);
    expect(isConfidenceOutOfRange(5)).toBe(false);
    expect(isConfidenceOutOfRange(0)).toBe(true);
    expect(isConfidenceOutOfRange(87)).toBe(true);
  });
});

describe('toTopicKey', () => {
  it.each([
    ['Q4 Budget', 'q4-budget'],
    ['  Launch_plan  2027 ', 'launch-plan-2027'],
    ['a -- b', 'a-b'],
    ['--Hello--', 'hello'],
    ['Bad topic!!', 'bad-topic'],
    ['预算 !!', ''],
  ])('%s → %s', (name, key) => {
    expect(toTopicKey(name)).toBe(key);
  });
});

describe('validateNewTopicName', () => {
  it('accepts a valid name', () => {
    expect(validateNewTopicName('Q4 Budget', ['other'])).toEqual({ key: 'q4-budget', error: null });
  });

  it('rejects an empty name', () => {
    expect(validateNewTopicName('   ', []).error).toBe('Enter a topic name.');
  });

  it('rejects a name with no usable characters', () => {
    expect(validateNewTopicName('预算 !!', []).error).toBe(FIELD_MESSAGES.topicKeyEmpty);
  });

  it('rejects a key longer than 64 characters', () => {
    expect(validateNewTopicName('a'.repeat(72), []).error).toBe(
      'This name is too long. Use 64 characters or fewer.',
    );
  });

  it('rejects a duplicate key', () => {
    expect(validateNewTopicName('Other', ['other']).error).toBe(
      'A topic with the key other already exists. Choose another name.',
    );
  });
});

describe('extractFieldErrors', () => {
  const withData = (data: unknown) => ({ response: { data } });

  it('reads decision-service 400 bodies', () => {
    expect(
      extractFieldErrors(withData({ title: ['Title must be 255 characters or fewer.'] })),
    ).toEqual({ title: 'Title must be 255 characters or fewer.' });
  });

  it('reads the commit fieldErrors format', () => {
    expect(
      extractFieldErrors(
        withData({
          error: {
            details: {
              fieldErrors: [
                { field: 'confidenceScore', message: 'Required.' },
                { field: 'origin_meeting_id', message: 'Invalid meeting.' },
              ],
            },
          },
        }),
      ),
    ).toEqual({ confidenceScore: 'Required.', originMeetingId: 'Invalid meeting.' });
  });

  it('converts snake_case keys and accepts plain strings', () => {
    expect(extractFieldErrors(withData({ project_id: 'Project context is required.' }))).toEqual({
      projectId: 'Project context is required.',
    });
  });

  it('ignores generic messages and non-HTTP errors', () => {
    expect(extractFieldErrors(withData({ detail: 'Not found' }))).toEqual({});
    expect(extractFieldErrors(new Error('Network Error'))).toEqual({});
    expect(extractFieldErrors(undefined)).toEqual({});
  });
});