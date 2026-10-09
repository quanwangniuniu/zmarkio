import { requireProjectForUser } from '@/lib/projects';
import { buildUserPrompt } from '@/src/ai/prompts';
import type { CopyGenerator, CopyJson } from '@/src/ai/types';
import { runCustomGenerate } from '@/src/domains/generate/orchestrator';
import { getPlatformSpec, type PlatformSpec } from '@/src/platforms';
import { metaSpec } from '@/src/platforms/meta';
import { insertVariations } from '@/src/repo';

jest.mock('@/lib/projects', () => ({
  ...jest.requireActual('@/lib/projects'),
  requireProjectForUser: jest.fn(),
}));
jest.mock('@/src/platforms', () => ({
  ...jest.requireActual('@/src/platforms'),
  getPlatformSpec: jest.fn(),
}));
jest.mock('@/src/repo', () => ({ insertVariations: jest.fn() }));

describe('platform configuration in generation', () => {
  it('takes the prompt, version, CTA policy and slug source from the platform spec', async () => {
    const spec: PlatformSpec = {
      ...metaSpec,
      promptFragment: 'Use the alternate platform prompt.',
      promptVersion: 'alternate-v2',
      cta: { kind: 'enum', field: 'cta', values: ['JOIN_US', 'SKIP'], normalization: 'exact', fallback: 'SKIP' },
      slugSource: { field: 'hook' },
    };
    const baseCopy: CopyJson = {
      hook: 'Source hook', headline: 'Source headline', description: 'Source description', cta: 'SHOP_NOW',
    };
    const generateCopy = jest.fn<Promise<CopyJson>, [string, string]>()
      .mockResolvedValue({ ...baseCopy, hook: 'Hook for the slug', cta: 'join us' });
    const generator: CopyGenerator = { modelName: 'test-model', generateCopy, getErrorMessage: () => null };

    jest.mocked(getPlatformSpec).mockReturnValue(spec);
    jest.mocked(requireProjectForUser).mockResolvedValue({ ok: true, projectId: BigInt(12) });
    jest.mocked(insertVariations).mockImplementation(async (_schema, rows) => rows.map((row, index) => ({
      ...row,
      id: BigInt(index + 1),
      createdAt: new Date('2026-01-01T00:00:00Z'),
      updatedAt: new Date('2026-01-01T00:00:00Z'),
      isDeleted: false,
    })));

    const result = await runCustomGenerate({
      schema: 'test_tenant',
      userId: 7,
      body: { project_id: '12', source_mode: 'custom', base_copy: baseCopy, instruction: 'Focus on value' },
      generator,
    });

    expect(getPlatformSpec).toHaveBeenCalledWith('meta');
    expect(generateCopy).toHaveBeenCalledWith(spec.promptFragment, buildUserPrompt(baseCopy, 'Focus on value'));
    expect(result.results[0]).toMatchObject({
      cta: 'SKIP', prompt_version: 'alternate-v2', slug: 'hook-for-the-slug',
    });
  });
});
