import { MODEL_NAME, lockCta } from '@/src/ai/prompts';
import type { CopyGenerator, CopyJson } from '@/src/ai/types';

const VERTEX_BASE = 'https://aiplatform.googleapis.com/v1/publishers/google/models';
const TIMEOUT_MS = 60_000;
const RATE_LIMIT_BACKOFF_MS = [2000, 4000];

export class GeminiError extends Error {
  status?: number;

  constructor(message: string, status?: number) {
    super(message);
    this.name = 'GeminiError';
    this.status = status;
  }
}

export function isGeminiQuotaError(err: unknown): boolean {
  return err instanceof GeminiError && err.status === 429;
}

function stripJsonFences(text: string): string {
  let stripped = text.trim();
  if (stripped.startsWith('```')) {
    const firstNewline = stripped.indexOf('\n');
    if (firstNewline !== -1) stripped = stripped.slice(firstNewline + 1);
    if (stripped.endsWith('```')) stripped = stripped.slice(0, -3);
  }
  return stripped.trim();
}

function asCopy(raw: unknown): CopyJson {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new GeminiError('Gemini returned non-object JSON');
  }
  const row = raw as Record<string, unknown>;
  return {
    hook: typeof row.hook === 'string' ? row.hook : '',
    headline: typeof row.headline === 'string' ? row.headline : '',
    description: typeof row.description === 'string' ? row.description : '',
    cta: lockCta(typeof row.cta === 'string' ? row.cta : ''),
  };
}

function parseStreamPayload(raw: string): {
  candidates?: Array<{ content?: { parts?: Array<{ text?: string }> } }>;
} {
  const trimmed = raw.trim();
  try {
    const parsed = JSON.parse(trimmed) as unknown;
    const chunks = Array.isArray(parsed) ? parsed : [parsed];
    const text = chunks
      .flatMap((chunk) => {
        const candidates =
          chunk && typeof chunk === 'object'
            ? (chunk as { candidates?: Array<{ content?: { parts?: Array<{ text?: string }> } }> }).candidates
            : undefined;
        return (candidates ?? []).flatMap((candidate) =>
          (candidate.content?.parts ?? []).map((part) => part.text ?? '')
        );
      })
      .join('');
    return { candidates: [{ content: { parts: [{ text }] } }] };
  } catch {
    const parts: string[] = [];
    for (const line of trimmed.split('\n')) {
      const data = line.trim().startsWith('data:') ? line.trim().slice(5).trim() : '';
      if (!data) continue;
      try {
        const obj = JSON.parse(data) as {
          candidates?: Array<{ content?: { parts?: Array<{ text?: string }> } }>;
        };
        for (const candidate of obj.candidates ?? []) {
          for (const part of candidate.content?.parts ?? []) {
            if (part.text) parts.push(part.text);
          }
        }
      } catch {
        // skip malformed SSE line
      }
    }
    return { candidates: [{ content: { parts: [{ text: parts.join('') }] } }] };
  }
}

async function postVertex(systemPrompt: string, userPrompt: string): Promise<unknown> {
  const apiKey = process.env.GEMINI_API_KEY;
  if (!apiKey) {
    throw new GeminiError('GEMINI_API_KEY is not configured');
  }

  const url = `${VERTEX_BASE}/${MODEL_NAME}:streamGenerateContent?key=${encodeURIComponent(apiKey)}`;
  const body = {
    systemInstruction: { parts: [{ text: systemPrompt }] },
    contents: [{ role: 'user', parts: [{ text: userPrompt }] }],
    generationConfig: {
      temperature: 0.7,
      responseMimeType: 'application/json',
    },
  };

  let lastStatus: number | undefined;
  for (let attempt = 0; attempt <= RATE_LIMIT_BACKOFF_MS.length; attempt += 1) {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    lastStatus = response.status;
    if (response.status === 429 && attempt < RATE_LIMIT_BACKOFF_MS.length) {
      await new Promise((resolve) => {
        setTimeout(resolve, RATE_LIMIT_BACKOFF_MS[attempt]);
      });
      continue;
    }
    if (!response.ok) {
      const snippet = (await response.text()).slice(0, 200);
      console.error('Vertex Gemini failed status=%s body=%s', response.status, snippet);
      throw new GeminiError(`Gemini request failed with HTTP ${response.status}.`, response.status);
    }
    const raw = await response.text();
    return parseStreamPayload(raw);
  }
  throw new GeminiError('Gemini rate limited (HTTP 429).', lastStatus);
}

export async function callGeminiJson(
  systemPrompt: string,
  userPrompt: string,
  retryParse = true
): Promise<CopyJson> {
  const data = (await postVertex(systemPrompt, userPrompt)) as {
    candidates?: Array<{ content?: { parts?: Array<{ text?: string }> } }>;
  };
  if (!data.candidates?.length) {
    throw new GeminiError('Gemini returned no candidates');
  }
  const text = stripJsonFences(
    (data.candidates?.[0]?.content?.parts ?? []).map((part) => part.text ?? '').join('')
  );
  try {
    return asCopy(JSON.parse(text));
  } catch (err) {
    if (retryParse) {
      return callGeminiJson(systemPrompt, userPrompt, false);
    }
    throw err;
  }
}

/** Default production CopyGenerator backed by Vertex Gemini. */
export const geminiCopyGenerator: CopyGenerator = {
  modelName: MODEL_NAME,
  generateCopy(systemPrompt, userPrompt) {
    return callGeminiJson(systemPrompt, userPrompt);
  },
  isQuotaError: isGeminiQuotaError,
};
