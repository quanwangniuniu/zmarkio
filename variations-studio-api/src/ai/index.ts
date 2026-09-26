import type { CopyGenerator } from './types';
import { callOllamaJson, getOllamaConfig, getOllamaErrorMessage, } from './providers/ollama';

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
} from './prompts';

export {
  OllamaError,
  callOllamaJson,
  getOllamaConfig,
  getOllamaErrorMessage,
} from './providers/ollama';

export {
  GeminiError,
  callGeminiJson,
  isGeminiQuotaError,
  geminiCopyGenerator,
} from './providers/gemini';

/**
 * Build the default CopyGenerator for Ads Generation.
 * Configuration is read lazily so unrelated endpoints can start even when
 * Ollama has not been configured.
 */
export function createCopyGenerator(): CopyGenerator {
  return {
    get modelName() {
      return getOllamaConfig().model;
    },
    generateCopy: (systemPrompt, userPrompt) =>
      callOllamaJson(systemPrompt, userPrompt),
    getErrorMessage: getOllamaErrorMessage,
  };
}

export const defaultCopyGenerator: CopyGenerator = createCopyGenerator();
