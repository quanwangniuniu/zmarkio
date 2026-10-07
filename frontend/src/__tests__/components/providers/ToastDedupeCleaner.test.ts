import {
  activeToastIds,
  dedupeKeysToClear,
} from '@/components/providers/ToastDedupeCleaner';

describe('ToastDedupeCleaner', () => {
  it('treats a dismissed toast as gone so its dedupe entry can reset', () => {
    const active = activeToastIds([
      { id: 'still-visible', dismissed: false },
      { id: 'leaving', dismissed: true },
    ]);

    expect(active.has('still-visible')).toBe(true);
    expect(active.has('leaving')).toBe(false);
    expect(dedupeKeysToClear(['still-visible', 'leaving'], active)).toEqual([
      'leaving',
    ]);
  });

  it('does not clear a key that has not been seen on screen', () => {
    const active = activeToastIds([]);
    expect(dedupeKeysToClear([], active)).toEqual([]);
  });
});
