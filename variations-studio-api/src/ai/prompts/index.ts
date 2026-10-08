export const MAX_BATCH = 50;
export const BATCH_CONCURRENCY = 5;
export const MODEL_NAME = 'qwen3:4b';
// Keep in sync with PROMPT_VERSION in backend/ad_copy_variation/services.py.
export const PROMPT_VERSION = 'v2';
export const AI_QUOTA_MESSAGE =
  'AI generation is temporarily rate-limited or quota-limited. Please wait '
  + 'a minute before generating more variations, or reduce the number of '
  + 'variations and try again.';

export function buildJsonShapePrompt(fieldKeys: readonly string[]): string {
  // Preserve spelled-out counts for prompt compatibility.
  const counts = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten'];
  const count = counts[fieldKeys.length] ?? String(fieldKeys.length);
  return (
    'OUTPUT JSON SHAPE\n'
    + `Return strict JSON with exactly these ${count} keys: ${fieldKeys.join(', ')}. `
    + 'No prose, no explanation, no fences.\n\n'
  );
}

export const SHARED_SYSTEM_RULES = (
  'OUTPUT LANGUAGE\n'
  + 'Detect the language of the source ad copy. Output every text field in '
  + 'THAT SAME LANGUAGE. If the source is English, output English. If the '
  + 'source is Chinese, output Chinese. If the source is Portuguese, output '
  + 'Portuguese. NEVER translate to a different language. The cta field is the '
  + 'only exception — it stays in the English uppercase enum format regardless '
  + 'of source language.\n\n'
  + 'DIVERSITY\n'
  + 'Each call should explore a different angle: a different value proposition, '
  + 'a different emotional hook, or a different sentence structure. Avoid '
  + 'producing variations that read as near-duplicates of the source or of an '
  + 'obvious literal rewrite. Surprise, contrast, urgency, social proof, and '
  + 'specific numbers are all valid angles to vary across calls.\n\n'
  + 'VOICE\n'
  + 'Preserve the source\'s offer, target audience, and tone. Do not invent '
  + 'product features, prices, or claims that are not implied by the source.'
);

export type { CopyJson } from '@/src/ai/types';
import type { CopyJson } from '@/src/ai/types';

export function buildExternalUrlPrompt(pageText: string, instruction: string): string {
  const focus = instruction.trim()
    || 'Rewrite all four fields with fresh phrasing, exploring a different angle than a literal rewrite. Preserve the source language. Respect the length caps and the cta enum lock.';
  return (
    'Below is the rendered text content of a public ad page. The page may '
    + 'contain navigation, ad library metadata, advertiser info, and unrelated '
    + 'boilerplate. Identify the actual ad copy inside it (typically: a short '
    + 'hook line, a headline, a body paragraph, and a call-to-action button '
    + 'label), then produce a NEW VARIATION of that ad copy following the '
    + "user's instruction.\n\n"
    + 'LANGUAGE LOCK (CRITICAL)\n'
    + 'Detect the language of the ad copy embedded in the page text below. '
    + 'Output every text field in THAT SAME LANGUAGE. NEVER drift to English '
    + 'unless the source ad copy is already English. If the page is in '
    + 'Portuguese, the output must be in Portuguese. The cta field stays in '
    + 'the English uppercase enum format regardless of source language.\n\n'
    + 'Apply all length caps, the cta enum lock, and the diversity rule from '
    + 'the system instructions to the new variation. Each value in the JSON '
    + 'must be the NEW VARIATION, not the extracted source.\n\n'
    + `Page text:\n---\n${pageText}\n---\n\n`
    + `Instruction: ${focus}\n\n`
    + 'Return strict JSON with keys: hook, headline, description, cta. '
    + 'No prose, no fences.'
  );
}

export function buildUserPrompt(template: CopyJson, instruction: string): string {
  const focus = instruction.trim()
    || 'Rewrite all four fields with fresh phrasing, exploring a different angle than a literal rewrite. Respect the length caps and the cta enum lock.';
  return (
    'Source ad (reference only; every text field you return must use NEW wording):\n'
    + `- Hook: ${template.hook}\n`
    + `- Headline: ${template.headline}\n`
    + `- Description: ${template.description}\n`
    + `- CTA: ${template.cta}\n\n`
    + `Instruction: ${focus}\n\n`
    + 'Write one new variation of the source ad.'
  );
}

/** Angles rotated by each variation's batch position; each reshapes facts already in the source. */
export const VARIATION_ANGLES = [
  'lead with the main benefit',
  'open with a short question about the problem',
  'lead with the offer already in the source ad',
  'describe how it feels to use the product',
];

export function withVariationAngle(userPrompt: string, index: number): string {
  return `${userPrompt}\nAngle: ${VARIATION_ANGLES[index % VARIATION_ANGLES.length]}.`;
}
