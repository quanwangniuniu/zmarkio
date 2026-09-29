import { callGeminiJson } from '@/src/ai/providers/gemini';

describe('Gemini provider', () => {
  const originalApiKey = process.env.GEMINI_API_KEY;

  afterEach(() => {
    jest.restoreAllMocks();
    if (originalApiKey === undefined) delete process.env.GEMINI_API_KEY;
    else process.env.GEMINI_API_KEY = originalApiKey;
  });

  it('returns the raw CTA and leaves platform rules to generation', async () => {
    process.env.GEMINI_API_KEY = 'test-api-key';
    const copy = { hook: 'Hook', headline: 'Headline', description: 'Description', cta: ' learn more ' };
    jest.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify([
      { candidates: [{ content: { parts: [{ text: JSON.stringify(copy) }] } }] },
    ])));

    await expect(callGeminiJson('System prompt', 'User prompt')).resolves.toEqual(copy);
  });
});
