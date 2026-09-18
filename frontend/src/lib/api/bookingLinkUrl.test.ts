import { bookingLinkUrl } from '@/lib/api/calendarApi';

/**
 * The URL a user copies out of the manager must match the public route
 * (app/(public)/book/[orgSlug]/[linkSlug]) exactly — a mismatch here produces a
 * link that 404s only after it has been sent to a prospect.
 */
describe('bookingLinkUrl', () => {
  it('builds a path matching the public booking route', () => {
    expect(bookingLinkUrl('acme', 'intro-call')).toBe(
      `${window.location.origin}/book/acme/intro-call`,
    );
  });

  it('encodes segments so a copied link stays valid', () => {
    expect(bookingLinkUrl('acme corp', 'intro call')).toBe(
      `${window.location.origin}/book/acme%20corp/intro%20call`,
    );
  });

  it('does not double-encode an already safe slug', () => {
    expect(bookingLinkUrl('acme-corp', 'intro_call-2')).toBe(
      `${window.location.origin}/book/acme-corp/intro_call-2`,
    );
  });
});
