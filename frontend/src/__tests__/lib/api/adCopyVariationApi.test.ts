import api from '@/lib/api';
import { updateVariation } from '@/lib/api/adCopyVariationApi';

jest.mock('@/lib/api', () => ({
  __esModule: true,
  default: { patch: jest.fn() },
}));

const mockApi = api as jest.Mocked<typeof api>;

describe('updateVariation', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  const invalidSlugs: Array<string | undefined> = [undefined, '', '   '];

  it.each(invalidSlugs)('rejects invalid draft slug %p without sending a PATCH', async (slug) => {
    await expect(updateVariation(slug as string, { headline: 'Edited headline' })).rejects.toThrow(/slug/i);
    expect(mockApi.patch).not.toHaveBeenCalled();
  });

  it('patches the draft by slug and returns the updated draft', async () => {
    const fields = { headline: 'Edited headline' };
    const draft = { id: 27, slug: 'summer-offer', ...fields };
    mockApi.patch.mockResolvedValueOnce({ data: draft });

    const updated = await updateVariation(draft.slug, fields);

    expect(mockApi.patch).toHaveBeenCalledWith('/api/ad_copy_variation/variations/summer-offer/', fields);
    expect(updated).toEqual(draft);
  });
});
