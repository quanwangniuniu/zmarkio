import {
  isAllEventsSelection,
  isAllEventsStoredValue,
  resolveVisibleCalendarSelection,
} from '@/components/calendar/utils';

const PERSONAL_ID = '11111111-1111-4111-8111-111111111111';
const TEAM_ID = '22222222-2222-4222-8222-222222222222';

const accessible = [
  { id: PERSONAL_ID, project_id: null },
  { id: TEAM_ID, project_id: 42 },
];

describe('resolveVisibleCalendarSelection', () => {
  it('keeps a personal calendar even when it is not the project calendar', () => {
    expect(
      resolveVisibleCalendarSelection(42, accessible, [PERSONAL_ID]),
    ).toBeNull();
  });

  it('keeps the project calendar when the user selected it', () => {
    expect(
      resolveVisibleCalendarSelection(42, accessible, [TEAM_ID]),
    ).toBeNull();
  });

  it('does not snap back when the view payload only includes the team calendar', () => {
    const viewOnlyTeam = [{ id: TEAM_ID, project_id: 42 }];
    expect(
      resolveVisibleCalendarSelection(42, viewOnlyTeam, [PERSONAL_ID]),
    ).toEqual([TEAM_ID]);
    expect(
      resolveVisibleCalendarSelection(42, accessible, [PERSONAL_ID]),
    ).toBeNull();
  });

  it('defaults to the project calendar when nothing is selected', () => {
    expect(resolveVisibleCalendarSelection(42, accessible, undefined)).toEqual([
      TEAM_ID,
    ]);
  });

  it('keeps an explicit all-events selection', () => {
    expect(resolveVisibleCalendarSelection(42, accessible, [])).toBeNull();
    expect(isAllEventsSelection([])).toBe(true);
    expect(isAllEventsSelection(undefined)).toBe(false);
    expect(isAllEventsStoredValue('all')).toBe(true);
    expect(isAllEventsStoredValue(TEAM_ID)).toBe(false);
  });

  it('defaults to the project calendar when the stored id is gone', () => {
    expect(
      resolveVisibleCalendarSelection(42, accessible, [
        '33333333-3333-4333-8333-333333333333',
      ]),
    ).toEqual([TEAM_ID]);
  });
});
