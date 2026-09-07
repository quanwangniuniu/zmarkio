import type { CopyGenerator, CopyJson } from '@/src/ai/types';
import { createCopyGenerator } from '@/src/ai';
import { callGeminiJson, isGeminiQuotaError } from '@/src/ai/providers/gemini';

jest.mock('@/src/ai/providers/gemini', () => ({
  callGeminiJson: jest.fn(),
  isGeminiQuotaError: jest.fn(() => false),
  MODEL_NAME: 'gemini-2.5-flash-lite',
}));

const geminiMock = callGeminiJson as jest.MockedFunction<typeof callGeminiJson>;
const quotaMock = isGeminiQuotaError as jest.MockedFunction<
  typeof isGeminiQuotaError
>;

describe('CopyGenerator (Phase 2a)', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    quotaMock.mockReturnValue(false);
  });

  it('createCopyGenerator delegates to the Gemini provider', async () => {
    const copy: CopyJson = {
      hook: 'h',
      headline: 'headline',
      description: 'desc',
      cta: 'LEARN_MORE',
    };
    geminiMock.mockResolvedValueOnce(copy);

    const generator = createCopyGenerator();
    const result = await generator.generateCopy('system', 'user');

    expect(result).toEqual(copy);
    expect(geminiMock).toHaveBeenCalledWith('system', 'user');
  });

  it('accepts an injected mock generator without touching Gemini', async () => {
    const injected: CopyGenerator = {
      modelName: 'test-model',
      generateCopy: jest.fn(async () => ({
        hook: 'injected',
        headline: 'injected hl',
        description: 'injected desc',
        cta: 'SHOP_NOW',
      })),
      isQuotaError: () => false,
    };

    const result = await injected.generateCopy('sys', 'usr');
    expect(result.hook).toBe('injected');
    expect(geminiMock).not.toHaveBeenCalled();
  });
});
