export const UPCOMING_MEETINGS_PANEL_STORAGE_KEY = 'dashboard-upcoming-meetings-panel-open';

export function normalizeUpcomingMeetingsPanelOpen(value: string | null | undefined): boolean {
  if (value === 'false') return false;
  if (value === 'true') return true;
  return true;
}

/**
 * Read the saved preference in the browser. Client-only layouts use this for
 * their initial state; server layouts read the mirrored cookie instead.
 */
export function readStoredUpcomingMeetingsPanelOpen(): boolean {
  if (typeof window === 'undefined') return true;
  try {
    return normalizeUpcomingMeetingsPanelOpen(window.localStorage.getItem(UPCOMING_MEETINGS_PANEL_STORAGE_KEY));
  } catch {
    return true;
  }
}
