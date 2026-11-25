import {
  applySaved,
  buildOptionsSave,
  newOptionRow,
  optionsKey,
  rowsFromOptions,
  type OptionRow,
} from '@/components/decisions/detail/optionRows';

const row = (over: Partial<OptionRow>): OptionRow => ({
  key: 'k',
  text: '',
  isSelected: false,
  savedText: null,
  ...over,
});

describe('rowsFromOptions', () => {
  it('sorts by order and marks rows as saved', () => {
    const rows = rowsFromOptions([
      { text: 'B', isSelected: true, order: 1 },
      { text: 'A', isSelected: false, order: 0 },
    ]);
    expect(rows.map((r) => [r.text, r.isSelected, r.savedText])).toEqual([
      ['A', false, 'A'],
      ['B', true, 'B'],
    ]);
    expect(new Set(rows.map((r) => r.key)).size).toBe(2);
  });

  it('handles null', () => {
    expect(rowsFromOptions(null)).toEqual([]);
  });
});

describe('buildOptionsSave', () => {
  it('leaves out a new blank row', () => {
    const { options, sent } = buildOptionsSave([
      row({ key: 'a', text: 'A', savedText: 'A' }),
      newOptionRow(),
    ]);
    expect(options).toEqual([{ text: 'A', isSelected: false, order: 0 }]);
    expect([...sent.keys()]).toEqual(['a']);
  });

  it('treats whitespace-only text as blank', () => {
    const { options } = buildOptionsSave([row({ key: 'a', text: '  \n ' })]);
    expect(options).toEqual([]);
  });

  it('keeps the last saved text for a saved row the user cleared', () => {
    const { options, sent } = buildOptionsSave([row({ key: 'a', text: '', savedText: 'Old', isSelected: true })]);
    expect(options).toEqual([{ text: 'Old', isSelected: true, order: 0 }]);
    expect(sent.get('a')).toBe('Old');
  });

  it('drops a legacy blank option that was saved before validation existed', () => {
    const { options } = buildOptionsSave([row({ key: 'a', text: '', savedText: '' })]);
    expect(options).toEqual([]);
  });

  it('renumbers orders without gaps', () => {
    const { options } = buildOptionsSave([
      row({ key: 'a', text: 'A', savedText: 'A' }),
      row({ key: 'b', text: '' }),
      row({ key: 'c', text: 'C' }),
    ]);
    expect(options.map((o) => [o.text, o.order])).toEqual([
      ['A', 0],
      ['C', 1],
    ]);
  });
});

describe('applySaved', () => {
  it('updates savedText only for rows that were sent', () => {
    const rows = [row({ key: 'a', text: 'A2', savedText: 'A' }), row({ key: 'b', text: '' })];
    const { sent } = buildOptionsSave(rows);
    expect(applySaved(rows, sent).map((r) => r.savedText)).toEqual(['A2', null]);
  });
});

describe('optionsKey', () => {
  it('ignores ids, extra fields and array order', () => {
    const a = optionsKey([
      { id: 1, decisionId: 9, text: 'A', isSelected: false, order: 0 },
      { id: 2, decisionId: 9, text: 'B', isSelected: true, order: 1 },
    ]);
    const b = optionsKey([
      { text: 'B', isSelected: true, order: 1 },
      { text: 'A', isSelected: false, order: 0 },
    ]);
    expect(a).toBe(b);
  });

  it('changes when text, selection or order changes', () => {
    const base = optionsKey([{ text: 'A', isSelected: false, order: 0 }]);
    expect(optionsKey([{ text: 'A!', isSelected: false, order: 0 }])).not.toBe(base);
    expect(optionsKey([{ text: 'A', isSelected: true, order: 0 }])).not.toBe(base);
    expect(optionsKey([{ text: 'A', isSelected: false, order: 1 }])).not.toBe(base);
  });

  it('matches what buildOptionsSave sends, so our own save is recognised', () => {
    const rows = [row({ key: 'a', text: 'A' }), row({ key: 'b', text: '' }), row({ key: 'c', text: 'C' })];
    const { options } = buildOptionsSave(rows);
    const echoed = options.map((o, i) => ({ ...o, id: 100 + i, decisionId: 1 }));
    expect(optionsKey(echoed)).toBe(optionsKey(options));
  });
});
