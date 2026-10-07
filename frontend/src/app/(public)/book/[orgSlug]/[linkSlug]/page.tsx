import type { Metadata } from 'next';
import BookingWidget from '@/components/calendar/BookingWidget';

/**
 * Public booking page.
 *
 * Sits in the (public) route group, which has no auth gating — the whole point
 * is that an external prospect with no account can reach it. The org slug is in
 * the path because the API needs it to resolve the tenant schema; there is no
 * authenticated user to resolve it from.
 */

interface BookingPageProps {
  params: { orgSlug: string; linkSlug: string };
}

export const metadata: Metadata = {
  title: 'Book a time',
  // Booking links are shared directly with prospects, not meant to be indexed.
  robots: { index: false, follow: false },
};

export default function BookingPage({ params }: BookingPageProps) {
  return (
    <main className="min-h-screen bg-gray-50">
      <BookingWidget orgSlug={params.orgSlug} linkSlug={params.linkSlug} />
    </main>
  );
}
