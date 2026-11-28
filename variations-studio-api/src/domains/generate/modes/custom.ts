import { buildUserPrompt } from '@/src/ai/prompts';
import type { PlatformSpec } from '@/src/platforms/types';

import type { GenerateContext, SourceModeHandler, SourceModeResult } from '../types';

function parseBaseCopy(raw: unknown, fields: PlatformSpec['fields']): Record<string, string> {
  const obj =
    raw && typeof raw === 'object' && !Array.isArray(raw)
      ? (raw as Record<string, unknown>)
      : {};
  return Object.fromEntries(fields.map(({ key }) => {
    const value = obj[key];
    return [key, typeof value === 'string' ? value : ''];
  }));
}

export const customMode: SourceModeHandler = {
  mode: 'custom',
  async resolve(ctx: GenerateContext, body: Record<string, unknown>): Promise<SourceModeResult> {
    return {
      userPrompt: buildUserPrompt(parseBaseCopy(body.base_copy, ctx.spec.fields), ctx.instruction, ctx.spec),
      creativeId: null,
      sourceRef: '',
    };
  },
};
