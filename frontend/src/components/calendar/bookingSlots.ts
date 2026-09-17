import type { BookingSlotDTO } from '@/lib/api/calendarApi';

/**
 * Slot presentation helpers for the public booking widget.
 *
 * The API returns UTC instants. A prospect thinks in their own timezone, and
 * the owner's day boundaries are in theirs, so grouping and labelling are done
 * explicitly in a chosen zone rather than relying on the runtime default.
 *
 * Pure functions, so the date arithmetic is testable without rendering.
 */

export interface DayGroup {
  /** Calendar day in the display timezone, as YYYY-MM-DD. */
  date: string;
  /** e.g. "Tuesday, 1 September" */
  label: string;
  slots: BookingSlotDTO[];
}

/**
 * Zones offered in pickers, alongside whatever the browser reports.
 * Shared so the owner's form and the public widget never drift apart.
 */
export const TIMEZONE_CHOICES = [
  'UTC',
  'America/Los_Angeles',
  'America/New_York',
  'Europe/London',
  'Europe/Berlin',
  'Asia/Singapore',
  'Asia/Kolkata',
  'Australia/Sydney',
];

/** The visitor's timezone, falling back to UTC where unavailable. */
export function detectTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}

/** YYYY-MM-DD for an instant, as seen in `timeZone`. */
export function dayKey(iso: string, timeZone: string): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date(iso));

  const lookup = (type: string) => parts.find((p) => p.type === type)?.value ?? '';
  return `${lookup('year')}-${lookup('month')}-${lookup('day')}`;
}

/** e.g. "2:30 PM" */
export function formatTime(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat(undefined, {
    timeZone,
    hour: 'numeric',
    minute: '2-digit',
  }).format(new Date(iso));
}

/** e.g. "Tuesday, 1 September" */
export function formatDayLabel(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat(undefined, {
    timeZone,
    weekday: 'long',
    day: 'numeric',
    month: 'long',
  }).format(new Date(iso));
}

/**
 * Group slots into calendar days in `timeZone`, preserving chronological order.
 *
 * Grouping has to happen in the display zone: the same instant can fall on
 * different dates for the prospect and the owner, and slots near midnight would
 * otherwise land under the wrong heading.
 */
export function groupSlotsByDay(
  slots: BookingSlotDTO[],
  timeZone: string,
): DayGroup[] {
  const ordered = [...slots].sort((a, b) => a.start.localeCompare(b.start));
  const groups = new Map<string, DayGroup>();

  for (const slot of ordered) {
    const key = dayKey(slot.start, timeZone);
    const existing = groups.get(key);
    if (existing) {
      existing.slots.push(slot);
    } else {
      groups.set(key, {
        date: key,
        label: formatDayLabel(slot.start, timeZone),
        slots: [slot],
      });
    }
  }

  return Array.from(groups.values());
}

/** Short offset label for the timezone note, e.g. "GMT+10". */
export function timezoneLabel(timeZone: string): string {
  try {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone,
      timeZoneName: 'short',
    }).formatToParts(new Date());
    return parts.find((p) => p.type === 'timeZoneName')?.value ?? timeZone;
  } catch {
    return timeZone;
  }
}

// ── Month grid (public booking page) ─────────────────────────────────────

export interface MonthCell {
  /** YYYY-MM-DD in the display timezone, or null for padding cells. */
  date: string | null;
  dayOfMonth: number | null;
}

/** Today's date key in the display timezone. */
export function todayKey(timeZone: string): string {
  return dayKey(new Date().toISOString(), timeZone);
}

/**
 * The viewer's first day of week, 0=Sunday … 6=Saturday.
 *
 * Locale-driven: US calendars start on Sunday, most of Europe and AU on Monday.
 * `weekInfo` is not in every engine, so fall back to Monday.
 */
export function firstDayOfWeek(): number {
  try {
    const language = typeof navigator === 'undefined' ? 'en' : navigator.language;
    // `weekInfo` is a getter in some engines and `getWeekInfo()` in others, and
    // neither is in TypeScript's lib yet — read both off an unknown-typed value
    // rather than asserting a shape onto Intl.Locale.
    const locale = new Intl.Locale(language) as unknown as {
      weekInfo?: { firstDay?: number };
      getWeekInfo?: () => { firstDay?: number };
    };
    const info =
      typeof locale.getWeekInfo === 'function' ? locale.getWeekInfo() : locale.weekInfo;
    // Intl reports 1=Monday … 7=Sunday; we want 0=Sunday … 6=Saturday.
    if (info?.firstDay) return info.firstDay % 7;
  } catch {
    // Unsupported engine — fall through to Monday.
  }
  return 1;
}

/** Weekday column headings, ordered from `firstDay`. */
export function weekdayHeads(firstDay: number): string[] {
  const base = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  return Array.from({ length: 7 }, (_, i) => base[(firstDay + i) % 7]);
}

/**
 * Weeks of a month, padded with nulls, starting on `firstDay`.
 *
 * Built from plain Y/M/D arithmetic rather than timezone conversion: the grid
 * is a calendar of dates, not instants, so it must not shift when the viewer
 * changes zone. Only the slots themselves are zone-sensitive.
 */
export function buildMonthGrid(
  year: number,
  monthIndex: number,
  firstDay = 1,
): MonthCell[][] {
  const first = new Date(Date.UTC(year, monthIndex, 1));
  const daysInMonth = new Date(Date.UTC(year, monthIndex + 1, 0)).getUTCDate();
  // getUTCDay: 0=Sunday. Shift so the row starts on `firstDay`.
  const leading = (first.getUTCDay() - firstDay + 7) % 7;

  const cells: MonthCell[] = [];
  for (let i = 0; i < leading; i += 1) cells.push({ date: null, dayOfMonth: null });
  for (let day = 1; day <= daysInMonth; day += 1) {
    const mm = String(monthIndex + 1).padStart(2, '0');
    const dd = String(day).padStart(2, '0');
    cells.push({ date: `${year}-${mm}-${dd}`, dayOfMonth: day });
  }
  while (cells.length % 7 !== 0) cells.push({ date: null, dayOfMonth: null });

  const weeks: MonthCell[][] = [];
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
  return weeks;
}

/** e.g. "September 2026" */
export function monthLabel(year: number, monthIndex: number): string {
  return new Intl.DateTimeFormat(undefined, { month: 'long', year: 'numeric' }).format(
    new Date(Date.UTC(year, monthIndex, 1)),
  );
}

/** Inclusive UTC bounds covering a whole month, for the availability query. */
export function monthRange(year: number, monthIndex: number): { from: Date; to: Date } {
  return {
    from: new Date(Date.UTC(year, monthIndex, 1)),
    to: new Date(Date.UTC(year, monthIndex + 1, 1)),
  };
}

// ── Time-of-day grouping ─────────────────────────────────────────────────

export interface SlotPeriod {
  key: string;
  label: string;
  slots: BookingSlotDTO[];
}

/**
 * Bands covering the full 24 hours, in chronological order.
 * `startHour` is inclusive; each band runs until the next one begins.
 */
const PERIODS: { key: string; label: string; startHour: number }[] = [
  { key: 'early-morning', label: 'Early morning', startHour: 0 },
  { key: 'morning', label: 'Morning', startHour: 6 },
  { key: 'noon', label: 'Noon', startHour: 12 },
  { key: 'afternoon', label: 'Afternoon', startHour: 13 },
  { key: 'evening', label: 'Evening', startHour: 17 },
  { key: 'night', label: 'Night', startHour: 21 },
];

/** The hour of a slot as seen in `timeZone`. */
function hourIn(iso: string, timeZone: string): number {
  const value = new Intl.DateTimeFormat('en-GB', {
    timeZone,
    hour: '2-digit',
    hour12: false,
  }).format(new Date(iso));
  // en-GB renders midnight as "24" in some engines.
  return Number(value) % 24;
}

/** Which band a slot falls into, in the display timezone. */
export function slotPeriod(iso: string, timeZone: string): { key: string; label: string } {
  const hour = hourIn(iso, timeZone);
  let match = PERIODS[0];
  for (const period of PERIODS) {
    if (hour >= period.startHour) match = period;
  }
  return { key: match.key, label: match.label };
}

/**
 * Label runs of slots by time of day without reordering them.
 *
 * Walks the list in the order given and opens a new group whenever the band
 * changes, so the times themselves are never sorted or moved — the headings
 * are purely annotations over the existing sequence.
 */
export function groupSlotsByPeriod(
  slots: BookingSlotDTO[],
  timeZone: string,
): SlotPeriod[] {
  const groups: SlotPeriod[] = [];
  for (const slot of slots) {
    const period = slotPeriod(slot.start, timeZone);
    const last = groups[groups.length - 1];
    if (last && last.key === period.key) {
      last.slots.push(slot);
    } else {
      groups.push({ key: period.key, label: period.label, slots: [slot] });
    }
  }
  return groups;
}
