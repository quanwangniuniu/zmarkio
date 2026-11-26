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
  generateTopicKey,
  toTopicKey,
  validateNewTopicName,
} from '@/components/decisions/decisionValidation';

// A real title and a real option write-up, reused for the length rules below.
const TITLE =
  'Shift 30% of the remaining Q4 Meta budget into Google Search and YouTube for the Black Friday to Boxing Day period, ' +
  'keep TikTok flat, and review the split with the paid social and search leads once the first December results are in. ' +
  'Confirm with finance before the 1 December cut-off.';
const OPTION =
  'Use the existing vendor for all Q4 creative production. They already know our brand guidelines and have delivered the last three campaigns on time. '.repeat(8);
const firstChars = (text: string, n: number) => Array.from(text).slice(0, n).join('');

describe('charCount / clip', () => {
  it('counts an emoji as one character, like the backend', () => {
    expect('🎄'.length).toBe(2);
    expect(charCount('🎄 Christmas push')).toBe(16);
    expect(charCount(null)).toBe(0);
  });

  it('leaves a title that fits untouched', () => {
    expect(clip('Q4 budget split', DECISION_LIMITS.title)).toEqual({ text: 'Q4 budget split', cut: false });
  });

  it('cuts by character without splitting an emoji', () => {
    expect(clip('Boxing Day 🎄🎁', 12)).toEqual({ text: 'Boxing Day 🎄', cut: true });
  });

  it('cuts a long title to 255 characters', () => {
    const result = clip(TITLE, DECISION_LIMITS.title);
    expect(charCount(TITLE)).toBeGreaterThan(255);
    expect(charCount(result.text)).toBe(255);
    expect(result.text).toBe(firstChars(TITLE, 255));
    expect(result.cut).toBe(true);
  });
});

describe('lengthState / formatCounter', () => {
  it('hides the title counter until 230 characters', () => {
    expect(lengthState(firstChars(TITLE, 229), 255, 230)).toMatchObject({ showCounter: false, atLimit: false });
    expect(lengthState(firstChars(TITLE, 230), 255, 230)).toMatchObject({ count: 230, showCounter: true, atLimit: false });
  });

  it('flags the limit at exactly 255 characters', () => {
    expect(lengthState(firstChars(TITLE, 254), 255, 230).atLimit).toBe(false);
    expect(lengthState(firstChars(TITLE, 255), 255, 230).atLimit).toBe(true);
  });

  it('formats the option counter with a thousands separator', () => {
    expect(formatCounter(lengthState(firstChars(OPTION, 936), 1000, 900))).toBe('936 / 1,000');
    expect(FIELD_MESSAGES.pastedCut(1000)).toBe('Pasted text was cut to fit 1,000 characters.');
  });
});

describe('isOptionTextBlank / isConfidenceOutOfRange', () => {
  it('treats spaces, tabs and line breaks as an empty option', () => {
    expect(isOptionTextBlank('   ')).toBe(true);
    expect(isOptionTextBlank('\n\t ')).toBe(true);
    expect(isOptionTextBlank(undefined)).toBe(true);
    expect(isOptionTextBlank('  Use the existing vendor  ')).toBe(false);
  });

  it('flags stored scores outside 1–5, such as 87 from the old free-text field', () => {
    expect(isConfidenceOutOfRange(null)).toBe(false);
    expect(isConfidenceOutOfRange(1)).toBe(false);
    expect(isConfidenceOutOfRange(5)).toBe(false);
    expect(isConfidenceOutOfRange(0)).toBe(true);
    expect(isConfidenceOutOfRange(6)).toBe(true);
    expect(isConfidenceOutOfRange(87)).toBe(true);
  });
});

describe('toTopicKey', () => {
  it.each([
    ['Q4 Budget', 'q4-budget'],
    ['  Launch_plan  2027 ', 'launch-plan-2027'],
    ['Click Frenzy -- Mid Year', 'click-frenzy-mid-year'],
    ['- EOFY Sale -', 'eofy-sale'],
    ['Bad topic!!', 'bad-topic'],
    ['🎄 !!', ''],
  ])('%s → %s', (name, key) => {
    expect(toTopicKey(name)).toBe(key);
  });
});

// Topics an Australian paid media team already has on its decision map.
const EXISTING_TOPICS = [{ title: 'EOFY Sale' }, { title: 'Click Frenzy' }, { title: 'Other' }];
const BACKEND_TOPIC_KEY = /^[a-z0-9_-]{1,64}$/;
const KEY_WITH_SUFFIX = (prefix: string) => new RegExp(`^${prefix}-[a-z0-9]{5}$`);

describe('generateTopicKey', () => {
  it('starts the key with a readable version of the name', () => {
    expect(generateTopicKey('Boxing Day Sales')).toMatch(KEY_WITH_SUFFIX('boxing-day-sales'));
    expect(generateTopicKey('Q4 Meta → Google Search budget shift (AU)')).toMatch(
      KEY_WITH_SUFFIX('q4-meta-google-search-budget-shift-au'),
    );
  });

  it('keeps giving the same key for a name, so existing keys stay valid after a release', () => {
    expect(generateTopicKey('Boxing Day Sales')).toBe('boxing-day-sales-epukg');
    expect(generateTopicKey('EOFY Sale')).toBe('eofy-sale-3xo5f');
  });

  it('gives the same key when the planner saves the same name again, whatever the case or spacing', () => {
    expect(generateTopicKey('  boxing  day SALES ')).toBe(generateTopicKey('Boxing Day Sales'));
  });

  it('keeps different names apart even when they read the same as a slug', () => {
    expect(generateTopicKey('Click-Frenzy')).not.toBe(generateTopicKey('Click Frenzy'));
  });

  it('uses "topic" when the name has no a–z or 0–9, and still keeps names apart', () => {
    expect(generateTopicKey('🎄')).toMatch(KEY_WITH_SUFFIX('topic'));
    expect(generateTopicKey('🎄')).not.toBe(generateTopicKey('🎁'));
  });

  it('keeps a long name\'s key within 64 characters', () => {
    const key = generateTopicKey('Black Friday and Cyber Monday retargeting push for AU NZ and SEA markets');
    expect(key).toMatch(KEY_WITH_SUFFIX('black-friday-and-cyber-monday-retargeting-push-for-au-nz-a'));
    expect(key).toHaveLength(64);
  });

  it('does not leave a double hyphen when the cut lands on a space', () => {
    const key = generateTopicKey('Black Friday and Cyber Monday retargeting push for AU and NZ SEA');
    expect(key).toMatch(KEY_WITH_SUFFIX('black-friday-and-cyber-monday-retargeting-push-for-au-and'));
  });

  it.each(['Boxing Day Sales', '🎄🎁', '!!!', 'Café Relaunch', 'Black Friday / Cyber Monday', 'Christmas and Boxing Day paid social push for AU and NZ, SEA to follow (draft-1)'])(
    'always gives a key the backend accepts: %s',
    (name) => {
      expect(generateTopicKey(name)).toMatch(BACKEND_TOPIC_KEY);
    },
  );
});

describe('validateNewTopicName', () => {
  it('accepts a new topic', () => {
    expect(validateNewTopicName('Boxing Day Sales', EXISTING_TOPICS)).toBeNull();
  });

  it('accepts a name that only looks like an existing one', () => {
    expect(validateNewTopicName('Click Frenzy Mid Year', EXISTING_TOPICS)).toBeNull();
    expect(validateNewTopicName('🎄', EXISTING_TOPICS)).toBeNull();
  });

  it('rejects a topic that is already on the map, ignoring case and extra spaces', () => {
    expect(validateNewTopicName(' eofy  sale ', EXISTING_TOPICS)).toBe('A topic named "eofy  sale" already exists.');
  });

  it('rejects an empty name', () => {
    expect(validateNewTopicName('   ', EXISTING_TOPICS)).toBe('Enter a topic name.');
  });

  it('allows names up to 80 characters', () => {
    const name = 'Christmas and Boxing Day paid social push for AU and NZ, SEA to follow (draft-1)';
    expect(name).toHaveLength(80);
    expect(validateNewTopicName(`  ${name}  `, EXISTING_TOPICS)).toBeNull();
    expect(validateNewTopicName(name.replace('draft-1', 'draft-12'), EXISTING_TOPICS)).toBe(
      'Use 80 characters or fewer.',
    );
  });

  it('counts an emoji as two characters, like the backend', () => {
    const name = 'Christmas and Boxing Day paid social push for AU and NZ, SEA to follow (draft)🎄🎁';
    expect(name).toHaveLength(82);
    expect(validateNewTopicName(name, EXISTING_TOPICS)).toBe('Use 80 characters or fewer.');
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