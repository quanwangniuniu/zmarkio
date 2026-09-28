import { createCopyGenerator } from '@/src/ai';
import { callOllamaJson, getOllamaConfig, } from '@/src/ai/providers/ollama';
import type { CopyGenerator, CopyJson, } from '@/src/ai/types';

jest.mock('@/src/ai/providers/ollama', () => ({
  callOllamaJson: jest.fn(),
  getOllamaConfig: jest.fn(() => ({
    baseUrl: 'http://ollama.test:11434',
    model: 'test-model',
    timeoutMs: 5000,
  })),
}));

const ollamaMock = callOllamaJson as jest.MockedFunction<
  typeof callOllamaJson
>;

const configMock = getOllamaConfig as jest.MockedFunction<
  typeof getOllamaConfig
>;

describe('CopyGenerator', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    configMock.mockReturnValue({
      baseUrl: 'http://ollama.test:11434',
      model: 'test-model',
      timeoutMs: 5000,
    });
  });

  it('delegates to the Ollama provider', async () => {
    const copy: CopyJson = {
      hook: 'h',
      headline: 'headline',
      description: 'desc',
      cta: 'LEARN_MORE',
    };

    ollamaMock.mockResolvedValueOnce(copy);

    const generator = createCopyGenerator();
    const result = await generator.generateCopy('system', 'user');

    expect(result).toEqual(copy);
    expect(generator.modelName).toBe('test-model');
    expect(ollamaMock).toHaveBeenCalledWith('system', 'user');
  });

  it('accepts an injected mock generator without touching Ollama', async () => {
    const injected: CopyGenerator = {
      modelName: 'test-model',
      generateCopy: jest.fn(async () => ({
        hook: 'injected',
        headline: 'injected hl',
        description: 'injected desc',
        cta: 'SHOP_NOW',
      })),
      getErrorMessage: () => null,
    };

    const result = await injected.generateCopy('sys', 'usr');

    expect(result.hook).toBe('injected');
    expect(ollamaMock).not.toHaveBeenCalled();
  });
});
