import {
  creativeToTemplate,
  loadCreativeForProject,
  parseCreativeId,
} from '@/lib/creatives';
import { buildUserPrompt } from '@/src/ai/prompts';
import { metaSpec } from '@/src/platforms/meta';

import type { GenerateContext, SourceModeHandler, SourceModeResult } from '../types';

export const existingMode: SourceModeHandler = {
  mode: 'existing',
  async resolve(ctx: GenerateContext, body: Record<string, unknown>): Promise<SourceModeResult> {
    const loaded = await loadCreativeForProject(
      parseCreativeId(body.creative_id),
      ctx.projectId,
      ctx.organizationId
    );
    return {
      userPrompt: buildUserPrompt(creativeToTemplate(loaded), ctx.instruction, ctx.spec, metaSpec.fields),
      creativeId: loaded.id,
      sourceRef: '',
    };
  },
};
