'use client';

import { useAuthStore } from '@/lib/authStore';

const OWNER_ONLY_STATUSES = new Set(['PREDRAFT', 'DRAFT']);

export function useDraftOwnerLock(
  status: string | null | undefined,
  ownerId: number | null | undefined,
  canApproveOrReview: boolean,
): boolean {
  const userId = useAuthStore((s) => s.user?.id);
  if (!status || !OWNER_ONLY_STATUSES.has(status)) return false;
  return Number(ownerId) !== Number(userId) && !canApproveOrReview;
}
