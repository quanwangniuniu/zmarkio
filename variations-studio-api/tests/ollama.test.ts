import { AI_QUOTA_MESSAGE } from '@/src/ai/prompts';
import { metaSpec } from '@/src/platforms/meta';
import {
    callOllamaJson,
    getOllamaConfig,
    getOllamaErrorMessage,
} from '@/src/ai/providers/ollama';
import { testTextSpec } from './support/textPlatform';

const originalEnv = process.env;
const originalFetch = global.fetch;
const fetchMock = jest.fn();

function jsonResponse(body: unknown, status = 200): Response {
    return new Response(JSON.stringify(body), {
        status,
        headers: {
            'Content-Type': 'application/json',
        },
    });
}

describe('Ollama provider', () => {
    beforeEach(() => {
        jest.resetAllMocks();
        process.env = {
            ...originalEnv,
            OLLAMA_BASE_URL: 'http://ollama.test:11434/',
            OLLAMA_MODEL: 'test-model',
            OLLAMA_NUM_CTX: '8192',
            OLLAMA_REQUEST_TIMEOUT_MS: '5000',
            OLLAMA_RETRY_DELAYS_MS: '0,0',
        };
        global.fetch = fetchMock as typeof fetch;
    });

    afterAll(() => {
        process.env = originalEnv;
        global.fetch = originalFetch;
    });

    it('reads and normalizes Ollama configuration', () => {
        expect(getOllamaConfig()).toEqual({
            baseUrl: 'http://ollama.test:11434',
            model: 'test-model',
            numCtx: 8192,
            timeoutMs: 5000,
            retryDelaysMs: [0, 0],
            keepAlive: '30m',
        });
    });

    it('requires a configured model', () => {
        delete process.env.OLLAMA_MODEL;

        expect(() => getOllamaConfig()).toThrow(
            'OLLAMA_MODEL is not configured.'
        );
    });

    it('rejects an invalid context window', () => {
        process.env.OLLAMA_NUM_CTX = '8192.5';

        expect(() => getOllamaConfig()).toThrow(
            'OLLAMA_NUM_CTX must be a positive integer.'
        );
    });

    it('rejects an invalid timeout', () => {
        process.env.OLLAMA_REQUEST_TIMEOUT_MS = 'invalid';

        expect(() => getOllamaConfig()).toThrow(
            'OLLAMA_REQUEST_TIMEOUT_MS must be a positive number.'
        );
    });

    it('returns structured copy from a valid response', async () => {
        fetchMock.mockResolvedValueOnce(
            jsonResponse({
                message: {
                    content: JSON.stringify({
                        hook: 'A useful hook',
                        headline: 'A useful headline',
                        description: 'A useful description',
                        cta: 'learn more',
                    }),
                },
            })
        );

        const result = await callOllamaJson('system prompt', 'user prompt', metaSpec);

        expect(result).toEqual({
            hook: 'A useful hook',
            headline: 'A useful headline',
            description: 'A useful description',
            cta: 'learn more',
        });

        expect(fetchMock).toHaveBeenCalledTimes(1);
        expect(fetchMock).toHaveBeenCalledWith(
            'http://ollama.test:11434/api/chat',
            expect.objectContaining({
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
            })
        );

        const request = fetchMock.mock.calls[0][1] as RequestInit;
        const body = JSON.parse(request.body as string);

        expect(body).toEqual({
            model: 'test-model',
            messages: [
                { role: 'system', content: 'system prompt' },
                { role: 'user', content: 'user prompt' },
            ],
            stream: false,
            think: false,
            keep_alive: '30m',
            format: {
                type: 'object',
                properties: {
                    hook: { type: 'string' },
                    headline: { type: 'string' },
                    description: { type: 'string' },
                    cta: { type: 'string' },
                },
                required: ['hook', 'headline', 'description', 'cta'],
                additionalProperties: false,
            },
            options: {
                temperature: 0.7,
                num_ctx: 8192,
            },
        });
    });

    it('requests all platform fields and accepts a missing optional string', async () => {
        const copy = { ad_text: 'Discover the collection', action: 'JOIN_US' };
        fetchMock.mockResolvedValueOnce(jsonResponse({ message: { content: JSON.stringify(copy) } }));

        await expect(callOllamaJson('system', 'user', testTextSpec)).resolves.toEqual(copy);
        expect(fetchMock).toHaveBeenCalledTimes(1);
        const request = fetchMock.mock.calls[0][1] as RequestInit;
        expect(JSON.parse(request.body as string).format).toEqual({
            type: 'object',
            properties: {
                ad_text: { type: 'string' },
                action: { type: 'string' },
                tagline: { type: 'string' },
            },
            required: ['ad_text', 'action'],
            additionalProperties: false,
        });
    });

    it('rejects a supplied optional field of the wrong type', async () => {
        const copy = { ad_text: 'Discover the collection', action: 'JOIN_US', tagline: null };
        fetchMock.mockImplementation(async () => jsonResponse({ message: { content: JSON.stringify(copy) } }));

        await expect(callOllamaJson('system', 'user', testTextSpec)).rejects.toMatchObject({
            code: 'invalid_output',
            message: 'Ollama output field "tagline" must be a string.',
        });
    });

    it('returns a connection error when Ollama is unavailable', async () => {
        fetchMock.mockRejectedValueOnce(new TypeError('fetch failed'));

        await expect(
            callOllamaJson('system', 'user', metaSpec)
        ).rejects.toMatchObject({
            code: 'connection',
            message: 'Unable to connect to Ollama at http://ollama.test:11434.',
        });
    });

    it('returns a timeout error', async () => {
        const timeoutError = new Error('timed out');
        timeoutError.name = 'TimeoutError';
        fetchMock.mockRejectedValueOnce(timeoutError);

        await expect(
            callOllamaJson('system', 'user', metaSpec)
        ).rejects.toMatchObject({
            code: 'timeout',
            message: 'Ollama request timed out after 5000 ms.',
        });
    });

    it('returns a missing-model error for HTTP 404', async () => {
        fetchMock.mockResolvedValueOnce(
            jsonResponse(
                {
                    error: 'model not found',
                },
                404
            )
        );

        await expect(
            callOllamaJson('system', 'user', metaSpec)
        ).rejects.toMatchObject({
            code: 'model_not_found',
            status: 404,
            message: 'The configured Ollama model "test-model" is not available.',
        });
    });

    it('returns a clear error for a failed Ollama request', async () => {
        fetchMock.mockResolvedValueOnce(
            jsonResponse(
                {
                    error: 'model failed to generate a response',
                },
                500
            )
        );

        await expect(
            callOllamaJson('system', 'user', metaSpec)
        ).rejects.toMatchObject({
            code: 'request_failed',
            status: 500,
            message:
                'Ollama request failed: model failed to generate a response',
        });
    });

    it('rejects malformed copy JSON', async () => {
        fetchMock.mockImplementation(async () =>
            jsonResponse({
                message: {
                    content: 'not-json',
                },
            })
        );

        await expect(
            callOllamaJson('system', 'user', metaSpec)
        ).rejects.toMatchObject({
            code: 'invalid_output',
            message: 'Ollama returned malformed copy JSON.',
        });
    });

    it('rejects copy with a missing required field', async () => {
        fetchMock.mockImplementation(async () =>
            jsonResponse({
                message: {
                    content: JSON.stringify({
                        hook: 'Hook',
                        headline: 'Headline',
                        description: 'Description',
                    }),
                },
            })
        );

        await expect(
            callOllamaJson('system', 'user', metaSpec)
        ).rejects.toMatchObject({
            code: 'invalid_output',
            message: 'Ollama output is missing the required "cta" field.',
        });
    });

    it('rejects an empty Ollama response', async () => {
        fetchMock.mockImplementation(async () =>
            jsonResponse({
                message: {
                    content: '',
                },
            })
        );

        await expect(
            callOllamaJson('system', 'user', metaSpec)
        ).rejects.toMatchObject({
            code: 'invalid_output',
            message: 'Ollama returned an empty response.',
        });
    });

    it('retries malformed JSON once', async () => {
        const copy = { ad_text: 'Discover the collection', action: 'JOIN_US', tagline: 'Today only' };
        fetchMock
            .mockResolvedValueOnce(jsonResponse({ message: { content: 'not-json' } }))
            .mockResolvedValueOnce(jsonResponse({ message: { content: JSON.stringify(copy) } }));

        await expect(callOllamaJson('system', 'user', testTextSpec)).resolves.toEqual(copy);
    });

    it('retries a busy Ollama before succeeding', async () => {
        const copy = { ad_text: 'Discover the collection', action: 'JOIN_US' };
        fetchMock
            .mockResolvedValueOnce(jsonResponse({ error: 'server busy' }, 503))
            .mockResolvedValueOnce(jsonResponse({ message: { content: JSON.stringify(copy) } }));

        await expect(callOllamaJson('system', 'user', testTextSpec)).resolves.toEqual(copy);
    });

    it('returns the quota message when Ollama stays busy', async () => {
        fetchMock.mockImplementation(async () => jsonResponse({ error: 'rate limited' }, 429));

        const error = await callOllamaJson('system', 'user', metaSpec).catch((err: unknown) => err);

        expect(fetchMock).toHaveBeenCalledTimes(3);
        expect(getOllamaErrorMessage(error)).toBe(AI_QUOTA_MESSAGE);
    });
});
