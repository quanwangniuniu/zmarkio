import { formatBucket, formatDate, formatDateTime } from '@/components/csm/quality/formatDates';

describe('quality date formatting', () => {
  it('uses short-month form rather than an ambiguous numeric date', () => {
    // 9/17/2026 reads as 17 September in the US and as nothing sensible
    // elsewhere; "Sep 17, 2026" is unambiguous.
    // jsdom runs in UTC, so use a UTC instant: the point is the shape, not
    // which zone the test machine is in.
    const formatted = formatDateTime('2026-09-17T09:59:00Z');

    expect(formatted).toContain('Sep');
    expect(formatted).toContain('17');
    expect(formatted).toContain('2026');
  });

  it('formats a date without a time when only the day matters', () => {
    expect(formatDate('2026-09-17T09:59:00Z')).toContain('Sep');
  });

  it('falls back rather than rendering Invalid Date', () => {
    expect(formatDateTime(null)).toBe('—');
    expect(formatDateTime('not a date')).toBe('—');
    expect(formatDate(undefined)).toBe('—');
    expect(formatBucket('')).toBe('—');
  });

  it('keeps a report bucket on its own day regardless of the viewer offset', () => {
    // new Date('2026-09-17') parses as UTC midnight, which renders as the 16th
    // for anyone west of UTC. formatBucket builds from the parts instead.
    const formatted = formatBucket('2026-09-17');

    expect(formatted).toContain('17');
    expect(formatted).toContain('Sep');
    expect(formatted).not.toContain('16');
  });
});
