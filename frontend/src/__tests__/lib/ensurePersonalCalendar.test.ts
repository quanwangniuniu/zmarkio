import {
  findPersonalCalendar,
  normalizeCalendarList,
} from '@/lib/ensurePersonalCalendar';
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

describe('ensurePersonalCalendar helpers', () => {
  it('reads both a bare array and a paginated payload', () => {
    const personal = calendar({ id: 'p', project_id: null, is_primary: true });
    expect(normalizeCalendarList([personal])).toEqual([personal]);
    expect(normalizeCalendarList({ results: [personal] })).toEqual([personal]);
    expect(normalizeCalendarList({ count: 0 })).toEqual([]);
  });

  it('prefers the primary personal calendar over a project one', () => {
    const extra = calendar({ id: 'p2', project_id: null });
    const primary = calendar({ id: 'p', project_id: null, is_primary: true });
    const team = calendar({ id: 't', project_id: 7 });
    expect(findPersonalCalendar([team, extra, primary])?.id).toBe('p');
    expect(findPersonalCalendar([team])).toBeUndefined();
  });
});
