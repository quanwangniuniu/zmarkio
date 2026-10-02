import {
  creativeToTemplate,
  loadCreativeForProject,
  parseCreativeId,
} from '@/lib/creatives';
import { buildUserPrompt } from '@/src/ai/prompts';

import type { GenerateContext, SourceModeHandler, SourceModeResult } from '../types';

export const existingMode: SourceModeHandler = {
  mode: 'existing',
  async resolve(ctx: GenerateContext, body: Record<string, unknown>): Promise<SourceModeResult> {
    const loaded = await loadCreativeForProject(
      parseCreativeId(body.creative_id),
      ctx.projectId
    );
    const template = creativeToTemplate(loaded);
    return {
      userPrompt: buildUserPrompt(template, ctx.instruction),
      creativeId: loaded.id,
      sourceRef: '',
      sourceCopy: template,
    };
  },
};
