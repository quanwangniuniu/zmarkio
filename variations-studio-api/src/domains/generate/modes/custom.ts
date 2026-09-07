import { buildUserPrompt } from '@/src/ai/prompts';
import type { CopyJson } from '@/src/ai/types';

import type { GenerateContext, SourceModeHandler, SourceModeResult } from '../types';

function parseBaseCopy(raw: unknown): CopyJson {
  const obj =
    raw && typeof raw === 'object' && !Array.isArray(raw)
      ? (raw as Record<string, unknown>)
      : {};
  return {
    hook: typeof obj.hook === 'string' ? obj.hook : '',
    headline: typeof obj.headline === 'string' ? obj.headline : '',
    description: typeof obj.description === 'string' ? obj.description : '',
    cta: typeof obj.cta === 'string' ? obj.cta : '',
  };
}

export const customMode: SourceModeHandler = {
  mode: 'custom',
  async resolve(ctx: GenerateContext, body: Record<string, unknown>): Promise<SourceModeResult> {
    return {
      userPrompt: buildUserPrompt(parseBaseCopy(body.base_copy), ctx.instruction),
      creativeId: null,
      sourceRef: '',
    };
  },
};
