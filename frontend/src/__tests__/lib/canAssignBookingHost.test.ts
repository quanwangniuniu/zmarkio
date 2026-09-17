import { canAssignBookingHost } from '@/lib/canAssignBookingHost';

describe('canAssignBookingHost', () => {
  const user = { id: 6 };

  it('denies when nobody is signed in', () => {
    expect(canAssignBookingHost({ currentUser: null, projectOwnerId: 6 })).toBe(
      false,
    );
  });

  it('allows the project owner', () => {
    expect(
      canAssignBookingHost({
        currentUser: user,
        projectOwnerId: 6,
        membershipRole: 'member',
      }),
    ).toBe(true);
  });

  it('allows an org admin', () => {
    expect(
      canAssignBookingHost({
        currentUser: { id: 9, is_org_admin: true },
        projectOwnerId: 6,
        membershipRole: 'member',
      }),
    ).toBe(true);
  });

  it('allows a privileged project role', () => {
    expect(
      canAssignBookingHost({
        currentUser: { id: 9 },
        projectOwnerId: 6,
        membershipRole: 'Organization Admin',
      }),
    ).toBe(true);
  });

  it('denies a regular member', () => {
    expect(
      canAssignBookingHost({
        currentUser: { id: 9 },
        projectOwnerId: 6,
        membershipRole: 'member',
      }),
    ).toBe(false);
  });
});
