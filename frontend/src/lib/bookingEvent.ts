export const BOOKING_EVENT_SOURCE = "booking_link";

type EventLike = {
  metadata?: { source?: string | null } | null;
};

export function isBookingEvent(event: EventLike | null | undefined): boolean {
  return event?.metadata?.source === BOOKING_EVENT_SOURCE;
}
