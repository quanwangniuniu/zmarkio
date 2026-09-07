/**
 * Re-export barrel — preserves backward compatibility.
 * Actual implementations live in src/ai/.
 */
export {
  GeminiError,
  callGeminiJson,
  isGeminiQuotaError,
  geminiCopyGenerator,
} from '@/src/ai';
