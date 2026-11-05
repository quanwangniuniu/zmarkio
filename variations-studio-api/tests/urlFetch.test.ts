import { BrowserlessError, fetchUrlText } from '@/lib/urlFetch';

const originalEnv = process.env;
const originalFetch = global.fetch;
const fetchMock = jest.fn();

describe('fetchUrlText', () => {
  beforeEach(() => {
    jest.resetAllMocks();
    process.env = { ...originalEnv, BROWSERLESS_API_KEY: 'test-key' };
    delete process.env.BROWSERLESS_BASE;
    fetchMock.mockResolvedValue(
      new Response('<html><body><p>Hello</p></body></html>', { status: 200 })
    );
    global.fetch = fetchMock as typeof fetch;
  });

  afterAll(() => {
    process.env = originalEnv;
    global.fetch = originalFetch;
  });

  it('calls the configured self-hosted Browserless base', async () => {
    process.env.BROWSERLESS_BASE = 'http://browserless.internal:3000';

    const text = await fetchUrlText('https://example.com/landing');

    expect(text).toContain('Hello');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [endpoint] = fetchMock.mock.calls[0];
    expect(String(endpoint).startsWith('http://browserless.internal:3000/content')).toBe(true);
  });

  it('fails closed without BROWSERLESS_BASE and makes no outbound request', async () => {
    await expect(fetchUrlText('https://example.com/landing')).rejects.toThrow(BrowserlessError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('sends the token in an Authorization header, never in the URL', async () => {
    process.env.BROWSERLESS_BASE = 'http://browserless.internal:3000';

    await fetchUrlText('https://example.com/landing');

    const [endpoint, init] = fetchMock.mock.calls[0];
    expect(String(endpoint)).not.toContain('token=');
    expect(String(endpoint)).not.toContain('test-key');
    expect((init as RequestInit).headers).toMatchObject({
      Authorization: 'Bearer test-key',
    });
  });
});
