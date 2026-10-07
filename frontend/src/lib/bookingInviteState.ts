import type { NotificationItem } from '@/types/notifications';

export type BookingInvitePhase = 'pending' | 'booked' | 'declined' | 'closed';

function meta(notification: NotificationItem): Record<string, unknown> {
  return (notification.metadata || {}) as Record<string, unknown>;
}

export function bookingInviteIsBooked(notification: NotificationItem): boolean {
  return Boolean(meta(notification).booked);
}

export function bookingInviteCanRebook(notification: NotificationItem): boolean {
  return meta(notification).can_rebook !== false;
}

export function bookingInvitePhase(notification: NotificationItem): BookingInvitePhase {
  const data = meta(notification);
  if (data.can_rebook === false) return 'closed';
  if (data.booked) return 'booked';
  if (notification.responded && notification.response === 'reject') return 'declined';
  return 'pending';
}

/** Footer / card CTA: never send someone without a slot (or without permission) to pick. */
export function bookingInvitePickLabel(
  notification: NotificationItem,
): 'Pick a time' | 'Change time' | null {
  const phase = bookingInvitePhase(notification);
  if (phase === 'closed' || phase === 'declined') return null;
  if (phase === 'booked') return 'Change time';
  return 'Pick a time';
}

export function bookingInviteSlot(notification: NotificationItem): {
  start: string;
  end: string;
  cancelToken: string;
} | null {
  const data = meta(notification);
  const start = typeof data.start === 'string' ? data.start : '';
  const end = typeof data.end === 'string' ? data.end : '';
  const cancelToken = typeof data.cancel_token === 'string' ? data.cancel_token : '';
  if (!start) return null;
  return { start, end, cancelToken };
}

export function formatBookingSlot(start: string, end?: string): string {
  const from = new Date(start);
  if (Number.isNaN(from.getTime())) return start;
  const date = from.toLocaleDateString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  });
  const fromTime = from.toLocaleTimeString(undefined, {
    hour: 'numeric',
    minute: '2-digit',
  });
  if (!end) return `${date} · ${fromTime}`;
  const to = new Date(end);
  if (Number.isNaN(to.getTime())) return `${date} · ${fromTime}`;
  const toTime = to.toLocaleTimeString(undefined, {
    hour: 'numeric',
    minute: '2-digit',
  });
  return `${date} · ${fromTime} – ${toTime}`;
}
