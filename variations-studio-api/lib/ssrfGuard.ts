import { lookup } from 'node:dns/promises';
import { isIP } from 'node:net';

import { ApiError } from '@/lib/bulk';

export type HostResolver = (hostname: string) => Promise<string[]>;

const defaultResolver: HostResolver = async (hostname) => {
  const records = await lookup(hostname, { all: true, verbatim: true });
  return records.map((record) => record.address);
};

// [base address, prefix length] — CIDR blocks a server-side fetch must never reach.
const PRIVATE_IPV4_RANGES: Array<[string, number]> = [
  ['0.0.0.0', 8], // "this host"
  ['10.0.0.0', 8], // private network
  ['100.64.0.0', 10], // carrier-grade NAT
  ['127.0.0.0', 8], // loopback
  ['169.254.0.0', 16], // link-local, incl. cloud metadata 169.254.169.254
  ['172.16.0.0', 12], // private network
  ['192.168.0.0', 16], // private network
  ['224.0.0.0', 3], // multicast + reserved, incl. broadcast
];

function ipv4ToNumber(ip: string): number {
  return ip.split('.').reduce((total, part) => total * 256 + Number(part), 0);
}

function isPrivateIpv4(ip: string): boolean {
  const value = ipv4ToNumber(ip);
  return PRIVATE_IPV4_RANGES.some(([base, prefix]) => {
    const start = ipv4ToNumber(base);
    return value >= start && value < start + 2 ** (32 - prefix);
  });
}

function isPrivateIpv6(ip: string): boolean {
  const lower = ip.toLowerCase();

  // IPv4-mapped IPv6, written either way: ::ffff:127.0.0.1 or ::ffff:7f00:1
  const dotted = lower.match(/^::ffff:(\d+\.\d+\.\d+\.\d+)$/);
  if (dotted) return isPrivateIpv4(dotted[1]);
  const hex = lower.match(/^::ffff:([0-9a-f]{1,4}):([0-9a-f]{1,4})$/);
  if (hex) {
    const high = parseInt(hex[1], 16);
    const low = parseInt(hex[2], 16);
    return isPrivateIpv4([high >> 8, high & 255, low >> 8, low & 255].join('.'));
  }

  if (lower === '::' || lower === '::1') return true;

  const firstGroup = parseInt(lower.split(':')[0] || '0', 16);
  return (
    (firstGroup & 0xfe00) === 0xfc00 || // fc00::/7 unique local
    (firstGroup & 0xffc0) === 0xfe80 || // fe80::/10 link-local
    (firstGroup & 0xff00) === 0xff00 // ff00::/8 multicast
  );
}

export function isPrivateAddress(ip: string): boolean {
  const version = isIP(ip);
  if (version === 4) return isPrivateIpv4(ip);
  if (version === 6) return isPrivateIpv6(ip);
  return true; // not an IP at all: refuse rather than guess
}

/**
 * Throws ApiError(400) unless every address the URL's host resolves to is public.
 * The resolver is injectable so tests never touch real DNS.
 */
export async function assertPublicUrl(
  rawUrl: string,
  resolve: HostResolver = defaultResolver
): Promise<void> {
  let hostname: string;
  try {
    hostname = new URL(rawUrl).hostname;
  } catch {
    throw new ApiError(400, 'url is not a valid URL');
  }
  const host = hostname.replace(/^\[|\]$/g, '');

  let addresses: string[];
  if (isIP(host)) {
    addresses = [host];
  } else {
    try {
      addresses = await resolve(host);
    } catch {
      throw new ApiError(400, 'url host could not be resolved');
    }
  }

  if (addresses.length === 0 || addresses.some(isPrivateAddress)) {
    throw new ApiError(400, 'url must not point to a private or internal address');
  }
}
