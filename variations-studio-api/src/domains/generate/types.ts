import type { PlatformSpec } from '@/src/platforms';

export type GenerateContext = {
  schema: string;
  userId: number;
  projectId: bigint;
  count: number;
  instruction: string;
  spec: PlatformSpec;
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
