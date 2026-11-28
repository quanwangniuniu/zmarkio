import type { PlatformSpec } from '@/src/platforms/types';
import { formatCopyViolation, type CopyViolation } from '@/src/platforms/validateCopy';

export const MAX_BATCH = 50;
export const BATCH_CONCURRENCY = 5;
export const MODEL_NAME = 'qwen3:4b';
export const AI_QUOTA_MESSAGE =
  'AI generation is temporarily rate-limited or quota-limited. Please wait '
  + 'a minute before generating more variations, or reduce the number of '
  + 'variations and try again.';

// Preserve spelled-out counts for prompt compatibility.
const COUNT_WORDS = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten'];

function countWord(count: number): string {
  return COUNT_WORDS[count] ?? String(count);
}

function optionalKeys(spec: PlatformSpec): string[] {
  return spec.fields.filter(field => !field.required).map(field => field.key);
}

function jsonKeysInstruction(keys: readonly string[], optional: readonly string[], requiredPhrase: string): string {
  if (optional.length) {
    return `Return strict JSON using only these keys: ${keys.join(', ')}. `
      + `Optional keys: ${optional.join(', ')}. All other keys are required.`;
  }
  return `Return strict JSON ${requiredPhrase}: ${keys.join(', ')}.`;
}

export function buildJsonShapePrompt(fieldKeys: readonly string[], optional: readonly string[] = []): string {
  return (
    'OUTPUT JSON SHAPE\n'
    + jsonKeysInstruction(fieldKeys, optional, `with exactly these ${countWord(fieldKeys.length)} keys`)
    + ' No prose, no explanation, no fences.\n\n'
  );
}

export function buildRetryPrompt(userPrompt: string, violations: readonly CopyViolation[]): string {
  const limits = violations.map((violation) => `- ${formatCopyViolation(violation)}`);
  return `${userPrompt}\n\nThe previous variation exceeded these limits:\n${limits.join('\n')}`
    + '\nReturn a complete JSON variation that meets the limits.';
}

const DIVERSITY_AND_VOICE = (
  'DIVERSITY\n'
  + 'Each call should explore a different angle: a different value proposition, '
  + 'a different emotional hook, or a different sentence structure. Avoid '
  + 'producing variations that read as near-duplicates of the source or of an '
  + 'obvious literal rewrite. Surprise, contrast, urgency, social proof, and '
  + 'specific numbers are all valid angles to vary across calls.\n\n'
  + 'VOICE\n'
  + 'Preserve the source\'s offer, target audience, and tone. Do not invent '
  + 'product features, prices, or claims that are not implied by the source.'
);

function uppercaseCtaField({ cta }: PlatformSpec): string | undefined {
  return cta.kind === 'enum' && cta.normalization === 'trim-uppercase-underscores' ? cta.field : undefined;
}

export function buildSystemPrompt(spec: PlatformSpec): string {
  const uppercaseField = uppercaseCtaField(spec);
  const languageException = uppercaseField
    ? ` The ${uppercaseField} field is the only exception — it stays in the English uppercase enum format regardless of source language.`
    : '';
  return (
    spec.promptIntro
    + buildJsonShapePrompt(spec.fields.map((field) => field.key), optionalKeys(spec))
    + spec.promptFragment
    + 'OUTPUT LANGUAGE\n'
    + 'Detect the language of the source ad copy. Output every text field in '
    + 'THAT SAME LANGUAGE. If the source is English, output English. If the '
    + 'source is Chinese, output Chinese. If the source is Portuguese, output '
    + 'Portuguese. NEVER translate to a different language.'
    + languageException + '\n\n'
    + DIVERSITY_AND_VOICE
  );
}

export type { CopyJson } from '@/src/ai/types';

function defaultInstruction(spec: PlatformSpec, source: 'template' | 'url'): string {
  const count = spec.fields.length;
  const target = spec.fields.some(field => !field.required)
    ? 'each field you return'
    : count === 1 ? 'the field' : `all ${countWord(count)} fields`;
  const enumLock = spec.cta.kind === 'enum' ? ` and the ${spec.cta.field} enum lock` : '';
  return `Rewrite ${target} with fresh phrasing, exploring a different angle than a literal rewrite.`
    + (source === 'url' ? ' Preserve the source language.' : '')
    + ` Respect the length caps${enumLock}.`;
}

export function buildExternalUrlPrompt(pageText: string, instruction: string, spec: PlatformSpec): string {
  const focus = instruction.trim() || defaultInstruction(spec, 'url');
  const uppercaseField = uppercaseCtaField(spec);
  const languageException = uppercaseField
    ? ` The ${uppercaseField} field stays in the English uppercase enum format regardless of source language.`
    : '';
  const enumLock = spec.cta.kind === 'enum' ? `, the ${spec.cta.field} enum lock,` : '';
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
    + 'Portuguese, the output must be in Portuguese.'
    + languageException + '\n\n'
    + `Apply all length caps${enumLock} and the diversity rule from `
    + 'the system instructions to the new variation. Each value in the JSON '
    + 'must be the NEW VARIATION, not the extracted source.\n\n'
    + `Page text:\n---\n${pageText}\n---\n\n`
    + `Instruction: ${focus}\n\n`
    + jsonKeysInstruction(spec.fields.map((field) => field.key), optionalKeys(spec), 'with keys')
    + ' No prose, no fences.'
  );
}

function rewriteRequirement({ fields, cta }: PlatformSpec): string {
  const keys = fields.filter(field => field.required && (cta.kind !== 'enum' || field.key !== cta.field))
    .map(field => field.key);
  if (keys.length === 0) return '';
  if (keys.length === 1) return ` The ${keys[0]} must differ from the source.`;
  const list = `${keys.slice(0, -1).join(', ')} and ${keys[keys.length - 1]}`;
  return ` The ${list} must each differ from the source.`;
}

export function buildUserPrompt(
  template: Record<string, string>,
  instruction: string,
  spec: PlatformSpec,
  sourceFields = spec.fields
): string {
  const focus = instruction.trim() || defaultInstruction(spec, 'template');
  return (
    'Source ad (reference only; every text field you return must use NEW wording):\n'
    + sourceFields.map(({ key, label }) => `- ${label}: ${template[key] ?? ''}`).join('\n')
    + '\n\n'
    + `Instruction: ${focus}\n\n`
    + 'Write one new variation of the source ad.'
    + rewriteRequirement(spec)
  );
}
