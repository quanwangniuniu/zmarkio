import { isBookingEvent } from '@/lib/bookingEvent';

describe('isBookingEvent', () => {
  it('recognises a booking-link meeting', () => {
    expect(isBookingEvent({ metadata: { source: 'booking_link' } })).toBe(true);
  });

  it('ignores ordinary calendar events', () => {
    expect(isBookingEvent({ metadata: { source: 'manual' } })).toBe(false);
    expect(isBookingEvent({ metadata: {} })).toBe(false);
    expect(isBookingEvent({})).toBe(false);
    expect(isBookingEvent(null)).toBe(false);
  });
});
