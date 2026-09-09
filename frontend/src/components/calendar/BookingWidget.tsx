'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
// AnimatePresence is the only way to animate an element on the way out; the
// project already uses it (see KPICard).
import { AnimatePresence, motion } from 'framer-motion';
import {
  ArrowLeft,
  CalendarDays,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock,
  Download,
  Globe,
  Loader2,
  User,
} from 'lucide-react';
import {
  PublicBookingAPI,
  type BookingSlotDTO,
  type PublicBookingLinkDTO,
  type ViewerBookingDTO,
} from '@/lib/api/calendarApi';
import {
  buildMonthGrid,
  detectTimezone,
  formatDayLabel,
  formatTime,
  groupSlotsByDay,
  groupSlotsByPeriod,
  firstDayOfWeek,
  monthLabel,
  monthRange,
  weekdayHeads,
  timezoneLabel,
  todayKey,
  TIMEZONE_CHOICES,
} from './bookingSlots';
import { downloadIcs, type CalendarEntry } from './calendarExport';
import BookingFind from './BookingFind';
import { BookingExistingReminder } from './BookingExistingReminder';
import {
  clearBookingConfirmation,
  readBookingConfirmation,
  readBookingReminder,
  saveBookingConfirmation,
  saveBookingReminder,
} from './bookingConfirmationSession';
import { resolveViewerBookings } from '@/lib/bookingViewerReminder';
import { useAuthStore } from '@/lib/authStore';
import { bookerDisplayName, isInternalBooker } from '@/lib/bookingBookerIdentity';
import ConfirmDialog from '@/components/common/ConfirmDialog';
import { CalendarScopeSwitch } from './CalendarScopeSwitch';
import {
  SAME_PROJECT_TEAM_MESSAGE,
  bookerCanUseTeamScope,
  type BookingScope,
} from '@/lib/bookingLinkScope';

interface BookingWidgetProps {
  orgSlug: string;
  linkSlug: string;
}

type Stage = 'loading' | 'picking' | 'confirming' | 'finding' | 'booked' | 'missing';

function errorMessage(error: unknown, fallback: string): string {
  const data = (error as { response?: { data?: unknown } })?.response?.data;
  if (typeof data === 'string') return data;
  if (data && typeof data === 'object') {
    const record = data as Record<string, unknown>;
    if (typeof record.message === 'string') return record.message;
    const first = Object.values(record)[0];
    if (typeof first === 'string') return first;
    if (Array.isArray(first) && typeof first[0] === 'string') return first[0];
  }
  return fallback;
}

export default function BookingWidget({ orgSlug, linkSlug }: BookingWidgetProps) {
  const user = useAuthStore((state) => state.user);
  const token = useAuthStore((state) => state.token);
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  const hasHydrated = useAuthStore((state) => state.hasHydrated);
  const internalBooker = hasHydrated && isInternalBooker(user, isAuthenticated);
  const showGuestFields = hasHydrated && !internalBooker;

  const [stage, setStage] = useState<Stage>('loading');
  const [link, setLink] = useState<PublicBookingLinkDTO | null>(null);
  const [timeZone, setTimeZone] = useState<string>('UTC');
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [selectedSlot, setSelectedSlot] = useState<BookingSlotDTO | null>(null);
  // Keeping `end` as well as `start` so the confirmation can offer an
  // add-to-calendar entry without another round trip.
  const [confirmation, setConfirmation] = useState<CalendarEntry | null>(null);
  // Kept beside the entry rather than inside it: a subscription URL is not part
  // of the calendar entry, it is how the guest keeps that entry current.
  const [feedUrl, setFeedUrl] = useState<string>('');
  const [feedProtocol, setFeedProtocol] = useState<'webcal' | 'http'>('webcal');
  const [copyingFeed, setCopyingFeed] = useState(false);
  const [feedCopyResult, setFeedCopyResult] = useState<{
    url: string;
    success: boolean;
  } | null>(null);
  // The API's webcal URL uses the public site's host. Restore its transport
  // scheme for manual subscription without downgrading HTTPS sites.
  const httpFeedUrl = feedUrl.replace(
    /^webcal:/i,
    typeof window !== 'undefined' ? window.location.protocol : 'https:',
  );
  const subscriptionUrl = feedProtocol === 'webcal'
    ? feedUrl.replace(/^https?:/i, 'webcal:')
    : httpFeedUrl;
  const copySubscriptionUrl = async () => {
    setCopyingFeed(true);
    setFeedCopyResult(null);
    try {
      await navigator.clipboard.writeText(subscriptionUrl);
      setFeedCopyResult({ url: subscriptionUrl, success: true });
    } catch {
      setFeedCopyResult({ url: subscriptionUrl, success: false });
    } finally {
      setCopyingFeed(false);
    }
  };
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [form, setForm] = useState({ name: '', email: '', phone: '', notes: '' });
  const [bookerScope, setBookerScope] = useState<BookingScope>('personal');
  const [confirmedScope, setConfirmedScope] = useState<BookingScope | null>(null);
  const [sameProjectPrompt, setSameProjectPrompt] = useState(false);
  const [viewerBookings, setViewerBookings] = useState<ViewerBookingDTO[]>([]);

  // Which month the picker is showing. Kept as plain year/month so paging never
  // drifts across timezones.
  const now = new Date();
  const [view, setView] = useState({
    year: now.getUTCFullYear(),
    month: now.getUTCMonth(),
  });

  // Resolved after mount: the server can't know the viewer's zone, and reading
  // it during render would differ between server and client HTML.
  useEffect(() => {
    setTimeZone(detectTimezone());
  }, []);

  useEffect(() => {
    const stored = readBookingConfirmation(orgSlug, linkSlug);
    if (!stored) return;
    setConfirmation(stored.confirmation);
    setFeedUrl(stored.feedUrl);
    setConfirmedScope(stored.bookerScope ?? null);
    setStage('booked');
  }, [orgSlug, linkSlug]);

  const load = useCallback(async () => {
    try {
      // Fetch only the visible month rather than a fixed window, so paging
      // forward actually shows more availability.
      const { from, to } = monthRange(view.year, view.month);
      const padded = new Date(from.getTime() - 86_400_000);
      const data = await PublicBookingAPI.getAvailability(
        orgSlug,
        linkSlug,
        {
          from: (padded > new Date() ? padded : new Date()).toISOString(),
          to: new Date(to.getTime() + 86_400_000).toISOString(),
        },
        token,
      );
      if (data.invitees_only && data.viewer_can_book === false) {
        setLink(null);
        setStage('missing');
        return;
      }
      setLink(data);
      setViewerBookings(
        resolveViewerBookings({
          fromApi: data.viewer_bookings,
          fromSession: readBookingReminder(orgSlug, linkSlug),
          signedIn: Boolean(token),
        }),
      );
      setStage((current) => {
        if (forcePickerRef.current) {
          forcePickerRef.current = false;
          return 'picking';
        }
        return current === 'confirming' || current === 'booked' || current === 'finding'
          ? current
          : 'picking';
      });
    } catch (err) {
      const statusCode = (err as { response?: { status?: number } })?.response?.status;
      if (statusCode === 404) {
        setStage('missing');
        return;
      }
      setError(errorMessage(err, 'Could not load availability. Please try again.'));
      setStage('picking');
    }
  }, [orgSlug, linkSlug, view.year, view.month, token]);

  useEffect(() => {
    if (!hasHydrated) return;
    void load();
  }, [hasHydrated, load]);

  const resumePicker = () => {
    forcePickerRef.current = true;
    if (confirmation) {
      const reminder = [
        {
          start: confirmation.start,
          end: confirmation.end,
          title: confirmation.title,
        },
      ];
      saveBookingReminder(orgSlug, linkSlug, reminder);
      setViewerBookings(reminder);
    }
    clearBookingConfirmation(orgSlug, linkSlug);
    setConfirmation(null);
    setFeedUrl('');
    setSelectedSlot(null);
    setError(null);
    setStage('picking');
    void load();
  };

  // Availability is a snapshot. Without these two, a page left open keeps
  // offering times that have already passed, and the booking then fails at
  // submit with a misleading "just taken".
  const [nowTs, setNowTs] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNowTs(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') void load();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [load]);

  const dayGroups = useMemo(() => {
    if (!link) return [];
    const cutoff = nowTs + (link.min_notice_minutes ?? 0) * 60_000;
    const live = link.slots.filter((slot) => new Date(slot.start).getTime() >= cutoff);
    return groupSlotsByDay(live, timeZone);
  }, [link, timeZone, nowTs]);
  const slotsByDate = useMemo(
    () => new Map(dayGroups.map((group) => [group.date, group.slots])),
    [dayGroups],
  );

  // Deliberately no auto-selection: the time column stays hidden until the
  // visitor picks a day. Clear the selection if that day stops having times.
  useEffect(() => {
    setSelectedDate((current) =>
      current && !slotsByDate.has(current) ? null : current,
    );
  }, [slotsByDate]);

  // Keep the grid and the time column in agreement. Slots are bucketed in the
  // viewer's zone, so the first free day can land in the next month — showing a
  // blank August beside a list of September times is just confusing.
  const viewPrefix = `${view.year}-${String(view.month + 1).padStart(2, '0')}`;
  useEffect(() => {
    if (dayGroups.length === 0) return;
    if (dayGroups.some((group) => group.date.startsWith(viewPrefix))) return;
    const [year, month] = dayGroups[0].date.split('-').map(Number);
    setView({ year, month: month - 1 });
  }, [dayGroups, viewPrefix]);

  // Resolved after mount for the same reason as the timezone: locale is a
  // client fact, and reading it during render breaks hydration.
  const [firstDay, setFirstDay] = useState(1);
  useEffect(() => {
    setFirstDay(firstDayOfWeek());
  }, []);

  const weeks = useMemo(
    () => buildMonthGrid(view.year, view.month, firstDay),
    [view, firstDay],
  );
  const heads = useMemo(() => weekdayHeads(firstDay), [firstDay]);
  const today = todayKey(timeZone);
  // Memoised: the `?? []` fallback returns a fresh array each render, which
  // would defeat the memo below.
  const daySlots = useMemo(
    () => (selectedDate ? slotsByDate.get(selectedDate) ?? [] : []),
    [selectedDate, slotsByDate],
  );
  // Headings over the existing sequence — grouping never reorders the times.
  const periods = useMemo(
    () => groupSlotsByPeriod(daySlots, timeZone),
    [daySlots, timeZone],
  );

  // Bands start open: this is a booking page, and collapsing availability by
  // default would hide times a visitor came to find. Folding persists across
  // days — someone who folds "Night" means it for every day, not just this one.
  const [foldedPeriods, setFoldedPeriods] = useState<string[]>([]);
  const togglePeriod = (key: string) =>
    setFoldedPeriods((current) =>
      current.includes(key) ? current.filter((k) => k !== key) : [...current, key],
    );

  // Roving focus: one date is tabbable, arrows move between them.
  const [focusedDate, setFocusedDate] = useState<string | null>(null);
  const focusedCellRef = useRef<HTMLButtonElement | null>(null);
  const shouldRestoreFocus = useRef(false);
  const forcePickerRef = useRef(false);

  useEffect(() => {
    setFocusedDate((current) => current ?? selectedDate);
  }, [selectedDate]);

  useEffect(() => {
    if (shouldRestoreFocus.current && focusedCellRef.current) {
      focusedCellRef.current.focus();
      shouldRestoreFocus.current = false;
    }
  }, [focusedDate]);

  const onGridKeyDown = (event: React.KeyboardEvent) => {
    const steps: Record<string, number> = {
      ArrowLeft: -1,
      ArrowRight: 1,
      ArrowUp: -7,
      ArrowDown: 7,
    };
    const step = steps[event.key];
    if (!step) return;
    event.preventDefault();

    const from = focusedDate ?? selectedDate;
    if (!from) return;
    const [y, m, d] = from.split('-').map(Number);
    const moved = new Date(Date.UTC(y, m - 1, d + step));
    const next = moved.toISOString().slice(0, 10);

    // Follow the date across a month boundary rather than dead-ending.
    if (moved.getUTCMonth() !== m - 1 || moved.getUTCFullYear() !== y) {
      setView({ year: moved.getUTCFullYear(), month: moved.getUTCMonth() });
    }
    shouldRestoreFocus.current = true;
    setFocusedDate(next);
  };

  const shiftMonth = (delta: number) => {
    setView(({ year, month }) => {
      const next = new Date(Date.UTC(year, month + delta, 1));
      return { year: next.getUTCFullYear(), month: next.getUTCMonth() };
    });
  };

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!selectedSlot || submitting) return;

    setSubmitting(true);
    setError(null);
    try {
      const identity = internalBooker && user
        ? { name: bookerDisplayName(user), email: user.email.trim(), phone: '' }
        : {
            name: form.name.trim(),
            email: form.email.trim(),
            phone: form.phone.trim(),
          };
      const result = await PublicBookingAPI.createBooking(
        orgSlug,
        linkSlug,
        {
          ...identity,
          start: selectedSlot.start,
          notes: form.notes.trim(),
        },
        internalBooker ? token : undefined,
      );
      const confirmation = {
        start: result.start,
        end: result.end,
        title: result.title,
        // Absolute: this travels into the guest's own calendar app, where a
        // relative path means nothing.
        url: result.cancel_token
          ? `${window.location.origin}/book/${encodeURIComponent(orgSlug)}/${encodeURIComponent(
              linkSlug,
            )}/cancel?token=${encodeURIComponent(result.cancel_token)}`
          : undefined,
        description:
          [link?.description?.trim(), link?.owner_name && `With ${link.owner_name}`]
            .filter(Boolean)
            .join('\n\n') || undefined,
      };
      const nextFeed = result.feed_url || '';
      setConfirmation(confirmation);
      setFeedUrl(nextFeed);
      setConfirmedScope(internalBooker ? bookerScope : null);
      setStage('booked');
      saveBookingConfirmation(orgSlug, linkSlug, {
        confirmation,
        feedUrl: nextFeed,
        bookerScope: internalBooker ? bookerScope : undefined,
      });
    } catch (err) {
      const statusCode = (err as { response?: { status?: number } })?.response?.status;
      if (statusCode === 409) {
        setError('That time is no longer available. Please pick another.');
        setSelectedSlot(null);
        setStage('picking');
        void load();
      } else if (statusCode === 401) {
        setError('Your session expired. Sign in again before booking.');
      } else if (statusCode === 429) {
        setError('Too many booking attempts. Please try again later.');
      } else {
        setError(errorMessage(err, 'Could not complete the booking.'));
      }
    } finally {
      setSubmitting(false);
    }
  };

  if (stage === 'loading') {
    return (
      <div
        className="flex min-h-[60vh] items-center justify-center"
        data-testid="booking-loading"
      >
        <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
      </div>
    );
  }

  if (stage === 'missing') {
    return (
      <div
        className="mx-auto mt-24 max-w-md rounded-2xl border border-gray-200 bg-white p-8 text-center shadow-sm"
        data-testid="booking-missing"
      >
        <CalendarDays className="mx-auto h-8 w-8 text-gray-300" />
        <h1 className="mt-4 text-lg font-semibold text-gray-900">Link unavailable</h1>
        <p className="mt-2 text-sm text-gray-500">
          This booking link doesn&apos;t exist or is no longer accepting bookings.
        </p>
      </div>
    );
  }

  if (stage === 'booked' && confirmation) {
    return (
      <div
        className="mx-auto mt-24 max-w-md rounded-2xl border border-gray-200 bg-white p-8 text-center shadow-sm animate-in fade-in zoom-in-95 duration-300"
        data-testid="booking-confirmed"
      >
        <button
          type="button"
          onClick={resumePicker}
          data-testid="booking-back-to-slots"
          className="mb-4 flex items-center gap-1.5 text-xs font-medium text-gray-500 hover:text-gray-900"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Back
        </button>
        <CheckCircle2 className="mx-auto h-10 w-10 text-emerald-500" />
        <h1 className="mt-4 text-lg font-semibold text-gray-900">You&apos;re booked</h1>
        <p className="mt-2 text-sm text-gray-600">{confirmation.title}</p>
        <p className="mt-1 text-sm font-medium text-gray-900">
          {formatDayLabel(confirmation.start, timeZone)} at{' '}
          {formatTime(confirmation.start, timeZone)}
        </p>
        <p className="mt-1 text-xs text-gray-400">{timezoneLabel(timeZone)}</p>
        {hasHydrated ? (
          <p
            data-testid="booking-confirmed-next-step"
            className="mt-4 rounded-lg border border-[#3CCED7]/40 bg-[#3CCED7]/10 px-3 py-2.5 text-sm font-medium leading-relaxed text-[#0E8A96]"
          >
            {internalBooker
              ? `This meeting is already on your ${
                  (confirmedScope ?? bookerScope) === 'team' ? 'team' : 'personal'
                } calendar. You can reschedule or cancel it from Calendar.`
              : 'You can cancel or pick another time on this page.'}
          </p>
        ) : null}

        {/*
          Subscribe first: a saved .ics is a snapshot and never learns the
          meeting was called off. The feed token is in the path because
          Outlook desktop drops ?query= on internet calendars.
        */}
        <div className="mt-6 border-t border-gray-100 pt-5">
          {feedUrl && (
            <div className="text-left">
              <p className="text-xs font-medium text-gray-600">
                Subscribe, and it stays up to date
              </p>
              <p className="mt-1 text-[11px] leading-relaxed text-gray-400">
                Copy the link into your calendar app&apos;s subscription option.
                If webcal does not work, try the HTTP link there.
              </p>
              <p className="mt-1 text-[11px] leading-relaxed text-gray-400">
                Changes appear when your calendar app refreshes the subscription.
                Cancelled meetings may stay visible as cancelled entries.
              </p>
              <label className="mt-3 flex items-center gap-2 text-xs text-gray-600">
                Link format
                <select
                  value={feedProtocol}
                  disabled={copyingFeed}
                  onChange={(event) => {
                    setFeedProtocol(event.target.value as 'webcal' | 'http');
                    setFeedCopyResult(null);
                  }}
                  data-testid="subscription-protocol"
                  className="rounded-md border border-gray-200 bg-white px-2 py-1.5 text-xs text-gray-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#3CCED7]"
                >
                  <option value="webcal">webcal://</option>
                  <option value="http">{httpFeedUrl.startsWith('https:') ? 'https://' : 'http://'}</option>
                </select>
              </label>
              <div className="mt-2 flex items-center gap-2">
                <code
                  data-testid="subscription-url"
                  className="min-w-0 flex-1 select-all break-all rounded-md bg-gray-50 px-2.5 py-1.5 text-[11px] text-gray-600"
                >
                  {subscriptionUrl}
                </code>
                <button
                  type="button"
                  onClick={() => void copySubscriptionUrl()}
                  disabled={copyingFeed}
                  data-testid="copy-subscription-url"
                  className="shrink-0 rounded-md border border-gray-200 px-2.5 py-1.5 text-[11px] font-medium text-gray-700 transition-colors hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#3CCED7] focus-visible:ring-offset-2 disabled:opacity-50"
                >
                  {copyingFeed ? 'Copying…' : 'Copy'}
                </button>
              </div>
              {feedCopyResult?.url === subscriptionUrl && (
                <p
                  role={feedCopyResult.success ? 'status' : 'alert'}
                  data-testid="subscription-copy-feedback"
                  className={`mt-2 text-xs ${feedCopyResult.success ? 'text-emerald-600' : 'text-red-600'}`}
                >
                  {feedCopyResult.success
                    ? 'Link copied. Paste it into your calendar app to subscribe.'
                    : 'Could not copy the link. Select and copy the full link above manually.'}
                </p>
              )}
            </div>
          )}

          <div className={feedUrl ? 'mt-4 border-t border-gray-100 pt-4' : undefined}>
            <p className="text-xs text-gray-500">Save a snapshot</p>
            <button
              type="button"
              onClick={() => downloadIcs(confirmation)}
              data-testid="download-ics"
              className="mt-3 flex w-full items-center justify-center gap-2 rounded-lg border border-gray-200 px-4 py-2.5 text-sm font-semibold text-gray-700 transition-colors hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#3CCED7] focus-visible:ring-offset-2"
            >
              <Download className="h-4 w-4 text-gray-400" />
              .ics file
            </button>
            <p className="mt-3 text-[11px] leading-relaxed text-gray-400">
              A downloaded file will not update if the meeting is cancelled.
              Classic Outlook can open it; the new Outlook app often cannot.
            </p>
          </div>
          {/*
            The same link is written into the .ics, so a guest who closes this
            tab can still get back here from their own calendar entry.
          */}
          <div className="mt-4 flex flex-col items-center gap-2">
            <button
              type="button"
              onClick={resumePicker}
              data-testid="book-another"
              className="rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#3CCED7] focus-visible:ring-offset-2"
            >
              Book another time
            </button>
            {confirmation.url && (
              <a
                href={confirmation.url}
                data-testid="confirmation-cancel-link"
                className="inline-block text-xs text-gray-400 underline underline-offset-2 transition-colors hover:text-gray-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#3CCED7] focus-visible:ring-offset-2"
              >
                Need to cancel?
              </a>
            )}
          </div>
        </div>
      </div>
    );
  }

  if (stage === 'finding') {
    return (
      <BookingFind
        orgSlug={orgSlug}
        linkSlug={linkSlug}
        timeZone={timeZone}
        onBack={() => setStage('picking')}
      />
    );
  }

  return (
    <div className="mx-auto max-w-5xl px-4 py-10" data-testid="booking-widget">
      <div className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm md:grid md:grid-cols-[300px_1fr]">
        {/* Who and what — fixed context while the visitor browses dates. */}
        <aside className="border-b border-gray-100 p-6 md:border-b-0 md:border-r">
          <p className="flex items-center gap-1.5 text-sm text-gray-500">
            <User className="h-3.5 w-3.5 text-gray-400" />
            {link?.owner_name}
          </p>
          <h1 className="mt-1 text-xl font-semibold text-gray-900">{link?.title}</h1>
          <p className="mt-3 flex items-center gap-1.5 text-sm text-gray-600">
            <Clock className="h-4 w-4 text-gray-400" />
            {link?.duration_minutes} minutes
          </p>
          {link?.description && (
            <p className="mt-3 border-t border-gray-100 pt-3 text-sm leading-relaxed text-gray-600">
              {link.description}
            </p>
          )}

          <div className="mt-6 border-t border-gray-100 pt-4">
            <label
              htmlFor="booking-timezone"
              className="flex items-center gap-1.5 text-xs font-medium text-gray-500"
            >
              <Globe className="h-3.5 w-3.5 text-gray-400" />
              Times shown in
            </label>
            <select
              id="booking-timezone"
              value={timeZone}
              onChange={(e) => setTimeZone(e.target.value)}
              data-testid="booking-timezone"
              className="mt-1.5 w-full rounded-lg border border-gray-200 bg-white px-2 py-1.5 text-xs text-gray-700 focus:border-gray-400 focus:outline-none"
            >
              {Array.from(new Set([timeZone, ...TIMEZONE_CHOICES])).map((zone) => (
                <option key={zone} value={zone}>
                  {zone}
                </option>
              ))}
            </select>
          </div>
        </aside>

        <main className="p-6">
          {error && (
            <div
              className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-2 text-sm text-amber-800"
              data-testid="booking-error"
            >
              {error}
            </div>
          )}

          <AnimatePresence mode="wait" initial={false}>
          {stage === 'confirming' && selectedSlot ? (
            <motion.form
              key="confirm"
              onSubmit={handleSubmit}
              data-testid="booking-form"
              initial={{ opacity: 0, x: 24 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 24 }}
              transition={{ duration: 0.22, ease: 'easeOut' }}
            >
              <button
                type="button"
                onClick={() => {
                  setSelectedSlot(null);
                  setStage('picking');
                }}
                className="mb-4 flex items-center gap-1.5 text-xs font-medium text-gray-500 hover:text-gray-900"
              >
                <ArrowLeft className="h-3.5 w-3.5" />
                Back
              </button>

              <p className="mb-4 rounded-lg bg-gray-50 px-3 py-2 text-sm text-gray-700">
                <span className="font-medium text-gray-900">
                  {formatDayLabel(selectedSlot.start, timeZone)}
                </span>{' '}
                at{' '}
                <span className="font-medium text-gray-900">
                  {formatTime(selectedSlot.start, timeZone)}
                </span>
              </p>

              <div className="space-y-3">
                {showGuestFields ? (
                  <>
                    <div>
                      <label htmlFor="booking-name" className="block text-xs font-medium text-gray-600">
                        Name
                      </label>
                      <input
                        id="booking-name"
                        required
                        value={form.name}
                        onChange={(e) => setForm({ ...form, name: e.target.value })}
                        data-testid="booking-name"
                        className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-gray-400 focus:outline-none"
                      />
                    </div>
                    <div>
                      <label htmlFor="booking-email" className="block text-xs font-medium text-gray-600">
                        Email
                      </label>
                      <input
                        id="booking-email"
                        type="email"
                        required
                        value={form.email}
                        onChange={(e) => setForm({ ...form, email: e.target.value })}
                        data-testid="booking-email"
                        className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-gray-400 focus:outline-none"
                      />
                    </div>
                    <div>
                      <label htmlFor="booking-phone" className="block text-xs font-medium text-gray-600">
                        Phone <span className="font-normal text-gray-400">(optional)</span>
                      </label>
                      {/*
                        Optional deliberately: a number helps the host reach you if
                        email bounces, but requiring one loses bookings.
                      */}
                      <input
                        id="booking-phone"
                        type="tel"
                        autoComplete="tel"
                        value={form.phone}
                        onChange={(e) => setForm({ ...form, phone: e.target.value })}
                        data-testid="booking-phone"
                        className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-gray-400 focus:outline-none"
                      />
                    </div>
                  </>
                ) : null}
                {internalBooker ? (
                  <div>
                    <p className="block text-xs font-medium text-gray-600">Calendar</p>
                    <div className="mt-1">
                      <CalendarScopeSwitch
                        scope={bookerScope}
                        onChange={(scope) => {
                          if (
                            scope === 'team' &&
                            !bookerCanUseTeamScope(Boolean(link?.same_project))
                          ) {
                            setSameProjectPrompt(true);
                            return;
                          }
                          setBookerScope(scope);
                        }}
                        testIdPrefix="booking-booker-scope"
                      />
                    </div>
                  </div>
                ) : null}
                <div>
                  <label htmlFor="booking-notes" className="block text-xs font-medium text-gray-600">
                    Anything to share beforehand?
                  </label>
                  <textarea
                    id="booking-notes"
                    rows={3}
                    value={form.notes}
                    onChange={(e) => setForm({ ...form, notes: e.target.value })}
                    data-testid="booking-notes"
                    className="mt-1 w-full rounded-lg border border-gray-200 px-3 py-2 text-sm focus:border-gray-400 focus:outline-none"
                  />
                </div>
              </div>

              <button
                type="submit"
                disabled={submitting || !hasHydrated}
                data-testid="booking-submit"
                className="mt-4 w-full rounded-lg bg-[#3CCED7] px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-[#2AB5BD] disabled:opacity-60"
              >
                {submitting ? 'Confirming…' : 'Confirm booking'}
              </button>
            </motion.form>
          ) : (
            <motion.div
              key="picker"
              initial={{ opacity: 0, x: -16 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -16 }}
              transition={{ duration: 0.22, ease: 'easeOut' }}
            >
              <BookingExistingReminder bookings={viewerBookings} timeZone={timeZone} />
              <h2 className="mb-4 text-base font-semibold text-gray-900">
                Select a date &amp; time
              </h2>
              <div className="grid gap-6 sm:grid-cols-[minmax(0,1fr)_190px]">
              {/* Month picker: dates first, times second — a flat list of every
                  slot across weeks is unreadable. */}
              <div>
                <div className="mb-3 flex items-center justify-between">
                  <h3 className="text-sm font-semibold text-gray-900">
                    {monthLabel(view.year, view.month)}
                  </h3>
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => shiftMonth(-1)}
                      aria-label="Previous month"
                      data-testid="booking-prev-month"
                      className="rounded-lg p-1.5 text-gray-400 hover:bg-gray-50 hover:text-gray-700"
                    >
                      <ChevronLeft className="h-4 w-4" />
                    </button>
                    <button
                      type="button"
                      onClick={() => shiftMonth(1)}
                      aria-label="Next month"
                      data-testid="booking-next-month"
                      className="rounded-lg p-1.5 text-gray-400 hover:bg-gray-50 hover:text-gray-700"
                    >
                      <ChevronRight className="h-4 w-4" />
                    </button>
                  </div>
                </div>

                {/* role="grid" with arrow-key movement and a roving tabindex:
                    the WAI-ARIA date-picker pattern. Without it this is a pile
                    of 30-odd tab stops with no date announced. */}
                <div
                  role="grid"
                  aria-label={monthLabel(view.year, view.month)}
                  onKeyDown={onGridKeyDown}
                  className="text-center"
                >
                  <div role="row" className="grid grid-cols-7 gap-1">
                    {heads.map((head) => (
                      <span
                        key={head}
                        role="columnheader"
                        aria-label={head}
                        className="py-1 text-[11px] font-medium text-gray-400"
                      >
                        {head}
                      </span>
                    ))}
                  </div>
                  {weeks.map((week, weekIndex) => (
                    <div role="row" key={weekIndex} className="grid grid-cols-7 gap-1">
                      {week.map((cell, cellIndex) => {
                        if (!cell.date) {
                          return <span role="gridcell" key={`pad-${weekIndex}-${cellIndex}`} />;
                        }
                        const available = slotsByDate.has(cell.date);
                        const isSelected = cell.date === selectedDate;
                        const isToday = cell.date === today;
                        const isFocusTarget = cell.date === focusedDate;
                        return (
                          <div
                            role="gridcell"
                            aria-selected={isSelected}
                            key={cell.date}
                            className="flex flex-col items-center justify-start"
                          >
                            <button
                              type="button"
                              disabled={!available}
                              ref={(node) => {
                                if (isFocusTarget) focusedCellRef.current = node;
                              }}
                              tabIndex={isFocusTarget ? 0 : -1}
                              onClick={() => {
                                setSelectedDate(cell.date);
                                setFocusedDate(cell.date);
                                setSelectedSlot(null);
                              }}
                              data-testid={
                                available ? 'booking-date-available' : 'booking-date'
                              }
                              data-date={cell.date}
                              aria-label={`${formatDayLabel(`${cell.date}T12:00:00Z`, 'UTC')}${
                                available ? '' : ', unavailable'
                              }`}
                              className={`flex h-10 w-10 items-center justify-center rounded-full text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-[#3CCED7] focus-visible:ring-offset-2 transition-colors ${
                                isSelected
                                  ? 'bg-[#3CCED7] font-semibold text-white shadow-sm'
                                  : available
                                    ? 'bg-[#3CCED7]/10 font-semibold text-[#0E8A96] hover:bg-[#3CCED7]/25'
                                    : 'text-gray-300'
                              }`}
                            >
                              {cell.dayOfMonth}
                            </button>
                            <span
                              className={`mt-1 h-1 w-1 rounded-full ${
                                isToday ? 'bg-[#3CCED7]' : 'bg-transparent'
                              }`}
                            />
                          </div>
                        );
                      })}
                    </div>
                  ))}
                </div>
              </div>

              {/* Times for the chosen day only. */}
              <div data-testid="booking-slots" aria-live="polite">
                <AnimatePresence mode="wait" initial={false}>
                {selectedDate ? (
                  <motion.div
                    key={selectedDate}
                    initial={{ opacity: 0, x: 12 }}
                    animate={{ opacity: 1, x: 0 }}
                    exit={{ opacity: 0, x: -12 }}
                    transition={{ duration: 0.2, ease: 'easeOut' }}
                  >
                    <h3 className="sticky top-0 z-10 mb-3 bg-white pb-2 text-sm font-semibold text-gray-900">
                      {formatDayLabel(`${selectedDate}T12:00:00Z`, 'UTC')}
                      <span className="block text-xs font-normal text-gray-400">
                        {new Date(`${selectedDate}T12:00:00Z`).getUTCFullYear()}
                      </span>
                    </h3>
                    <div
                      key={selectedDate}
                      // overflow-x-hidden is load-bearing: setting overflow-y makes the other
                      // axis compute to auto, so the slide-in transform briefly
                      // overflows and flashes a horizontal scrollbar.
                      className="flex max-h-[22rem] flex-col gap-2 overflow-y-auto overflow-x-hidden pr-1"
                    >
                      {periods.map((period) => {
                        const isFolded = foldedPeriods.includes(period.key);
                        return (
                        <div key={period.key} className="flex flex-col gap-2">
                          <button
                            type="button"
                            onClick={() => togglePeriod(period.key)}
                            aria-expanded={!isFolded}
                            data-testid="booking-period-toggle"
                            data-period={period.key}
                            className="flex items-center gap-1 pt-1 text-[11px] font-semibold uppercase tracking-wide text-gray-400 transition-colors hover:text-gray-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-[#3CCED7] focus-visible:ring-offset-2"
                          >
                            <ChevronRight
                              className={`h-3 w-3 transition-transform duration-200 ${
                                isFolded ? '' : 'rotate-90'
                              }`}
                            />
                            {period.label}
                            <span className="font-normal normal-case text-gray-300">
                              ({period.slots.length})
                            </span>
                          </button>
                          <AnimatePresence initial={false}>
                          {!isFolded && (
                          <motion.div
                            key="slots"
                            initial={{ height: 0, opacity: 0 }}
                            animate={{ height: 'auto', opacity: 1 }}
                            exit={{ height: 0, opacity: 0 }}
                            transition={{ duration: 0.2, ease: 'easeOut' }}
                            className="flex flex-col gap-2 overflow-hidden"
                          >
                          {period.slots.map((slot) => {
                            const isChosen = selectedSlot?.start === slot.start;
                            return (
                              <div key={slot.start} className="flex gap-2">
                                <button
                                  type="button"
                                  onClick={() => setSelectedSlot(isChosen ? null : slot)}
                                  data-testid="booking-slot"
                                  data-slot-start={slot.start}
                                  aria-pressed={isChosen}
                                  className={`min-w-0 flex-1 rounded-lg py-2.5 text-sm font-semibold focus:outline-none focus-visible:ring-2 focus-visible:ring-[#3CCED7] focus-visible:ring-offset-2 transition-all duration-300 ease-out ${
                                    isChosen
                                      ? 'bg-gray-100 text-gray-500'
                                      : 'bg-[#2FC4B2]/10 text-[#0E857A] hover:bg-[#2FC4B2]/25'
                                  }`}
                                >
                                  {formatTime(slot.start, timeZone)}
                                </button>
                                {isChosen && (
                                  <button
                                    type="button"
                                    onClick={() => {
                                      setBookerScope('personal');
                                      setSameProjectPrompt(false);
                                      setStage('confirming');
                                      setError(null);
                                    }}
                                    data-testid="booking-next"
                                    className="min-w-0 flex-1 rounded-lg bg-[#3CCED7] py-2.5 text-sm font-semibold text-white shadow-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-[#3CCED7] focus-visible:ring-offset-2 transition-colors hover:bg-[#2AB5BD] animate-in fade-in slide-in-from-right-3 zoom-in-95 duration-300 ease-out"
                                  >
                                    Next
                                  </button>
                                )}
                              </div>
                            );
                          })}
                          </motion.div>
                          )}
                          </AnimatePresence>
                        </div>
                        );
                      })}
                    </div>
                  </motion.div>
                ) : (
                  <motion.div
                    key="placeholder"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    transition={{ duration: 0.2 }}
                    className="flex h-full flex-col items-center justify-center rounded-lg border border-dashed border-gray-200 p-6 text-center">
                    <CalendarDays className="h-6 w-6 text-gray-300" />
                    <p className="mt-2 text-xs text-gray-500">
                      {dayGroups.length === 0
                        ? 'No times are available this month.'
                        : 'Pick a date to see available times.'}
                    </p>
                  </motion.div>
                )}
                </AnimatePresence>
                </div>
              </div>
            </motion.div>
          )}
          </AnimatePresence>
        </main>
      </div>
      <p className="mt-4 text-center">
        <button
          type="button"
          onClick={() => {
            setError(null);
            setStage('finding');
          }}
          data-testid="booking-find-open"
          className="text-xs text-gray-400 underline underline-offset-2 hover:text-gray-600"
        >
          Already booked? Find your booking
        </button>
      </p>
      <ConfirmDialog
        isOpen={sameProjectPrompt}
        type="info"
        title="Same project"
        message={SAME_PROJECT_TEAM_MESSAGE}
        confirmText="Use personal calendar"
        cancelText="Got it"
        onConfirm={() => setSameProjectPrompt(false)}
        onCancel={() => setSameProjectPrompt(false)}
      />
    </div>
  );
}
