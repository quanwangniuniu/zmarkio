import { lockCta } from '@/src/ai/prompts';
import type { CopyJson } from '@/src/ai/types';

const DEFAULT_BASE_URL = 'http://localhost:11434';
const DEFAULT_TIMEOUT_MS = 60_000;

const COPY_SCHEMA = {
    type: 'object',
    properties: {
        hook: { type: 'string' },
        headline: { type: 'string' },
        description: { type: 'string' },
        cta: { type: 'string' },
    },
    required: ['hook', 'headline', 'description', 'cta'],
    additionalProperties: false,
};

export type OllamaErrorCode =
    | 'configuration'
    | 'connection'
    | 'model_not_found'
    | 'timeout'
    | 'invalid_output'
    | 'request_failed';

export class OllamaError extends Error {
    constructor(
        message: string,
        readonly code: OllamaErrorCode,
        readonly status?: number
    ) {
        super(message);
        this.name = 'OllamaError';
    }
}

export type OllamaConfig = {
    baseUrl: string;
    model: string;
    timeoutMs: number;
};

export function getOllamaConfig(): OllamaConfig {
    const baseUrl = (
        process.env.OLLAMA_BASE_URL?.trim() || DEFAULT_BASE_URL
    ).replace(/\/+$/, '');

    const model = process.env.OLLAMA_MODEL?.trim();
    if (!model) {
        throw new OllamaError(
            'OLLAMA_MODEL is not configured.',
            'configuration'
        );
    }

    const timeoutValue = process.env.OLLAMA_REQUEST_TIMEOUT_MS?.trim();
    const timeoutMs = timeoutValue
        ? Number(timeoutValue)
        : DEFAULT_TIMEOUT_MS;

    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) {
        throw new OllamaError(
            'OLLAMA_REQUEST_TIMEOUT_MS must be a positive number.',
            'configuration'
        );
    }

    return {
        baseUrl,
        model,
        timeoutMs,
    };
}

function asCopy(raw: unknown): CopyJson {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
        throw new OllamaError(
            'Ollama returned invalid copy JSON.',
            'invalid_output'
        );
    }

    const row = raw as Record<string, unknown>;
    const fields = ['hook', 'headline', 'description', 'cta'] as const;

    for (const field of fields) {
        if (typeof row[field] !== 'string') {
            throw new OllamaError(
                `Ollama output is missing the required "${field}" field.`,
                'invalid_output'
            );
        }
    }

    return {
        hook: row.hook as string,
        headline: row.headline as string,
        description: row.description as string,
        cta: lockCta(row.cta as string),
    };
}

async function readErrorMessage(response: Response): Promise<string | null> {
    try {
        const body = (await response.json()) as { error?: unknown };
        return typeof body.error === 'string' ? body.error : null;
    } catch {
        return null;
    }
}

export async function callOllamaJson(
    systemPrompt: string,
    userPrompt: string
): Promise<CopyJson> {
    const config = getOllamaConfig();

    let response: Response;

    try {
        response = await fetch(`${config.baseUrl}/api/chat`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                model: config.model,
                messages: [
                    { role: 'system', content: systemPrompt },
                    { role: 'user', content: userPrompt },
                ],
                stream: false,
                think: false,
                format: COPY_SCHEMA,
                options: {
                    temperature: 0.7,
                },
            }),
            signal: AbortSignal.timeout(config.timeoutMs),
        });
    } catch (error) {
        if (
            error instanceof Error &&
            (error.name === 'TimeoutError' || error.name === 'AbortError')
        ) {
            throw new OllamaError(
                `Ollama request timed out after ${config.timeoutMs} ms.`,
                'timeout'
            );
        }

        throw new OllamaError(
            `Unable to connect to Ollama at ${config.baseUrl}.`,
            'connection'
        );
    }

    if (!response.ok) {
        const detail = await readErrorMessage(response);

        if (response.status === 404) {
            throw new OllamaError(
                `The configured Ollama model "${config.model}" is not available.`,
                'model_not_found',
                response.status
            );
        }

        throw new OllamaError(
            detail
                ? `Ollama request failed: ${detail}`
                : `Ollama request failed with HTTP ${response.status}.`,
            'request_failed',
            response.status
        );
    }

    let payload: unknown;

    try {
        payload = await response.json();
    } catch {
        throw new OllamaError(
            'Ollama returned an invalid response.',
            'invalid_output'
        );
    }

    const content =
        payload &&
            typeof payload === 'object' &&
            !Array.isArray(payload) &&
            typeof (payload as { message?: { content?: unknown } }).message
                ?.content === 'string'
            ? (payload as { message: { content: string } }).message.content
            : null;

    if (!content) {
        throw new OllamaError(
            'Ollama returned an empty response.',
            'invalid_output'
        );
    }

    try {
        return asCopy(JSON.parse(content));
    } catch (error) {
        if (error instanceof OllamaError) {
            throw error;
        }

        throw new OllamaError(
            'Ollama returned malformed copy JSON.',
            'invalid_output'
        );
    }
}

export function getOllamaErrorMessage(error: unknown): string | null {
    return error instanceof OllamaError ? error.message : null;
}
