import {
  buildIcs,
  escapeIcsText,
  googleCalendarUrl,
  icsFileName,
  outlookCalendarUrl,
  toCompactUtc,
  toOutlookUtc,
  type CalendarEntry,
} from '@/components/calendar/calendarExport';

const entry: CalendarEntry = {
  title: 'Intro call',
  start: '2026-09-10T14:30:00Z',
  end: '2026-09-10T15:00:00Z',
};

describe('toCompactUtc', () => {
  it('renders an instant as YYYYMMDDTHHMMSSZ', () => {
    expect(toCompactUtc('2026-09-10T14:30:00Z')).toBe('20260910T143000Z');
  });

  it('normalises an offset instant to UTC', () => {
    expect(toCompactUtc('2026-09-10T16:30:00+02:00')).toBe('20260910T143000Z');
  });
});

describe('escapeIcsText', () => {
  it('escapes the characters RFC 5545 reserves', () => {
    expect(escapeIcsText('a,b;c')).toBe('a\\,b\\;c');
  });

  it('turns newlines into literal \\n', () => {
    expect(escapeIcsText('one\ntwo')).toBe('one\\ntwo');
  });

  it('escapes backslashes without double-escaping later replacements', () => {
    expect(escapeIcsText('a\\b,c')).toBe('a\\\\b\\,c');
  });
});

describe('buildIcs', () => {
  it('emits a single well-formed VEVENT', () => {
    const ics = buildIcs(entry, 'fixed-uid');
    expect(ics).toContain('BEGIN:VCALENDAR');
    expect(ics).toContain('BEGIN:VEVENT');
    expect(ics).toContain('UID:fixed-uid');
    expect(ics).toContain('DTSTART:20260910T143000Z');
    expect(ics).toContain('DTEND:20260910T150000Z');
    expect(ics).toContain('SUMMARY:Intro call');
    expect(ics.trimEnd().endsWith('END:VCALENDAR')).toBe(true);
    expect(ics.endsWith('\r\n')).toBe(true);
  });

  it('separates lines with CRLF, as Outlook requires', () => {
    expect(buildIcs(entry, 'u')).toContain('\r\n');
    expect(buildIcs(entry, 'u').split('\r\n').every((line) => !line.includes('\n'))).toBe(true);
  });

  it('omits DESCRIPTION when there is none', () => {
    expect(buildIcs(entry, 'u')).not.toContain('DESCRIPTION:');
  });

  it('escapes the description rather than breaking the field', () => {
    const ics = buildIcs({ ...entry, description: 'Notes: a, b\nWith Ray' }, 'u');
    expect(ics).toContain('DESCRIPTION:Notes: a\\, b\\nWith Ray');
  });
});

describe('googleCalendarUrl', () => {
  it('builds a prefilled template link', () => {
    const url = new URL(googleCalendarUrl(entry));
    expect(url.origin + url.pathname).toBe('https://calendar.google.com/calendar/render');
    expect(url.searchParams.get('action')).toBe('TEMPLATE');
    expect(url.searchParams.get('text')).toBe('Intro call');
    expect(url.searchParams.get('dates')).toBe('20260910T143000Z/20260910T150000Z');
  });

  it('passes the description through as details', () => {
    const url = new URL(googleCalendarUrl({ ...entry, description: 'With Ray' }));
    expect(url.searchParams.get('details')).toBe('With Ray');
  });
});

describe('icsFileName', () => {
  it('slugifies the title', () => {
    expect(icsFileName('Intro Call: 30 min')).toBe('intro-call-30-min.ics');
  });

  it('falls back when the title has nothing usable', () => {
    expect(icsFileName('!!!')).toBe('booking.ics');
  });
});

describe('cancel link', () => {
  const withUrl = { ...entry, url: 'https://app.example/book/acme/intro/cancel?token=abc' };

  it('rides into the calendar entry as URL, so a closed tab is recoverable', () => {
    expect(buildIcs(withUrl, 'u')).toContain(
      'URL:https://app.example/book/acme/intro/cancel?token=abc',
    );
  });

  it('is omitted when there is none', () => {
    expect(buildIcs(entry, 'u')).not.toContain('URL:');
  });

  it('rides along in details for Google, which has no URL field', () => {
    const url = new URL(googleCalendarUrl({ ...withUrl, description: 'With Ray' }));
    expect(url.searchParams.get('details')).toBe(
      'With Ray\n\nhttps://app.example/book/acme/intro/cancel?token=abc',
    );
  });
});

describe('outlookCalendarUrl', () => {
  it('builds a prefilled Outlook compose link', () => {
    const url = new URL(outlookCalendarUrl(entry));
    expect(url.origin + url.pathname).toBe(
      'https://outlook.live.com/calendar/0/deeplink/compose',
    );
    expect(url.searchParams.get('rru')).toBe('addevent');
    expect(url.searchParams.get('subject')).toBe('Intro call');
    expect(url.searchParams.get('startdt')).toBe('2026-09-10T14:30:00Z');
    expect(url.searchParams.get('enddt')).toBe('2026-09-10T15:00:00Z');
  });

  it('normalises compact stamps to Outlook’s ISO form', () => {
    expect(toOutlookUtc('2026-09-10T16:30:00+02:00')).toBe('2026-09-10T14:30:00Z');
  });
});

describe('calendar text safety', () => {
  it('escapes a bare carriage return instead of starting a new property', () => {
    expect(escapeIcsText('Guest\rATTENDEE:intruder@example.com')).toBe('Guest\\nATTENDEE:intruder@example.com');
  });
});
