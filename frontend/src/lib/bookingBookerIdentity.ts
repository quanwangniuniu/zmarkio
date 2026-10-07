import type { User } from '@/types/auth';

/**
 * A signed-in account already has a name and email. Only unsigned guests
 * should type those on the public confirm step.
 */
export function isInternalBooker(
  user: User | null | undefined,
  isAuthenticated: boolean,
): boolean {
  return Boolean(isAuthenticated && user?.email?.trim());
}

export function bookerDisplayName(
  user: Pick<User, 'first_name' | 'last_name' | 'username' | 'email'>,
): string {
  const name = [user.first_name, user.last_name].filter(Boolean).join(' ').trim();
  return name || user.username?.trim() || user.email.trim();
}
