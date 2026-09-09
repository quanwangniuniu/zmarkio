'use client';

import DashboardLayout from '@/components/dashboard/DashboardLayout';
import { ProtectedRoute } from '@/components/auth/ProtectedRoute';
import BookingLinkManager from '@/components/calendar/BookingLinkManager';
import { useAuthStore } from '@/lib/authStore';

/**
 * Owner-side booking link management.
 *
 * The public booking page needs a link to exist; this is where one is
 * generated. The shareable URL is org-scoped, and the org must be the *user's*
 * own organization — that is what the API assigns to a new link. The active
 * project's org is not the same thing: a user can belong to projects in other
 * organizations, and using that slug produces a URL that 404s.
 */

function BookingLinksContent() {
  const user = useAuthStore((s) => s.user);
  const orgSlug = user?.organization?.slug ?? '';

  return (
    <DashboardLayout hideRightPanel>
      {orgSlug ? (
        <BookingLinkManager orgSlug={orgSlug} />
      ) : (
        <div className="mx-auto mt-16 max-w-md rounded-xl border border-dashed border-gray-200 bg-white p-8 text-center text-sm text-gray-500">
          Your account isn&apos;t linked to an organisation yet — booking links are scoped to one.
        </div>
      )}
    </DashboardLayout>
  );
}

export default function BookingLinksPage() {
  return (
    <ProtectedRoute>
      <BookingLinksContent />
    </ProtectedRoute>
  );
}
