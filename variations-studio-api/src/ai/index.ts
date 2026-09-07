import type { CopyGenerator } from './types';
import { MODEL_NAME } from './prompts';
import { callGeminiJson, isGeminiQuotaError } from './providers/gemini';

export type { CopyGenerator, CopyJson } from './types';
export {
  MAX_BATCH,
  BATCH_CONCURRENCY,
  MODEL_NAME,
  PROMPT_VERSION,
  AI_QUOTA_MESSAGE,
  CTA_ENUM,
  CTA_ENUM_ALLOWLIST,
  SYSTEM_PROMPT,
  buildExternalUrlPrompt,
  buildUserPrompt,
  lockCta,
} from './prompts';
export {
  GeminiError,
  callGeminiJson,
  isGeminiQuotaError,
  geminiCopyGenerator,
} from './providers/gemini';

/**
 * Build the default CopyGenerator (Gemini today).
 * Uses live imports so Jest can mock `@/src/ai/providers/gemini` and still
 * exercise the real generate orchestrator path.
 */
export function createCopyGenerator(): CopyGenerator {
  return {
    modelName: MODEL_NAME,
    generateCopy: (systemPrompt, userPrompt) =>
      callGeminiJson(systemPrompt, userPrompt),
    isQuotaError: (err) => isGeminiQuotaError(err),
  };
}

export const defaultCopyGenerator: CopyGenerator = createCopyGenerator();
