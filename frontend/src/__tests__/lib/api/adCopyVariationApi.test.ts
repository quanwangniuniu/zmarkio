import api, { LLM_BATCH_TIMEOUT_MS } from '@/lib/api';
import { generateVariation, listAiVariations, listPlatforms, updateVariation } from '@/lib/api/adCopyVariationApi';

jest.mock('@/lib/api', () => ({
  __esModule: true,
  LLM_BATCH_TIMEOUT_MS: 1234,
  default: { get: jest.fn(), post: jest.fn(), patch: jest.fn() },
}));

const mockApi = api as jest.Mocked<typeof api>;

it('retrieves the ordered platform specifications', async () => {
  const platforms = [{
    id: 'text', display_name: 'Text', fields: [
      { key: 'ad_text', label: 'Ad text', required: true, type: 'string', limits: { max_chars: 100 } },
    ], cta: { kind: 'none' },
  }];
  mockApi.get.mockResolvedValueOnce({ data: { platforms } });

  await expect(listPlatforms()).resolves.toEqual(platforms);
  expect(mockApi.get).toHaveBeenCalledWith('/api/ad_copy_variation/platforms/');
});

it.each([
  { label: 'with a platform', filters: { platform: 'text', batch_id: 'batch-1' } },
  { label: 'without a platform', filters: {} },
])('lists drafts $label', async ({ filters }) => {
  mockApi.get.mockResolvedValueOnce({ data: { results: [], count: 0 } });
  const params = { project_id: 7, ...filters };

  await listAiVariations(params);

  expect(mockApi.get).toHaveBeenLastCalledWith('/api/ad_copy_variation/variations/', { params });
});

it('sends the target platform and its custom source fields when generating', async () => {
  const request = { project_id: 7, source_mode: 'custom' as const, platform: 'text', base_copy: { ad_text: 'Source' } };
  const draft = { id: 27, slug: 'offer', status: 'draft', platform: 'text', fields: { ad_text: 'Offer' } };
  const batch = {
    batch_id: 'batch-1', count_requested: 1, count_succeeded: 1, count_failed: 0,
    results: [draft], failed_indices: [],
  };
  mockApi.post.mockResolvedValueOnce({ data: batch });

  await expect(generateVariation(request)).resolves.toEqual(batch);
  expect(mockApi.post).toHaveBeenCalledWith(
    '/api/ad_copy_variation/variations/generate/', { ...request, count: 1 }, { timeout: LLM_BATCH_TIMEOUT_MS }
  );
});

describe('updateVariation', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  const invalidSlugs: Array<string | undefined> = [undefined, '', '   '];

  it.each(invalidSlugs)('rejects invalid draft slug %p without sending a PATCH', async (slug) => {
    await expect(updateVariation(slug as string, { fields: { headline: 'Edited headline' } })).rejects.toThrow(/slug/i);
    expect(mockApi.patch).not.toHaveBeenCalled();
  });

  it('patches the draft by slug and returns the updated draft', async () => {
    const fields = { ad_text: 'Edited text' };
    const draft = { id: 27, slug: 'summer-offer', platform: 'text', fields };
    mockApi.patch.mockResolvedValueOnce({ data: draft });

    const updated = await updateVariation(draft.slug, { fields });

    expect(mockApi.patch).toHaveBeenCalledWith('/api/ad_copy_variation/variations/summer-offer/', { fields });
    expect(updated).toEqual(draft);
  });
});
