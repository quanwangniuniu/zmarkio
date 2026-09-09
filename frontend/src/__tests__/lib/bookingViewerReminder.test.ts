import { resolveViewerBookings } from '@/lib/bookingViewerReminder';

const existing = {
  start: '2026-09-07T00:00:00Z',
  end: '2026-09-07T01:00:00Z',
  title: 'standup4',
};

describe('resolveViewerBookings', () => {
  it('prefers the signed-in availability payload', () => {
    expect(
      resolveViewerBookings({
        fromApi: [existing],
        fromSession: [],
        signedIn: true,
      }),
    ).toEqual([existing]);
  });

  it('does not invent a booking for a signed-in viewer', () => {
    expect(
      resolveViewerBookings({
        fromApi: [],
        fromSession: [existing],
        signedIn: true,
      }),
    ).toEqual([]);
  });

  it('keeps a guest reminder from this tab after they leave confirmation', () => {
    expect(
      resolveViewerBookings({
        fromApi: [],
        fromSession: [existing],
        signedIn: false,
      }),
    ).toEqual([existing]);
  });
});
