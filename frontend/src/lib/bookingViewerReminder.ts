export type ViewerBookingReminder = {
  start: string;
  end: string;
  title: string;
};

/**
 * Signed-in availability is the source of truth. Guests have no account on
 * the payload, so a just-confirmed booking can still be shown from this tab.
 */
export function resolveViewerBookings({
  fromApi,
  fromSession,
  signedIn,
}: {
  fromApi?: ViewerBookingReminder[] | null;
  fromSession?: ViewerBookingReminder[] | null;
  signedIn: boolean;
}): ViewerBookingReminder[] {
  if (fromApi && fromApi.length > 0) return fromApi;
  if (signedIn) return [];
  return fromSession ?? [];
}
