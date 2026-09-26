import {
    callOllamaJson,
    getOllamaConfig,
} from '@/src/ai/providers/ollama';

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
        jest.clearAllMocks();
        process.env = {
            ...originalEnv,
            OLLAMA_BASE_URL: 'http://ollama.test:11434/',
            OLLAMA_MODEL: 'test-model',
            OLLAMA_REQUEST_TIMEOUT_MS: '5000',
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
            timeoutMs: 5000,
        });
    });

    it('requires a configured model', () => {
        delete process.env.OLLAMA_MODEL;

        expect(() => getOllamaConfig()).toThrow(
            'OLLAMA_MODEL is not configured.'
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

        const result = await callOllamaJson('system prompt', 'user prompt');

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

        expect(body).toMatchObject({
            model: 'test-model',
            messages: [
                { role: 'system', content: 'system prompt' },
                { role: 'user', content: 'user prompt' },
            ],
            stream: false,
            think: false,
            options: {
                temperature: 0.7,
            },
        });

        expect(body.format.required).toEqual([
            'hook',
            'headline',
            'description',
            'cta',
        ]);
    });

    it('returns a connection error when Ollama is unavailable', async () => {
        fetchMock.mockRejectedValueOnce(new TypeError('fetch failed'));

        await expect(
            callOllamaJson('system', 'user')
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
            callOllamaJson('system', 'user')
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
            callOllamaJson('system', 'user')
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
            callOllamaJson('system', 'user')
        ).rejects.toMatchObject({
            code: 'request_failed',
            status: 500,
            message:
                'Ollama request failed: model failed to generate a response',
        });
    });

    it('rejects malformed copy JSON', async () => {
        fetchMock.mockResolvedValueOnce(
            jsonResponse({
                message: {
                    content: 'not-json',
                },
            })
        );

        await expect(
            callOllamaJson('system', 'user')
        ).rejects.toMatchObject({
            code: 'invalid_output',
            message: 'Ollama returned malformed copy JSON.',
        });
    });

    it('rejects copy with a missing required field', async () => {
        fetchMock.mockResolvedValueOnce(
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
            callOllamaJson('system', 'user')
        ).rejects.toMatchObject({
            code: 'invalid_output',
            message: 'Ollama output is missing the required "cta" field.',
        });
    });

    it('rejects an empty Ollama response', async () => {
        fetchMock.mockResolvedValueOnce(
            jsonResponse({
                message: {
                    content: '',
                },
            })
        );

        await expect(
            callOllamaJson('system', 'user')
        ).rejects.toMatchObject({
            code: 'invalid_output',
            message: 'Ollama returned an empty response.',
        });
    });
});
