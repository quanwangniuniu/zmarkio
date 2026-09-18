import type { BookingLinkDTO } from '@/lib/api/calendarApi';

export function sortBookingLinks(links: BookingLinkDTO[]): BookingLinkDTO[] {
  return [...links].sort((left, right) => {
    const byCreated =
      Date.parse(right.created_at || '') - Date.parse(left.created_at || '');
    return byCreated || left.title.localeCompare(right.title);
  });
}

export function formatLinkCreatedAt(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleDateString('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  });
}

export function namedInvitees(
  link: BookingLinkDTO,
): { id: number | null; name: string; email: string }[] {
  return link.invitees ?? [];
}

const BOOKING_RULE_KEYS = [
  'duration_minutes',
  'slot_increment_minutes',
  'buffer_before_minutes',
  'buffer_after_minutes',
  'min_notice_minutes',
  'max_advance_days',
  'timezone',
] as const;

/** True when the host changed when people can book — not title, guests, or on/off. */
export function bookingRulesChanged(
  before: BookingLinkDTO,
  next: Partial<Pick<BookingLinkDTO, (typeof BOOKING_RULE_KEYS)[number]>>,
): boolean {
  return BOOKING_RULE_KEYS.some((key) => key in next && next[key] !== before[key]);
}
