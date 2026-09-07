import { ApiError } from '@/lib/bulk';

const DEFAULT_BASE = 'https://chrome.browserless.io';
const DEFAULT_TIMEOUT_MS = 30_000;
const MAX_TEXT_CHARS = 20_000;

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
    .replace(/&quot;/gi, '"');
  const lines = withoutBlocks
    .split('\n')
    .map((line) => line.replace(/\s+/g, ' ').trim())
    .filter(Boolean);
  const text = lines.join('\n');
  return text.length > MAX_TEXT_CHARS ? text.slice(0, MAX_TEXT_CHARS) : text;
}

export async function fetchUrlText(pageUrl: string): Promise<string> {
  const apiKey = process.env.BROWSERLESS_API_KEY;
  if (!apiKey) {
    throw new BrowserlessError('BROWSERLESS_API_KEY is not configured');
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
    throw new BrowserlessError(`Browserless fetch failed: status=${response.status}`);
  }

  const html = await response.text();
  return htmlToText(html);
}
