import type { BookingScope } from '@/lib/bookingLinkScope';
import type { CalendarEntry } from './calendarExport';

/**
 * The confirmation screen lives in React state on `/book/…`. Going to cancel
 * and then Back remounts the widget as a fresh picker unless we remember the
 * booking for this tab.
 */
export type StoredBookingConfirmation = {
  confirmation: CalendarEntry;
  feedUrl: string;
  bookerScope?: BookingScope;
};

function storageKey(orgSlug: string, linkSlug: string): string {
  return `booking-confirmation:${orgSlug}:${linkSlug}`;
}

export function saveBookingConfirmation(
  orgSlug: string,
  linkSlug: string,
  value: StoredBookingConfirmation,
): void {
  try {
    sessionStorage.setItem(storageKey(orgSlug, linkSlug), JSON.stringify(value));
  } catch {
    // Private mode or a full store: the cancel page still works without this.
  }
}

export function readBookingConfirmation(
  orgSlug: string,
  linkSlug: string,
): StoredBookingConfirmation | null {
  try {
    const raw = sessionStorage.getItem(storageKey(orgSlug, linkSlug));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredBookingConfirmation;
    if (!parsed?.confirmation?.start || !parsed.confirmation.end || !parsed.confirmation.title) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

export function clearBookingConfirmation(orgSlug: string, linkSlug: string): void {
  try {
    sessionStorage.removeItem(storageKey(orgSlug, linkSlug));
  } catch {
    // ignore
  }
}

function reminderKey(orgSlug: string, linkSlug: string): string {
  return `booking-reminder:${orgSlug}:${linkSlug}`;
}

export type StoredViewerBooking = {
  start: string;
  end: string;
  title: string;
};

export function saveBookingReminder(
  orgSlug: string,
  linkSlug: string,
  bookings: StoredViewerBooking[],
): void {
  try {
    sessionStorage.setItem(reminderKey(orgSlug, linkSlug), JSON.stringify(bookings));
  } catch {
    // Private mode or a full store: the picker still works without this.
  }
}

export function readBookingReminder(
  orgSlug: string,
  linkSlug: string,
): StoredViewerBooking[] {
  try {
    const raw = sessionStorage.getItem(reminderKey(orgSlug, linkSlug));
    if (!raw) return [];
    const parsed = JSON.parse(raw) as StoredViewerBooking[];
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((item) => item?.start && item.end && item.title);
  } catch {
    return [];
  }
}

export function clearBookingReminder(orgSlug: string, linkSlug: string): void {
  try {
    sessionStorage.removeItem(reminderKey(orgSlug, linkSlug));
  } catch {
    // ignore
  }
}
