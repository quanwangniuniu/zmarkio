import {
  SAME_PROJECT_TEAM_MESSAGE,
  bookerCanUseTeamScope,
  calendarScope,
  calendarsForScope,
  defaultBookingScope,
  defaultCalendarId,
  inferBookingScope,
} from '@/lib/bookingLinkScope';
import type { CalendarDTO } from '@/lib/api/calendarApi';

function calendar(partial: Partial<CalendarDTO> & { id: string }): CalendarDTO {
  return {
    organization_id: 'org',
    owner: { id: 1, email: 'a@b.c', username: 'a', full_name: 'A' },
    name: partial.name ?? partial.id,
    color: '#000',
    visibility: 'private',
    timezone: 'UTC',
    is_primary: false,
    ...partial,
  };
}

const personal = calendar({ id: 'p', name: 'My Calendar', is_primary: true });
const team = calendar({ id: 't', name: 'Harbor', project_id: 7 });

describe('bookingLinkScope', () => {
  it('treats a project calendar as team and a bare one as personal', () => {
    expect(calendarScope(team)).toBe('team');
    expect(calendarScope(personal)).toBe('personal');
  });

  it('filters and defaults to the primary personal calendar', () => {
    const extra = calendar({ id: 'p2', name: 'Other' });
    expect(calendarsForScope([personal, team, extra], 'personal')).toEqual([
      personal,
      extra,
    ]);
    expect(defaultCalendarId([extra, personal, team], 'personal')).toBe('p');
    expect(defaultCalendarId([personal, team], 'team')).toBe('t');
  });

  it('infers scope from the selected calendar', () => {
    expect(inferBookingScope([personal, team], 't')).toBe('team');
    expect(inferBookingScope([personal, team], 'missing')).toBe('personal');
  });

  it('prefers personal when creating a link, but still allows team', () => {
    expect(defaultBookingScope([personal, team])).toBe('personal');
    expect(defaultBookingScope([team])).toBe('team');
  });

  it('only blocks team for a same-project booker', () => {
    expect(bookerCanUseTeamScope(true)).toBe(false);
    expect(bookerCanUseTeamScope(false)).toBe(true);
    expect(SAME_PROJECT_TEAM_MESSAGE).toMatch(/same project/);
  });
});
