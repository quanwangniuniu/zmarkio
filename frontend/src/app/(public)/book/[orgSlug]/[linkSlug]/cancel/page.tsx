import type { Metadata } from 'next';
import BookingCancel from '@/components/calendar/BookingCancel';

/**
 * Where a guest calls off a booking.
 *
 * Anonymous like the booking page itself: the signed token in the query string
 * is the whole of the authorisation, because someone with no account has
 * nothing else to prove with. The same URL is written into the .ics as the
 * event's URL, so it survives closing the confirmation tab.
 */

interface CancelPageProps {
  params: { orgSlug: string; linkSlug: string };
  searchParams: { token?: string };
}

export const metadata: Metadata = {
  title: 'Cancel booking',
  robots: { index: false, follow: false },
};

export default function CancelBookingPage({ params, searchParams }: CancelPageProps) {
  return (
    <main className="min-h-screen bg-gray-50">
      <BookingCancel
        orgSlug={params.orgSlug}
        linkSlug={params.linkSlug}
        token={searchParams.token ?? ''}
      />
    </main>
  );
}
