"use client";

import { useState } from "react";
import { ArrowLeft, CalendarDays, Loader2 } from "lucide-react";
import { PublicBookingAPI } from "@/lib/api/calendarApi";

interface BookingFindProps {
  orgSlug: string;
  linkSlug: string;
  timeZone: string;
  onBack: () => void;
}

/** Recovery links go to the booking email; contact details alone grant no access. */
export default function BookingFind({
  orgSlug,
  linkSlug,
  onBack,
}: BookingFindProps) {
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const search = async (event: React.FormEvent) => {
    event.preventDefault();
    if (working || !email.trim()) return;
    setWorking(true);
    setError(null);
    setSubmitted(false);
    try {
      await PublicBookingAPI.lookupBookings(orgSlug, linkSlug, {
        email: email.trim(),
      });
      setSubmitted(true);
    } catch {
      setError("Could not request a recovery link. Please try again later.");
    } finally {
      setWorking(false);
    }
  };

  return (
    <div
      className="mx-auto mt-24 max-w-md rounded-2xl border border-gray-200 bg-white p-8 shadow-sm"
      data-testid="booking-find"
    >
      <button
        type="button"
        onClick={onBack}
        data-testid="booking-find-back"
        className="mb-4 flex items-center gap-1.5 text-xs font-medium text-gray-500 hover:text-gray-900"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> Back
      </button>
      <CalendarDays className="mx-auto h-8 w-8 text-gray-300" />
      <h1 className="mt-4 text-center text-lg font-semibold text-gray-900">
        Find your booking
      </h1>
      <p className="mt-2 text-center text-sm text-gray-500">
        Enter the email you booked with to request a link to manage your
        booking.
      </p>
      <form onSubmit={search} className="mt-6 space-y-3">
        <label className="block text-left text-xs font-medium text-gray-600">
          Email
          <input
            type="email"
            required
            maxLength={254}
            value={email}
            onChange={(event) => {
              setEmail(event.target.value);
              setSubmitted(false);
            }}
            data-testid="booking-find-value"
            className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-900 focus:border-gray-400 focus:outline-none"
          />
        </label>
        {error && (
          <p
            role="alert"
            className="text-sm text-amber-700"
            data-testid="booking-find-error"
          >
            {error}
          </p>
        )}
        <button
          type="submit"
          disabled={working}
          data-testid="booking-find-submit"
          className="flex w-full items-center justify-center gap-2 rounded-lg bg-[#3CCED7] px-4 py-2.5 text-sm font-semibold text-white hover:bg-[#2AB5BD] disabled:opacity-60"
        >
          {working ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            "Send recovery link"
          )}
        </button>
      </form>
      {submitted && (
        <p
          role="status"
          className="mt-6 border-t border-gray-100 pt-5 text-center text-sm text-gray-500"
          data-testid="booking-find-results"
        >
          If an upcoming booking matches, a recovery link will be sent to the
          email used to book. You can also use the link from your original
          confirmation.
        </p>
      )}
    </div>
  );
}
