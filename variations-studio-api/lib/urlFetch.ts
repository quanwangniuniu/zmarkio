import { isIP } from 'node:net';
import { ApiError } from '@/lib/bulk';
import { truncateRelevantTextByCharacters } from '@/lib/pageTextBudget';

const DEFAULT_BASE = 'https://chrome.browserless.io';
const DEFAULT_TIMEOUT_MS = 30_000;
const MAX_TEXT_CHARS = 20_000;
const MAX_REDIRECTS = 5;
const REDIRECT_STATUS_CODES = new Set([301, 302, 303, 307, 308]);

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

  const hostname = url.hostname.toLowerCase();

  if (
    !hostname ||
    hostname === 'localhost' ||
    hostname.endsWith('.localhost') ||
    hostname.endsWith('.local') ||
    hostname === 'metadata.google.internal' ||
    isIP(hostname)
  ) {
    throw new BrowserlessError('URL host is not allowed');
  }

  return url;
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

function getRedirectTarget(response: Response, currentUrl: URL): URL | null {
  if (!REDIRECT_STATUS_CODES.has(response.status)) {
    return null;
  }

  const location = response.headers.get('location');
  if (!location) {
    throw new BrowserlessError('Redirect has no location');
  }

  return new URL(location, currentUrl);
}

async function fetchDirectly(pageUrl: string): Promise<string> {
  let currentUrl = pageUrl;

  for (let redirects = 0; redirects <= MAX_REDIRECTS; redirects++) {
    const url = assertSafeUrl(currentUrl);

    const response = await fetch(url, {
      redirect: 'manual',
      signal: AbortSignal.timeout(DEFAULT_TIMEOUT_MS),
      headers: {
        'User-Agent': 'Mozilla/5.0 (compatible; MarketingStudio/1.0)',
        Accept: 'text/html,application/xhtml+xml',
      },
    });

    const redirectTarget = getRedirectTarget(response, url);
    if (redirectTarget) {
      currentUrl = redirectTarget.toString();
      continue;
    }

    if (!response.ok) {
      throw new BrowserlessError(
        `Page fetch failed: status=${response.status}`
      );
    }

    const contentType = response.headers.get('content-type') || '';
    if (
      contentType &&
      !contentType.includes('text/html') &&
      !contentType.includes('application/xhtml+xml')
    ) {
      throw new BrowserlessError('URL did not return an HTML page');
    }

    return htmlToText(await response.text());
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
