import {
  applySaved,
  buildOptionsSave,
  newOptionRow,
  optionsKey,
  rowsFromOptions,
  type OptionRow,
} from '@/components/decisions/detail/optionRows';

// Options for "Who produces the Q4 holiday creative?"
const VENDOR = 'Use the existing vendor for all Q4 creative';
const AGENCY = 'Split video work with a new agency';
const IN_HOUSE = 'Bring creative in-house with a contract designer';

const row = (over: Partial<OptionRow>): OptionRow => ({
  key: 'k',
  text: '',
  isSelected: false,
  savedText: null,
  ...over,
});

describe('rowsFromOptions', () => {
  it('lists saved options in their saved order', () => {
    const rows = rowsFromOptions([
      { text: AGENCY, isSelected: true, order: 1 },
      { text: VENDOR, isSelected: false, order: 0 },
    ]);
    expect(rows.map((r) => [r.text, r.isSelected, r.savedText])).toEqual([
      [VENDOR, false, VENDOR],
      [AGENCY, true, AGENCY],
    ]);
    expect(new Set(rows.map((r) => r.key)).size).toBe(2);
  });

  it('handles a decision with no options yet', () => {
    expect(rowsFromOptions(null)).toEqual([]);
  });
});

describe('buildOptionsSave', () => {
  it('leaves out an option the planner has just added and not written yet', () => {
    const { options, sent } = buildOptionsSave([
      row({ key: 'vendor', text: VENDOR, savedText: VENDOR }),
      newOptionRow(),
    ]);
    expect(options).toEqual([{ text: VENDOR, isSelected: false, order: 0 }]);
    expect([...sent.keys()]).toEqual(['vendor']);
  });

  it('treats an option of only spaces and line breaks as empty', () => {
    const { options } = buildOptionsSave([row({ key: 'vendor', text: '  \n ' })]);
    expect(options).toEqual([]);
  });

  it('keeps the last saved text while the planner has an option cleared', () => {
    const { options, sent } = buildOptionsSave([
      row({ key: 'vendor', text: '', savedText: VENDOR, isSelected: true }),
    ]);
    expect(options).toEqual([{ text: VENDOR, isSelected: true, order: 0 }]);
    expect(sent.get('vendor')).toBe(VENDOR);
  });

  it('drops a blank option that was saved before validation existed', () => {
    const { options } = buildOptionsSave([row({ key: 'legacy', text: '', savedText: '' })]);
    expect(options).toEqual([]);
  });

  it('renumbers the order without gaps when a blank option sits in the middle', () => {
    const { options } = buildOptionsSave([
      row({ key: 'vendor', text: VENDOR, savedText: VENDOR }),
      row({ key: 'blank', text: '' }),
      row({ key: 'in-house', text: IN_HOUSE }),
    ]);
    expect(options.map((o) => [o.text, o.order])).toEqual([
      [VENDOR, 0],
      [IN_HOUSE, 1],
    ]);
  });
});

describe('applySaved', () => {
  it('marks only the options that were actually sent as saved', () => {
    const edited = `${VENDOR} and display`;
    const rows = [row({ key: 'vendor', text: edited, savedText: VENDOR }), row({ key: 'blank', text: '' })];
    const { sent } = buildOptionsSave(rows);
    expect(applySaved(rows, sent).map((r) => r.savedText)).toEqual([edited, null]);
  });
});

describe('optionsKey', () => {
  it('sees the server reply as the same options, despite ids and order of the array', () => {
    const fromServer = optionsKey([
      { id: 101, decisionId: 48, text: VENDOR, isSelected: false, order: 0 },
      { id: 102, decisionId: 48, text: AGENCY, isSelected: true, order: 1 },
    ]);
    const sentByUs = optionsKey([
      { text: AGENCY, isSelected: true, order: 1 },
      { text: VENDOR, isSelected: false, order: 0 },
    ]);
    expect(fromServer).toBe(sentByUs);
  });

  it('changes when the text, the choice or the order changes', () => {
    const base = optionsKey([{ text: VENDOR, isSelected: false, order: 0 }]);
    expect(optionsKey([{ text: `${VENDOR}.`, isSelected: false, order: 0 }])).not.toBe(base);
    expect(optionsKey([{ text: VENDOR, isSelected: true, order: 0 }])).not.toBe(base);
    expect(optionsKey([{ text: VENDOR, isSelected: false, order: 1 }])).not.toBe(base);
  });

  it('matches what buildOptionsSave sends, so our own save is recognised when it comes back', () => {
    const rows = [
      row({ key: 'vendor', text: VENDOR }),
      row({ key: 'blank', text: '' }),
      row({ key: 'in-house', text: IN_HOUSE }),
    ];
    const { options } = buildOptionsSave(rows);
    const echoed = options.map((o, i) => ({ ...o, id: 100 + i, decisionId: 48 }));
    expect(optionsKey(echoed)).toBe(optionsKey(options));
  });
});
