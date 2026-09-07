/**
 * Re-export barrel — preserves backward compatibility.
 * Actual implementations live in src/ai/prompts/.
 */
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
} from '@/src/ai/prompts';
export type { CopyJson } from '@/src/ai/types';
