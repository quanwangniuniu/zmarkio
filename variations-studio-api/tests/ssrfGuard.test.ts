import { assertPublicUrl, isPrivateAddress } from '@/lib/ssrfGuard';

const resolvesTo =
  (...addresses: string[]) =>
  async () =>
    addresses;

describe('isPrivateAddress', () => {
  it.each([
    '127.0.0.1',
    '10.0.0.5',
    '172.16.0.1',
    '172.31.255.255',
    '192.168.1.10',
    '169.254.169.254',
    '100.64.0.1',
    '0.0.0.0',
    '::1',
    '::',
    'fd00:ec2::254',
    'fe80::1',
    '::ffff:127.0.0.1',
  ])('treats %s as private', (ip) => {
    expect(isPrivateAddress(ip)).toBe(true);
  });

  it.each(['93.184.216.34', '8.8.8.8', '172.32.0.1', '2606:4700:4700::1111'])(
    'treats %s as public',
    (ip) => {
      expect(isPrivateAddress(ip)).toBe(false);
    }
  );
});

describe('assertPublicUrl', () => {
  it('accepts a host that resolves only to public addresses', async () => {
    await expect(
      assertPublicUrl('https://example.com/landing', resolvesTo('93.184.216.34'))
    ).resolves.toBeUndefined();
  });

  it('rejects a literal loopback address without resolving DNS', async () => {
    const resolver = jest.fn();
    await expect(assertPublicUrl('http://127.0.0.1:5432/', resolver)).rejects.toMatchObject({
      status: 400,
    });
    expect(resolver).not.toHaveBeenCalled();
  });

  it('rejects a bracketed IPv6 loopback', async () => {
    await expect(assertPublicUrl('http://[::1]/', jest.fn())).rejects.toMatchObject({
      status: 400,
    });
  });

  it('rejects the cloud metadata address', async () => {
    await expect(
      assertPublicUrl('http://169.254.169.254/latest/meta-data/', jest.fn())
    ).rejects.toMatchObject({ status: 400 });
  });

  it('rejects a hostname that resolves to a private address', async () => {
    await expect(
      assertPublicUrl('https://evil.example.com/', resolvesTo('10.0.0.5'))
    ).rejects.toMatchObject({ status: 400 });
  });

  it('rejects when any resolved address is private', async () => {
    await expect(
      assertPublicUrl('https://mixed.example.com/', resolvesTo('93.184.216.34', '192.168.1.10'))
    ).rejects.toMatchObject({ status: 400 });
  });

  it('rejects a host that cannot be resolved', async () => {
    const failing = async () => {
      throw new Error('ENOTFOUND');
    };
    await expect(assertPublicUrl('https://nope.invalid/', failing)).rejects.toMatchObject({
      status: 400,
    });
  });
});
