#!/usr/bin/env node
// MED-356: live smoke check for which Google Gemini endpoint accepts GEMINI_API_KEY.
//
// The Jest suite mocks the provider, so nothing else exercises a real URL. This
// sends one tiny request to each candidate endpoint and reports the HTTP status,
// so the key type (AI Studio vs Vertex express vs Vertex project) can be confirmed.
//
// Usage (from variations-studio-api/):
//   GEMINI_API_KEY=... npm run smoke:gemini
//   GEMINI_API_KEY=... node scripts/gemini-smoke.mjs --only=vertex-express-query
//
// Optional env:
//   GEMINI_MODEL           default gemini-2.5-flash-lite (matches src/ai/prompts.ts)
//   GEMINI_VERTEX_PROJECT  default 406201877905 (matches backend/core/services/gemini_client.py)
//   GEMINI_VERTEX_LOCATION default global
//
// Exit code: 0 if the endpoint the Node provider uses today succeeds, 1 otherwise,
// 2 on bad configuration. The key is never printed.

const TIMEOUT_MS = 20_000;

const apiKey = (process.env.GEMINI_API_KEY ?? '').trim();
const model = process.env.GEMINI_MODEL || 'gemini-2.5-flash-lite';
const project = process.env.GEMINI_VERTEX_PROJECT || '406201877905';
const location = process.env.GEMINI_VERTEX_LOCATION || 'global';

const AISTUDIO = 'https://generativelanguage.googleapis.com/v1beta';
const VERTEX = 'https://aiplatform.googleapis.com/v1';

const candidates = [
  {
    id: 'aistudio-header',
    usedBy: 'Django backend/ad_copy_variation/aistudio_client.py',
    url: `${AISTUDIO}/models/${model}:generateContent`,
    headers: { 'x-goog-api-key': apiKey },
  },
  {
    id: 'vertex-express-query',
    usedBy: 'Node variations-studio-api/src/ai/providers/gemini.ts (current)',
    url: `${VERTEX}/publishers/google/models/${model}:streamGenerateContent?key=${encodeURIComponent(apiKey)}`,
    headers: {},
    current: true,
  },
  {
    id: 'vertex-express-header',
    usedBy: 'candidate fix: Vertex express path, key in header',
    url: `${VERTEX}/publishers/google/models/${model}:generateContent`,
    headers: { 'x-goog-api-key': apiKey },
  },
  {
    id: 'vertex-project-query',
    usedBy: 'Django backend/core/services/gemini_client.py (agent pipeline)',
    url: `${VERTEX}/projects/${project}/locations/${location}/publishers/google/models/${model}:streamGenerateContent?key=${encodeURIComponent(apiKey)}`,
    headers: {},
  },
  {
    id: 'vertex-project-header',
    usedBy: 'candidate fix: Vertex project path, key in header',
    url: `${VERTEX}/projects/${project}/locations/${location}/publishers/google/models/${model}:streamGenerateContent`,
    headers: { 'x-goog-api-key': apiKey },
  },
];

function redact(text) {
  if (!apiKey) return text;
  return text.split(apiKey).join('<GEMINI_API_KEY>').split(encodeURIComponent(apiKey)).join('<GEMINI_API_KEY>');
}

function extractText(raw) {
  try {
    const parsed = JSON.parse(raw);
    const chunks = Array.isArray(parsed) ? parsed : [parsed];
    return chunks
      .flatMap((c) => c?.candidates ?? [])
      .flatMap((c) => c?.content?.parts ?? [])
      .map((p) => p?.text ?? '')
      .join('')
      .trim();
  } catch {
    return '';
  }
}

async function probe(candidate) {
  const started = Date.now();
  try {
    const response = await fetch(candidate.url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...candidate.headers },
      body: JSON.stringify({
        contents: [{ role: 'user', parts: [{ text: 'Reply with the single word: ok' }] }],
        generationConfig: { temperature: 0, maxOutputTokens: 5 },
      }),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    const raw = await response.text();
    const detail = response.ok
      ? `reply=${JSON.stringify(extractText(raw).slice(0, 40))}`
      : redact(raw.replace(/\s+/g, ' ').slice(0, 240));
    return { ok: response.ok, status: String(response.status), detail, ms: Date.now() - started };
  } catch (err) {
    return { ok: false, status: 'ERR', detail: redact(String(err?.message ?? err)), ms: Date.now() - started };
  }
}

async function main() {
  if (!apiKey) {
    console.error('GEMINI_API_KEY is not set. Export it (do not commit it) and re-run.');
    process.exit(2);
  }

  const only = process.argv.find((arg) => arg.startsWith('--only='))?.slice('--only='.length);
  const selected = only ? candidates.filter((c) => c.id === only) : candidates;
  if (!selected.length) {
    console.error(`Unknown --only value. Choose one of: ${candidates.map((c) => c.id).join(', ')}`);
    process.exit(2);
  }

  console.log(`key: prefix=${apiKey.slice(0, 4)}… length=${apiKey.length}  model=${model}`);
  console.log(`vertex project=${project} location=${location}\n`);

  let currentOk = null;
  for (const candidate of selected) {
    const result = await probe(candidate);
    if (candidate.current) currentOk = result.ok;
    console.log(`${result.ok ? 'PASS' : 'FAIL'}  ${candidate.id.padEnd(22)} HTTP ${result.status.padEnd(3)} ${result.ms}ms`);
    console.log(`      used by: ${candidate.usedBy}`);
    console.log(`      ${result.detail}\n`);
  }

  if (currentOk === null) return;
  console.log(currentOk
    ? 'Node provider endpoint works with this key.'
    : 'Node provider endpoint FAILS with this key: generate would return 502.');
  process.exit(currentOk ? 0 : 1);
}

main();
