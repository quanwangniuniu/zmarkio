import { renderHook } from '@testing-library/react';
import { useDraftOwnerLock } from '@/components/decisions/detail/hooks/useDraftOwnerLock';

jest.mock('@/lib/authStore', () => ({
  useAuthStore: (selector: any) => selector({ user: { id: 100 } }),
}));

const lock = (status: string | null, ownerId: number | null, canApproveOrReview: boolean) =>
  renderHook(() => useDraftOwnerLock(status, ownerId, canApproveOrReview)).result.current;

describe('useDraftOwnerLock', () => {
  it.each(['PREDRAFT', 'DRAFT'])('locks a %s owned by someone else', (status) => {
    expect(lock(status, 200, false)).toBe(true);
  });

  it('does not lock the owner', () => {
    expect(lock('DRAFT', 100, false)).toBe(false);
  });

  it('does not lock an approver', () => {
    expect(lock('DRAFT', 200, true)).toBe(false);
  });

  it.each(['AWAITING_APPROVAL', 'COMMITTED', null])('does not lock status %s', (status) => {
    expect(lock(status, 200, false)).toBe(false);
  });
});
