import { lookup as dnsLookup } from 'node:dns/promises';
import type { LookupFunction } from 'node:net';
import * as http from 'node:http';
import * as https from 'node:https';
import { BlockList, isIP } from 'node:net';
import { ApiError } from '@/lib/bulk';
import { truncateRelevantTextByCharacters } from '@/lib/pageTextBudget';

const DEFAULT_BASE = 'https://chrome.browserless.io';
const DEFAULT_TIMEOUT_MS = 30_000;
const MAX_TEXT_CHARS = 20_000;
const MAX_REDIRECTS = 5;
const REDIRECT_STATUS_CODES = new Set([301, 302, 303, 307, 308]);

const blockedIPv4 = new BlockList();

[
  ['0.0.0.0', 8],
  ['10.0.0.0', 8],
  ['100.64.0.0', 10],
  ['127.0.0.0', 8],
  ['169.254.0.0', 16],
  ['172.16.0.0', 12],
  ['192.0.0.0', 24],
  ['192.0.2.0', 24],
  ['192.88.99.0', 24],
  ['192.168.0.0', 16],
  ['198.18.0.0', 15],
  ['198.51.100.0', 24],
  ['203.0.113.0', 24],
  ['224.0.0.0', 4],
  ['240.0.0.0', 4],
].forEach(([subnet, prefix]) => {
  blockedIPv4.addSubnet(subnet as string, prefix as number, 'ipv4');
});

const globalIPv6 = new BlockList();
globalIPv6.addSubnet('2000::', 3, 'ipv6');

const blockedIPv6 = new BlockList();

[
  ['2001::', 23],
  ['2001:db8::', 32],
  ['2002::', 16],
  ['3fff::', 20],
  ['64:ff9b::', 96],
  ['64:ff9b:1::', 48],
  ['100::', 64],
  ['fc00::', 7],
  ['fe80::', 10],
  ['ff00::', 8],
  ['::', 128],
  ['::1', 128],
  ['::ffff:0:0', 96],
].forEach(([subnet, prefix]) => {
  blockedIPv6.addSubnet(subnet as string, prefix as number, 'ipv6');
});

const blockedHostnameSuffixes = [
  '.localhost',
  '.local',
  '.internal',
  '.localdomain',
  '.lan',
  '.home',
  '.corp',
  '.intranet',
  '.test',
  '.invalid',
  '.example',
];

export class BrowserlessError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'BrowserlessError';
  }
}

export function parseExternalUrl(raw: unknown): string {
  const url = typeof raw === 'string' ? raw.trim() : '';
  if (!url) {
    throw new ApiError(400, 'url required for source_mode=external_url');
  }
  if (!url.startsWith('http://') && !url.startsWith('https://')) {
    throw new ApiError(400, 'url must be http or https');
  }

  return url;
}

function isBlockedIp(address: string, family: number): boolean {
  if (family === 4) {
    return blockedIPv4.check(address, 'ipv4');
  }

  if (family === 6) {
    return (
      !globalIPv6.check(address, 'ipv6') ||
      blockedIPv6.check(address, 'ipv6')
    );
  }

  return true;
}

function assertSafeUrl(rawUrl: string): URL {
  let url: URL;

  try {
    url = new URL(rawUrl);
  } catch {
    throw new BrowserlessError('Invalid page URL');
  }

  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new BrowserlessError('Only http and https URLs are allowed');
  }

  if (url.username || url.password) {
    throw new BrowserlessError('URL credentials are not allowed');
  }

  const hostname = url.hostname
    .toLowerCase()
    .replace(/^\[|\]$/g, '')
    .replace(/\.$/, '');

  if (!hostname) {
    throw new BrowserlessError('URL host is not allowed');
  }

  if (
    hostname === 'localhost' ||
    blockedHostnameSuffixes.some((suffix) => hostname.endsWith(suffix)) ||
    !hostname.includes('.') && isIP(hostname) === 0
  ) {
    throw new BrowserlessError('URL host is not allowed');
  }

  const family = isIP(hostname);

  if (family && isBlockedIp(hostname, family)) {
    throw new BrowserlessError('URL host is not allowed');
  }

  return url;
}

async function resolvePublicAddresses(
  hostname: string
): Promise<Array<{ address: string; family: number }>> {
  const family = isIP(hostname);

  if (family) {
    if (isBlockedIp(hostname, family)) {
      throw new BrowserlessError('URL host is not allowed');
    }

    return [{ address: hostname, family }];
  }

  let addresses: Array<{ address: string; family: number }>;

  try {
    addresses = await dnsLookup(hostname, {
      all: true,
      verbatim: true,
    });
  } catch {
    throw new BrowserlessError('Unable to resolve page hostname');
  }

  if (
    addresses.length === 0 ||
    addresses.some(({ address, family: addressFamily }) =>
      isBlockedIp(address, addressFamily)
    )
  ) {
    throw new BrowserlessError('URL host resolves to a non-public address');
  }

  return addresses;
}

function htmlToText(html: string): string {
  const withoutBlocks = html
    .replace(/<script[\s\S]*?<\/script>/gi, ' ')
    .replace(/<style[\s\S]*?<\/style>/gi, ' ')
    .replace(/<noscript[\s\S]*?<\/noscript>/gi, ' ')
    .replace(/<[^>]+>/g, '\n')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'");

  const lines = withoutBlocks
    .split('\n')
    .map((line) => line.replace(/\s+/g, ' ').trim())
    .filter(Boolean);

  return truncateRelevantTextByCharacters(
    lines.join('\n'),
    MAX_TEXT_CHARS
  );
}

function getRedirectTarget(response: http.IncomingMessage, currentUrl: URL): URL | null {
  if (!REDIRECT_STATUS_CODES.has(response.statusCode || 0)) {
    return null;
  }

  const location = response.headers.location;

  if (!location || Array.isArray(location)) {
    throw new BrowserlessError('Redirect has no valid location');
  }

  try {
    return new URL(location, currentUrl);
  } catch {
    throw new BrowserlessError('Invalid redirect URL');
  }
}

async function requestPinned(
  url: URL,
  addresses: Array<{ address: string; family: number }>
): Promise<{
  statusCode: number;
  headers: http.IncomingHttpHeaders;
  body: string;
}> {
  const pinnedLookup = ((
    _hostname: string,
    options: { all?: boolean } | number,
    callback: (...args: any[]) => void
  ) => {
    if (typeof options === 'object' && options.all) {
      callback(null, addresses);
    } else {
      callback(null, addresses[0].address, addresses[0].family);
    }
  }) as LookupFunction;

  return new Promise((resolve, reject) => {
    const onResponse = (response: http.IncomingMessage) => {
      const chunks: Buffer[] = [];

      response.on('data', (chunk: Buffer | string) => {
        chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
      });

      response.on('error', reject);

      response.on('end', () => {
        resolve({
          statusCode: response.statusCode || 0,
          headers: response.headers,
          body: Buffer.concat(chunks).toString('utf8'),
        });
      });
    };

    const options: http.RequestOptions = {
      method: 'GET',
      lookup: pinnedLookup,
      signal: AbortSignal.timeout(DEFAULT_TIMEOUT_MS),
      headers: {
        'User-Agent': 'Mozilla/5.0 (compatible; MarketingStudio/1.0)',
        Accept: 'text/html,application/xhtml+xml',
      },
    };

    const request = url.protocol === 'https:'
      ? https.request(url, options, onResponse)
      : http.request(url, options, onResponse);

    request.on('error', reject);
    request.end();
  });
}

async function fetchDirectly(pageUrl: string): Promise<string> {
  let currentUrl = pageUrl;

  for (let redirects = 0; redirects <= MAX_REDIRECTS; redirects++) {
    const url = assertSafeUrl(currentUrl);
    const hostname = url.hostname.replace(/^\[|\]$/g, '');
    const addresses = await resolvePublicAddresses(hostname);

    const response = await requestPinned(url, addresses);

    const redirectTarget = getRedirectTarget(
      {
        statusCode: response.statusCode,
        headers: response.headers,
      } as http.IncomingMessage,
      url
    );

    if (redirectTarget) {
      currentUrl = redirectTarget.toString();
      continue;
    }

    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw new BrowserlessError(
        `Page fetch failed: status=${response.statusCode}`
      );
    }

    const contentType = response.headers['content-type'] || '';
    if (
      contentType &&
      !contentType.includes('text/html') &&
      !contentType.includes('application/xhtml+xml')
    ) {
      throw new BrowserlessError('URL did not return an HTML page');
    }

    return htmlToText(response.body);
  }

  throw new BrowserlessError('Too many redirects');
}

export async function fetchUrlText(pageUrl: string): Promise<string> {
  const apiKey = process.env.BROWSERLESS_API_KEY;

  // Use Browserless when configured; otherwise try a normal HTTP request.
  if (!apiKey) {
    return fetchDirectly(pageUrl);
  }

  const base = (process.env.BROWSERLESS_BASE || DEFAULT_BASE).replace(/\/$/, '');
  const endpoint = new URL('/content', `${base}/`);
  endpoint.searchParams.set('token', apiKey);

  const response = await fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      url: pageUrl,
      gotoOptions: { waitUntil: 'networkidle2' },
    }),
    signal: AbortSignal.timeout(DEFAULT_TIMEOUT_MS),
  });

  if (!response.ok) {
    console.error('Browserless call failed status=%s', response.status);
    throw new BrowserlessError(
      `Browserless fetch failed: status=${response.status}`
    );
  }

  return htmlToText(await response.text());
}
