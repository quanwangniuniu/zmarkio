import { bookerDisplayName, isInternalBooker } from '@/lib/bookingBookerIdentity';
import type { User } from '@/types/auth';

function user(partial: Partial<User> & Pick<User, 'email' | 'username'>): User {
  return {
    organization: null,
    current_organization: null,
    roles: [],
    ...partial,
  };
}

describe('bookingBookerIdentity', () => {
  it('treats a signed-in account with an email as an internal booker', () => {
    expect(isInternalBooker(user({ email: 'ada@acme.com', username: 'ada' }), true)).toBe(
      true,
    );
    expect(isInternalBooker(user({ email: 'ada@acme.com', username: 'ada' }), false)).toBe(
      false,
    );
    expect(isInternalBooker(null, true)).toBe(false);
    expect(isInternalBooker(user({ email: '  ', username: 'ada' }), true)).toBe(false);
  });

  it('prefers a full name, then username, then email', () => {
    expect(
      bookerDisplayName(
        user({
          email: 'ada@acme.com',
          username: 'ada',
          first_name: 'Ada',
          last_name: 'Lovelace',
        }),
      ),
    ).toBe('Ada Lovelace');
    expect(bookerDisplayName(user({ email: 'ada@acme.com', username: 'ada' }))).toBe('ada');
    expect(bookerDisplayName(user({ email: 'ada@acme.com', username: '' }))).toBe(
      'ada@acme.com',
    );
  });
});
