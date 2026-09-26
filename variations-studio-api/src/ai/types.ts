export type CopyJson = {
  hook: string;
  headline: string;
  description: string;
  cta: string;
};

/**
 * Pluggable AI copy provider. Domain code depends on this interface only —
 * swap Gemini for another LLM (or a test mock) without touching generate modes.
 * Returns parsed text; platform rules are applied by the generation domain.
 */
export interface CopyGenerator {
  readonly modelName: string;
  generateCopy(systemPrompt: string, userPrompt: string): Promise<CopyJson>;
  getErrorMessage(err: unknown): string | null;
}
