import { render, screen } from '@testing-library/react';
import { BookingExistingReminder } from '@/components/calendar/BookingExistingReminder';

describe('BookingExistingReminder', () => {
  it('names the booked day and time', () => {
    render(
      <BookingExistingReminder
        bookings={[
          {
            start: '2026-09-07T00:00:00Z',
            end: '2026-09-07T01:00:00Z',
            title: 'standup4',
          },
        ]}
        timeZone="Australia/Sydney"
      />,
    );

    const reminder = screen.getByTestId('booking-existing-reminder');
    expect(reminder).toHaveTextContent(/You already have a booking on/i);
    expect(reminder).toHaveTextContent(/September/);
    expect(reminder).toHaveTextContent(/10:00/i);
  });

  it('renders nothing when the viewer has no booking', () => {
    const { container } = render(
      <BookingExistingReminder bookings={[]} timeZone="UTC" />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
