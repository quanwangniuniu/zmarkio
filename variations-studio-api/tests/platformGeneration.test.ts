import { requireProjectForUser } from '@/lib/projects';
import { createVariation } from '@/lib/variationCreate';
import { buildSystemPrompt, buildUserPrompt } from '@/src/ai/prompts';
import type { CopyGenerator, CopyJson } from '@/src/ai/types';
import { runCustomGenerate } from '@/src/domains/generate/orchestrator';
import { createPlatformRegistry, getPlatformSpec, listPlatformIds, type PlatformSpec } from '@/src/platforms';
import { metaSpec } from '@/src/platforms/meta';
import { insertVariation, insertVariations } from '@/src/repo';
import { testTextSpec } from './support/textPlatform';

jest.mock('@/lib/projects', () => ({
  ...jest.requireActual('@/lib/projects'),
  requireProjectForUser: jest.fn(),
}));
jest.mock('@/src/platforms', () => ({
  ...jest.requireActual('@/src/platforms'),
  getPlatformSpec: jest.fn(),
  listPlatformIds: jest.fn(),
}));
jest.mock('@/src/repo', () => ({ insertVariation: jest.fn(), insertVariations: jest.fn() }));

describe('platform configuration in generation and creation', () => {
  const registry = createPlatformRegistry([metaSpec, testTextSpec]);
  beforeEach(() => {
    jest.mocked(listPlatformIds).mockImplementation(registry.listPlatformIds);
    jest.mocked(getPlatformSpec).mockImplementation(registry.getPlatformSpec);
    jest.mocked(requireProjectForUser).mockResolvedValue({ ok: true, projectId: BigInt(12) });
  });

  it('takes the prompt, version, CTA policy and slug source from the platform spec', async () => {
    const spec: PlatformSpec = {
      ...metaSpec,
      fields: [...metaSpec.fields].reverse(),
      promptFragment: 'Use the alternate platform prompt.\n\n',
      promptVersion: 'alternate-v2',
      cta: { kind: 'enum', field: 'cta', values: ['JOIN_US', 'SKIP'], normalization: 'exact', fallback: 'SKIP' },
      slugSource: { field: 'hook' },
    };
    const baseCopy: CopyJson = {
      hook: 'Source hook', headline: 'Source headline', description: 'Source description', cta: 'SHOP_NOW',
    };
    const generateCopy = jest.fn<Promise<CopyJson>, Parameters<CopyGenerator['generateCopy']>>()
      .mockResolvedValue({ ...baseCopy, hook: 'Hook for the slug', cta: 'join us' });
    const generator: CopyGenerator = { modelName: 'test-model', generateCopy, getErrorMessage: () => null };

    jest.mocked(getPlatformSpec).mockReturnValue(spec);
    jest.mocked(insertVariations).mockImplementation(async (_schema, rows) => rows.map((row, index) => ({
      ...row,
      hook: row.fields.hook,
      headline: row.fields.headline,
      description: row.fields.description,
      cta: row.fields.cta,
      id: BigInt(index + 1),
      createdAt: new Date('2026-01-01T00:00:00Z'),
      updatedAt: new Date('2026-01-01T00:00:00Z'),
      isDeleted: false,
    })));

    const result = await runCustomGenerate({
      schema: 'test_tenant',
      organizationId: BigInt(3),
      userId: 7,
      body: { project_id: '12', source_mode: 'custom', base_copy: baseCopy, instruction: 'Focus on value' },
      generator,
    });

    expect(getPlatformSpec).toHaveBeenCalledWith('meta');
    expect(generateCopy).toHaveBeenCalledWith(
      buildSystemPrompt(spec), buildUserPrompt(baseCopy, 'Focus on value', spec), spec
    );
    expect(result.results[0]).toMatchObject({
      fields: { cta: 'SKIP' }, prompt_version: 'alternate-v2', slug: 'hook-for-the-slug',
    });
  });

  it('uses the requested platform for custom source fields, retry feedback and the saved draft', async () => {
    const baseCopy = { ad_text: 'Source ad', action: 'JOIN_US', tagline: 'Source tagline' };
    const generateCopy = jest.fn<Promise<CopyJson>, Parameters<CopyGenerator['generateCopy']>>()
      .mockResolvedValueOnce({ ad_text: 'a'.repeat(31), action: 'JOIN_US' })
      .mockResolvedValueOnce({ ad_text: 'A shorter offer', action: 'Unknown action' });
    jest.mocked(insertVariations).mockResolvedValueOnce([]);

    await runCustomGenerate({
      schema: 'test_tenant', organizationId: BigInt(3), userId: 7,
      body: { project_id: '12', platform: testTextSpec.id, source_mode: 'custom', base_copy: baseCopy },
      generator: { modelName: 'test-model', generateCopy, getErrorMessage: () => null },
    });

    const [system, prompt, spec] = generateCopy.mock.calls[0];
    expect(spec).toEqual(testTextSpec);
    expect(system).toBe(buildSystemPrompt(testTextSpec));
    expect(prompt).toContain('- Ad text: Source ad');
    expect(prompt).toContain('- Tagline: Source tagline');
    expect(generateCopy).toHaveBeenNthCalledWith(
      2, system, expect.stringContaining('ad_text: 31 characters (maximum 30)'), testTextSpec
    );
    expect(insertVariations).toHaveBeenLastCalledWith('test_tenant', [expect.objectContaining({
      platform: testTextSpec.id, slug: 'a-shorter-offer', promptVersion: testTextSpec.promptVersion,
      fields: { ad_text: 'A shorter offer', action: 'SKIP', tagline: '' },
    })]);
  });

  it('propagates unsupported validation rules before saving any item', async () => {
    jest.mocked(getPlatformSpec).mockReturnValue({
      ...metaSpec,
      fields: [...metaSpec.fields, {
        key: 'wide_title', label: 'Wide title', type: 'string', required: false,
        limits: { maxChars: 30, characterCounting: 'double-width' },
      }],
    });
    jest.mocked(insertVariations).mockClear();
    const copy = { hook: 'Hook', headline: 'Headline', description: 'Description', cta: 'SHOP_NOW' };
    const generateCopy = jest.fn()
      .mockResolvedValueOnce(copy)
      .mockResolvedValueOnce({ ...copy, wide_title: 'Offer' });
    const getErrorMessage = jest.fn(() => null);

    await expect(runCustomGenerate({
      schema: 'test_tenant', organizationId: BigInt(3), userId: 7,
      body: { project_id: '12', source_mode: 'custom', count: 2, base_copy: copy },
      generator: { modelName: 'test-model', generateCopy, getErrorMessage },
    })).rejects.toThrow('Unsupported copy validation rules for meta.wide_title');
    expect(getErrorMessage).not.toHaveBeenCalled();
    expect(insertVariations).not.toHaveBeenCalled();
  });

  it.each([
    { label: 'default', input: {}, expected: 'alternate-v2' },
    { label: 'explicit', input: { prompt_version: 'request-v3' }, expected: 'request-v3' },
  ])('uses the platform slug source and $label prompt version for manual create', async ({ input, expected }) => {
    jest.mocked(getPlatformSpec).mockReturnValue({
      ...metaSpec,
      promptVersion: 'alternate-v2',
      slugSource: { field: 'hook' },
    });

    await createVariation({
      schema: 'test_tenant',
      organizationId: BigInt(3),
      projectId: BigInt(12),
      userId: 7,
      body: { source_mode: 'custom', hook: 'Hook for manual slug', headline: 'Unused headline', ...input },
    });

    expect(insertVariation).toHaveBeenLastCalledWith('test_tenant', expect.objectContaining({
      slug: 'hook-for-manual-slug',
      promptVersion: expected,
    }));
  });
});
