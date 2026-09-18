'use client';

import { useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { AlertCircle, CalendarX2, CheckCircle2, Loader2 } from 'lucide-react';
import Link from 'next/link';
import { PublicBookingAPI } from '@/lib/api/calendarApi';
import {
  clearBookingConfirmation,
  clearBookingReminder,
} from './bookingConfirmationSession';

interface BookingCancelProps {
  orgSlug: string;
  linkSlug: string;
  token: string;
}

type Stage = 'confirm' | 'working' | 'done' | 'failed';

/**
 * Cancelling asks first.
 *
 * Deliberately not automatic on page load: mail clients and chat apps prefetch
 * links, and a cancellation that fires on a prefetch would silently drop a
 * meeting nobody meant to drop.
 */
export default function BookingCancel({ orgSlug, linkSlug, token }: BookingCancelProps) {
  const [stage, setStage] = useState<Stage>('confirm');

  const cancel = async () => {
    if (stage === 'working') return;
    setStage('working');
    try {
      await PublicBookingAPI.cancelBooking(orgSlug, linkSlug, token);
      clearBookingConfirmation(orgSlug, linkSlug);
      clearBookingReminder(orgSlug, linkSlug);
      setStage('done');
    } catch {
      setStage('failed');
    }
  };

  if (!token) {
    return (
      <div
        className="mx-auto mt-24 max-w-md rounded-2xl border border-gray-200 bg-white p-8 text-center shadow-sm"
        data-testid="cancel-missing-token"
      >
        <AlertCircle className="mx-auto h-8 w-8 text-gray-300" />
        <h1 className="mt-4 text-lg font-semibold text-gray-900">Link incomplete</h1>
        <p className="mt-2 text-sm text-gray-500">
          This cancellation link is missing its code. Use the link from your
          confirmation or calendar entry.
        </p>
      </div>
    );
  }

  return (
    <div
      className="mx-auto mt-24 max-w-md rounded-2xl border border-gray-200 bg-white p-8 text-center shadow-sm animate-in fade-in zoom-in-95 duration-300"
      data-testid="booking-cancel"
    >
      <AnimatePresence mode="wait" initial={false}>
        {stage === 'done' ? (
          <motion.div
            key="done"
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.22, ease: 'easeOut' }}
          >
            <CheckCircle2 className="mx-auto h-10 w-10 text-emerald-500" />
            <h1 className="mt-4 text-lg font-semibold text-gray-900">Booking cancelled</h1>
            <p className="mt-2 text-sm text-gray-600">
              The time has been released and everyone involved has been told.
            </p>
            <p className="mt-3 text-xs text-gray-400">
              Subscribed calendars update on their next refresh and may show a
              cancelled entry. If you imported an .ics file, remove that saved
              copy manually.
            </p>
          </motion.div>
        ) : (
          <motion.div
            key="confirm"
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.22, ease: 'easeOut' }}
          >
            <CalendarX2 className="mx-auto h-10 w-10 text-gray-300" />
            <h1 className="mt-4 text-lg font-semibold text-gray-900">
              Cancel this booking?
            </h1>
            <p className="mt-2 text-sm text-gray-600">
              The slot goes back on offer and the meeting is removed from both
              in-app calendars. This can&apos;t be undone.
            </p>

            {stage === 'failed' && (
              <p
                data-testid="cancel-error"
                className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700"
              >
                That didn&apos;t work. The link may have expired, or the booking
                may already be gone.
              </p>
            )}

            <button
              type="button"
              onClick={cancel}
              disabled={stage === 'working'}
              data-testid="cancel-confirm"
              className="mt-5 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-[#3CCED7] px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-[#2AB5BD] focus:outline-none focus-visible:ring-2 focus-visible:ring-[#3CCED7] focus-visible:ring-offset-2 disabled:opacity-60"
            >
              {stage === 'working' && <Loader2 className="h-4 w-4 animate-spin" />}
              {stage === 'working' ? 'Cancelling…' : 'Cancel booking'}
            </button>
            <Link
              href={`/book/${encodeURIComponent(orgSlug)}/${encodeURIComponent(linkSlug)}`}
              data-testid="cancel-keep"
              className="mt-3 inline-block text-xs text-gray-400 underline underline-offset-2 transition-colors hover:text-gray-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#3CCED7] focus-visible:ring-offset-2"
            >
              Keep this booking
            </Link>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
