import type { NotificationItem } from '@/types/notifications';
import {
  bookingInvitePhase,
  bookingInvitePickLabel,
  formatBookingSlot,
} from '@/lib/bookingInviteState';

function notice(
  overrides: Partial<NotificationItem> = {},
): NotificationItem {
  return {
    id: '1',
    category: 'MEETINGS',
    event_type: 'meeting_participant_added',
    related_object_type: 'booking_link',
    related_object_id: 'abc',
    title: 'Invite',
    body: '',
    is_read: false,
    action_url: '/book/acme/intro',
    metadata: {},
    created_at: '2024-01-01T00:00:00Z',
    responded: false,
    response: '',
    ...overrides,
  };
}

describe('bookingInviteState', () => {
  it('treats a legacy accept-without-a-slot as still pending', () => {
    const item = notice({ responded: true, response: 'accept' });
    expect(bookingInvitePhase(item)).toBe('pending');
    expect(bookingInvitePickLabel(item)).toBe('Pick a time');
  });

  it('shows change-time after a real booking', () => {
    const item = notice({
      responded: true,
      response: 'accept',
      metadata: { booked: true, start: '2026-09-10T10:00:00Z' },
    });
    expect(bookingInvitePhase(item)).toBe('booked');
    expect(bookingInvitePickLabel(item)).toBe('Change time');
  });

  it('hides pick-a-time when the host cancelled without a re-invite', () => {
    const item = notice({
      metadata: { can_rebook: false, cancelled_by_host: true },
    });
    expect(bookingInvitePhase(item)).toBe('closed');
    expect(bookingInvitePickLabel(item)).toBeNull();
  });

  it('formats a booked slot', () => {
    expect(
      formatBookingSlot('2026-09-10T10:00:00Z', '2026-09-10T10:30:00Z'),
    ).toMatch(/Sep/);
  });
});
