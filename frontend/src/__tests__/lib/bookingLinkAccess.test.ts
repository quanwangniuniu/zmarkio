import { canRestrictToInvitees, hasNamedInvitees } from '@/lib/bookingLinkAccess';

describe('bookingLinkAccess', () => {
  it('needs at least one named person before a link can be invitees-only', () => {
    expect(hasNamedInvitees([], [])).toBe(false);
    expect(canRestrictToInvitees([], [])).toBe(false);
    expect(canRestrictToInvitees([2], [])).toBe(true);
    expect(canRestrictToInvitees([], ['ada@acme.com'])).toBe(true);
    expect(hasNamedInvitees([], ['  '])).toBe(false);
  });
});
