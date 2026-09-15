import {
  dayKey,
  formatTime,
  groupSlotsByDay,
  groupSlotsByPeriod,
  slotPeriod,
} from '@/components/calendar/bookingSlots';
import type { BookingSlotDTO } from '@/lib/api/calendarApi';

function slot(start: string, end: string): BookingSlotDTO {
  return { start, end };
}

describe('bookingSlots grouping', () => {
  it('groups slots into calendar days', () => {
    const groups = groupSlotsByDay(
      [
        slot('2026-09-01T09:00:00Z', '2026-09-01T10:00:00Z'),
        slot('2026-09-01T11:00:00Z', '2026-09-01T12:00:00Z'),
        slot('2026-09-02T09:00:00Z', '2026-09-02T10:00:00Z'),
      ],
      'UTC',
    );
    expect(groups.map((g) => g.date)).toEqual(['2026-09-01', '2026-09-02']);
    expect(groups[0].slots).toHaveLength(2);
  });

  it('orders slots chronologically regardless of input order', () => {
    const groups = groupSlotsByDay(
      [
        slot('2026-09-01T15:00:00Z', '2026-09-01T16:00:00Z'),
        slot('2026-09-01T09:00:00Z', '2026-09-01T10:00:00Z'),
      ],
      'UTC',
    );
    expect(groups[0].slots.map((s) => s.start)).toEqual([
      '2026-09-01T09:00:00Z',
      '2026-09-01T15:00:00Z',
    ]);
  });

  it('groups by the display timezone, not UTC', () => {
    // 22:00Z on 1 Sept is already 08:00 on 2 Sept in Sydney. Grouping in UTC
    // would file this slot under the wrong day heading for the visitor.
    const groups = groupSlotsByDay(
      [slot('2026-09-01T22:00:00Z', '2026-09-01T23:00:00Z')],
      'Australia/Sydney',
    );
    expect(groups[0].date).toBe('2026-09-02');
  });

  it('returns an empty list for no slots', () => {
    expect(groupSlotsByDay([], 'UTC')).toEqual([]);
  });
});

describe('bookingSlots formatting', () => {
  it('dayKey resolves the date in the given zone', () => {
    expect(dayKey('2026-09-01T22:00:00Z', 'UTC')).toBe('2026-09-01');
    expect(dayKey('2026-09-01T22:00:00Z', 'Australia/Sydney')).toBe('2026-09-02');
    // Los Angeles is behind UTC, so an early-morning UTC instant is the day before.
    expect(dayKey('2026-09-01T02:00:00Z', 'America/Los_Angeles')).toBe('2026-08-31');
  });

  it('formatTime renders the local wall-clock time', () => {
    // 09:00Z is 19:00 in Sydney (UTC+10 in September).
    expect(formatTime('2026-09-01T09:00:00Z', 'Australia/Sydney')).toMatch(/7:00/);
    expect(formatTime('2026-09-01T09:00:00Z', 'UTC')).toMatch(/9:00/);
  });
});

describe('groupSlotsByPeriod', () => {
  const at = (hhmm: string) => slot(`2026-09-01T${hhmm}:00Z`, `2026-09-01T${hhmm}:00Z`);

  it('labels each band across the full day', () => {
    const groups = groupSlotsByPeriod(
      ['02:00', '08:00', '12:30', '14:00', '18:00', '22:00'].map(at),
      'UTC',
    );
    expect(groups.map((g) => g.label)).toEqual([
      'Early morning',
      'Morning',
      'Noon',
      'Afternoon',
      'Evening',
      'Night',
    ]);
  });

  it('never reorders the times', () => {
    const input = ['06:00', '06:30', '12:15', '13:00', '13:30'].map(at);
    const flattened = groupSlotsByPeriod(input, 'UTC').flatMap((g) => g.slots);
    expect(flattened.map((s) => s.start)).toEqual(input.map((s) => s.start));
  });

  it('keeps consecutive slots in one group', () => {
    const groups = groupSlotsByPeriod(['09:00', '09:15', '09:30'].map(at), 'UTC');
    expect(groups).toHaveLength(1);
    expect(groups[0].slots).toHaveLength(3);
  });

  it('bands by the display timezone, not UTC', () => {
    // 22:00Z is 08:00 the next day in Sydney — morning, not night.
    expect(slotPeriod('2026-09-01T22:00:00Z', 'Australia/Sydney').label).toBe('Morning');
    expect(slotPeriod('2026-09-01T22:00:00Z', 'UTC').label).toBe('Night');
  });

  it('treats midnight as early morning', () => {
    expect(slotPeriod('2026-09-01T00:00:00Z', 'UTC').label).toBe('Early morning');
  });
});
