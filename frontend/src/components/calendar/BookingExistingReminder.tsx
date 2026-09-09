import type { ViewerBookingDTO } from '@/lib/api/calendarApi';
import { formatDayLabel, formatTime } from './bookingSlots';

export function BookingExistingReminder({
  bookings,
  timeZone,
}: {
  bookings: ViewerBookingDTO[];
  timeZone: string;
}) {
  if (bookings.length === 0) return null;

  return (
    <div
      data-testid="booking-existing-reminder"
      className="mb-4 rounded-lg border border-[#3CCED7]/40 bg-[#3CCED7]/10 px-3 py-2.5 text-sm font-medium leading-relaxed text-[#0E8A96]"
    >
      {bookings.map((booking) => (
        <p key={`${booking.start}-${booking.end}`}>
          You already have a booking on {formatDayLabel(booking.start, timeZone)} at{' '}
          {formatTime(booking.start, timeZone)}.
        </p>
      ))}
    </div>
  );
}
