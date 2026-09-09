import {
  bookingRulesChanged,
  formatLinkCreatedAt,
  namedInvitees,
  sortBookingLinks,
} from '@/lib/bookingLinkList';
import type { BookingLinkDTO } from '@/lib/api/calendarApi';

function link(
  partial: Pick<BookingLinkDTO, 'id' | 'title' | 'created_at'> &
    Partial<BookingLinkDTO>,
): BookingLinkDTO {
  return {
    slug: partial.id,
    organization_slug: 'acme',
    description: null,
    duration_minutes: 30,
    slot_increment_minutes: 15,
    buffer_before_minutes: 0,
    buffer_after_minutes: 0,
    min_notice_minutes: 60,
    max_advance_days: 60,
    timezone: 'UTC',
    availability_windows: [],
    is_active: true,
    scope: 'team',
    calendar: 'cal',
    host: { id: 1, name: 'Ada' },
    invitees: [],
    invitee_emails: [],
    invitees_only: false,
    created_by_name: '',
    updated_at: partial.created_at,
    ...partial,
  };
}

describe('bookingLinkList', () => {
  it('sorts newest created first, then by title', () => {
    const older = link({ id: 'a', title: 'Zebra', created_at: '2026-08-01T00:00:00Z' });
    const newer = link({ id: 'b', title: 'Apple', created_at: '2026-09-01T00:00:00Z' });
    expect(sortBookingLinks([older, newer]).map((item) => item.id)).toEqual([
      'b',
      'a',
    ]);
  });

  it('formats a created date in day-month-year English', () => {
    expect(formatLinkCreatedAt('2026-08-01T00:00:00Z')).toMatch(/1 Aug 2026/);
  });

  it('reads named invitees off the link', () => {
    const named = [
      { id: 2, name: 'Grace', email: 'grace@example.com' },
      { id: null, name: 'guest@example.com', email: 'guest@example.com' },
    ];
    expect(namedInvitees(link({ id: 'c', title: 'C', created_at: '', invitees: named }))).toEqual(
      named,
    );
  });

  it('treats a duration change as a rules change, not a title edit', () => {
    const current = link({ id: 'd', title: 'Intro', created_at: '2026-09-01T00:00:00Z' });
    expect(bookingRulesChanged(current, { duration_minutes: 45 })).toBe(true);
    expect(bookingRulesChanged(current, { duration_minutes: 30 })).toBe(false);
  });
});
