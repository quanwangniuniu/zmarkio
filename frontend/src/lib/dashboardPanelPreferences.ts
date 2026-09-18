export const UPCOMING_MEETINGS_PANEL_STORAGE_KEY = 'dashboard-upcoming-meetings-panel-open';

export function normalizeUpcomingMeetingsPanelOpen(value: string | null | undefined): boolean {
  if (value === 'false') return false;
  if (value === 'true') return true;
  return true;
}

/**
 * Client-side counterpart to the server cookie read in `(project)/layout.tsx`.
 * Returns the persisted panel preference, defaulting to open.
 */
export function readUpcomingMeetingsPanelOpen(): boolean {
  // guard for server-side rendering
  if (typeof window === 'undefined') {
    return true;
  }
  // read the value from localStorage and use helper function to normalise it
  return normalizeUpcomingMeetingsPanelOpen(
    window.localStorage.getItem(UPCOMING_MEETINGS_PANEL_STORAGE_KEY)
  );
}