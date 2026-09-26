import { callGeminiJson, GeminiError } from '@/src/ai/providers/gemini';

// MED-356: copyGenerator.test.ts mocks this provider wholesale, so nothing
// checked the real URL and the short Vertex path 404'd in dev and prod. These
// tests pin the request shape; `npm run smoke:gemini` checks it against Google.

const ENV_KEYS = ['GEMINI_API_KEY', 'GEMINI_VERTEX_PROJECT', 'GEMINI_VERTEX_LOCATION'] as const;

function streamResponse(copy: Record<string, string>, status = 200): Response {
  const chunks = [{ candidates: [{ content: { parts: [{ text: JSON.stringify(copy) }] } }] }];
  return new Response(JSON.stringify(chunks), { status });
}

describe('Gemini provider request', () => {
  const savedEnv: Partial<Record<(typeof ENV_KEYS)[number], string>> = {};
  const originalFetch = global.fetch;
  let fetchMock: jest.Mock;

  beforeEach(() => {
    for (const key of ENV_KEYS) {
      savedEnv[key] = process.env[key];
      delete process.env[key];
    }
    process.env.GEMINI_API_KEY = 'AQ.test-key';
    fetchMock = jest.fn(async () =>
      streamResponse({ hook: 'h', headline: 'hl', description: 'd', cta: 'LEARN_MORE' })
    );
    global.fetch = fetchMock as unknown as typeof fetch;
  });

  afterEach(() => {
    global.fetch = originalFetch;
    for (const key of ENV_KEYS) {
      if (savedEnv[key] === undefined) delete process.env[key];
      else process.env[key] = savedEnv[key];
    }
    jest.restoreAllMocks();
  });

  it('calls the project-scoped Vertex path with the key in a header', async () => {
    const copy = await callGeminiJson('system', 'user');

    expect(copy.headline).toBe('hl');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(
      'https://aiplatform.googleapis.com/v1/projects/406201877905/locations/global' +
        '/publishers/google/models/gemini-2.5-flash-lite:streamGenerateContent'
    );
    expect(url).not.toContain('key=');
    expect((init.headers as Record<string, string>)['x-goog-api-key']).toBe('AQ.test-key');
  });

  it('honours project and location overrides', async () => {
    process.env.GEMINI_VERTEX_PROJECT = 'other-project';
    process.env.GEMINI_VERTEX_LOCATION = 'us-central1';

    await callGeminiJson('system', 'user');

    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toContain('/v1/projects/other-project/locations/us-central1/publishers/google/models/');
  });

  it('fails fast without a key', async () => {
    delete process.env.GEMINI_API_KEY;

    await expect(callGeminiJson('system', 'user')).rejects.toThrow('GEMINI_API_KEY is not configured');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('surfaces a non-retryable HTTP error with its status', async () => {
    jest.spyOn(console, 'error').mockImplementation(() => undefined);
    fetchMock.mockResolvedValueOnce(new Response('{"error":{"code":404}}', { status: 404 }));

    const error = await callGeminiJson('system', 'user').catch((err: unknown) => err);

    expect(error).toBeInstanceOf(GeminiError);
    expect((error as GeminiError).status).toBe(404);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
