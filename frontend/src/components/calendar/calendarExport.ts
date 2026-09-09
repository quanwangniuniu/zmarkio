/**
 * "Add to calendar" for the public booking confirmation.
 *
 * Entirely client-side: the confirmation response already carries everything a
 * calendar entry needs, so this needs no endpoint, no auth, and works for an
 * anonymous prospect who will never have an account here.
 *
 * Pure functions — the ICS text format is fiddly enough (CRLF, escaping, UTC
 * stamps) to be worth testing without a browser.
 */

export interface CalendarEntry {
  title: string;
  /** ISO 8601 instants. */
  start: string;
  end: string;
  description?: string;
  /**
   * Travels into the guest's own calendar as the entry's URL, which is how
   * someone who closed the confirmation tab can still find their way back.
   */
  url?: string;
}

/** ICS and Google both want YYYYMMDDTHHMMSSZ. */
export function toCompactUtc(iso: string): string {
  return new Date(iso).toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '');
}

/**
 * Escape a value for an ICS text field.
 * Order matters: backslashes first, or later escapes get double-escaped.
 */
export function escapeIcsText(value: string): string {
  return value
    .replace(/\\/g, '\\\\')
    .replace(/;/g, '\\;')
    .replace(/,/g, '\\,')
    .replace(/\r\n|\r|\n/g, '\\n');
}

/**
 * A one-event VCALENDAR.
 *
 * Lines are joined with CRLF because RFC 5545 requires it — Outlook in
 * particular rejects LF-only files.
 */
export function buildIcs(entry: CalendarEntry, uid?: string): string {
  const stamp = toCompactUtc(new Date().toISOString());
  const lines = [
    'BEGIN:VCALENDAR',
    'VERSION:2.0',
    'PRODID:-//Marketing Simplified//Booking//EN',
    'CALSCALE:GREGORIAN',
    'METHOD:PUBLISH',
    'BEGIN:VEVENT',
    `UID:${uid ?? `${toCompactUtc(entry.start)}-${Math.random().toString(36).slice(2)}@marketing-simplified`}`,
    `DTSTAMP:${stamp}`,
    `DTSTART:${toCompactUtc(entry.start)}`,
    `DTEND:${toCompactUtc(entry.end)}`,
    `SUMMARY:${escapeIcsText(entry.title)}`,
  ];
  if (entry.description) {
    lines.push(`DESCRIPTION:${escapeIcsText(entry.description)}`);
  }
  if (entry.url) {
    lines.push(`URL:${escapeIcsText(entry.url)}`);
  }
  lines.push('END:VEVENT', 'END:VCALENDAR');
  // RFC 5545: the stream ends with CRLF. Outlook rejects a file that doesn't.
  return `${lines.join('\r\n')}\r\n`;
}

/** Google's prefilled-event URL. No auth, no API — just a link. */
export function googleCalendarUrl(entry: CalendarEntry): string {
  const params = new URLSearchParams({
    action: 'TEMPLATE',
    text: entry.title,
    dates: `${toCompactUtc(entry.start)}/${toCompactUtc(entry.end)}`,
  });
  // Google's template has no URL field, so the link rides along in details.
  const details = [entry.description, entry.url].filter(Boolean).join('\n\n');
  if (details) params.set('details', details);
  return `https://calendar.google.com/calendar/render?${params.toString()}`;
}

/** Outlook wants YYYY-MM-DDTHH:mm:SSZ, not the compact Google stamp. */
export function toOutlookUtc(iso: string): string {
  return new Date(iso).toISOString().replace(/\.\d{3}/, '');
}

/** Outlook on the web's prefilled-event URL. Same idea as Google's template. */
export function outlookCalendarUrl(entry: CalendarEntry): string {
  const params = new URLSearchParams({
    path: '/calendar/action/compose',
    rru: 'addevent',
    subject: entry.title,
    startdt: toOutlookUtc(entry.start),
    enddt: toOutlookUtc(entry.end),
  });
  const body = [entry.description, entry.url].filter(Boolean).join('\n\n');
  if (body) params.set('body', body);
  return `https://outlook.live.com/calendar/0/deeplink/compose?${params.toString()}`;
}

/** Filename-safe slug for the downloaded file. */
export function icsFileName(title: string): string {
  const slug =
    title
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 60) || 'booking';
  return `${slug}.ics`;
}

/**
 * Hand the .ics to the browser as a download.
 *
 * Uses a Blob rather than a data: URI so large descriptions can't hit URL
 * length limits, and revokes the object URL afterwards.
 */
export function downloadIcs(entry: CalendarEntry): void {
  if (typeof window === 'undefined') return;
  const blob = new Blob([buildIcs(entry)], {
    type: 'text/calendar;charset=utf-8',
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = icsFileName(entry.title);
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}
