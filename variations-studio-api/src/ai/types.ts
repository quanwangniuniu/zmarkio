import type { PlatformSpec } from '@/src/platforms/types';

export type CopyJson = Record<string, string>;

/**
 * Pluggable AI copy provider. Domain code depends on this interface only —
 * swap Ollama for another LLM (or a test mock) without touching generate modes.
 * Returns parsed text; platform rules are applied by the generation domain.
 */
export interface CopyGenerator {
  readonly modelName: string;
  generateCopy(systemPrompt: string, userPrompt: string, spec: PlatformSpec): Promise<CopyJson>;
  getErrorMessage(err: unknown): string | null;
}
