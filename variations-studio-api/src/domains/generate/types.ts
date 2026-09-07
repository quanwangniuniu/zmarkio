import type { CopyJson } from '@/src/ai/types';

export type GenerateContext = {
  schema: string;
  userId: number;
  projectId: bigint;
  instruction: string;
};

export type SourceModeResult = {
  userPrompt: string;
  creativeId: bigint | null;
  sourceRef: string;
};

export type SourceModeEarlyReturn = {
  earlyReturn: true;
  response: import('./orchestrator').GenerateBatchResponse;
};

export interface SourceModeHandler {
  readonly mode: string;
  resolve(
    ctx: GenerateContext,
    body: Record<string, unknown>
  ): Promise<SourceModeResult | SourceModeEarlyReturn>;
}

export function isEarlyReturn(
  result: SourceModeResult | SourceModeEarlyReturn
): result is SourceModeEarlyReturn {
  return 'earlyReturn' in result && result.earlyReturn === true;
}
