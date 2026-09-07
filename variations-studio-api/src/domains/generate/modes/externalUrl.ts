import { randomUUID } from 'crypto';

import { buildExternalUrlPrompt } from '@/src/ai/prompts';
import { BrowserlessError, fetchUrlText, parseExternalUrl } from '@/lib/urlFetch';

import type { GenerateContext, SourceModeHandler, SourceModeResult, SourceModeEarlyReturn } from '../types';

export const externalUrlMode: SourceModeHandler = {
  mode: 'external_url',
  async resolve(
    ctx: GenerateContext,
    body: Record<string, unknown>
  ): Promise<SourceModeResult | SourceModeEarlyReturn> {
    const sourceRef = parseExternalUrl(body.url);
    try {
      const pageText = await fetchUrlText(sourceRef);
      return {
        userPrompt: buildExternalUrlPrompt(pageText, ctx.instruction),
        creativeId: null,
        sourceRef,
      };
    } catch (err) {
      const message =
        err instanceof BrowserlessError ? err.message : 'Browserless fetch failed';
      const count =
        typeof body.count === 'number' ? body.count : 1;
      return {
        earlyReturn: true,
        response: {
          batch_id: randomUUID(),
          count_requested: count,
          count_succeeded: 0,
          count_failed: count,
          results: [],
          failed_indices: Array.from({ length: count }, (_, i) => i),
          error: message,
        },
      };
    }
  },
};
