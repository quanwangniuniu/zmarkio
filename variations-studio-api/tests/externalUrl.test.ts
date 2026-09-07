import { POST as generate } from '@/app/api/ad_copy_variation/variations/generate/route';
import { callGeminiJson } from '@/src/ai/providers/gemini';
import { prisma } from '@/lib/prisma';
import { BrowserlessError, fetchUrlText } from '@/lib/urlFetch';
import { findVariationById } from '@/lib/variationStore';

import {
  setupStudioFixture,
  teardownStudioFixture,
  type StudioFixture,
} from './support/fixtures';
import { readJson, studioRequest } from './support/requests';
import { accessToken } from './support/tokens';

jest.mock('@/src/ai/providers/gemini', () => ({
  callGeminiJson: jest.fn(),
  isGeminiQuotaError: jest.fn(() => false),
}));

// parseExternalUrl is the validation under test here, so only the network hop
// is replaced.
jest.mock('@/lib/urlFetch', () => {
  const actual = jest.requireActual('@/lib/urlFetch');
  return { ...actual, fetchUrlText: jest.fn() };
});

const geminiMock = callGeminiJson as jest.MockedFunction<typeof callGeminiJson>;
const fetchMock = fetchUrlText as jest.MockedFunction<typeof fetchUrlText>;

let fixture: StudioFixture;
let token: string;

beforeAll(async () => {
  fixture = await setupStudioFixture();
  token = await accessToken(fixture.memberUserId);
});

afterAll(async () => {
  await teardownStudioFixture(fixture);
  await prisma.$disconnect();
});

beforeEach(() => {
  jest.clearAllMocks();
  fetchMock.mockResolvedValue('Landing page copy about a product.');
  geminiMock.mockResolvedValue({
    hook: 'URL hook',
    headline: 'URL headline',
    description: 'URL description',
    cta: 'LEARN_MORE',
  });
});

function generateExternal(overrides: Record<string, unknown> = {}) {
  return generate(
    studioRequest('/api/ad_copy_variation/variations/generate/', {
      token,
      body: {
        project_id: String(fixture.projectA),
        source_mode: 'external_url',
        count: 1,
        url: 'https://example.com/landing',
        ...overrides,
      },
    })
  );
}

describe('generate with source_mode=external_url', () => {
  it('persists a draft carrying the url as source_ref', async () => {
    const response = await generateExternal();

    expect(response.status).toBe(200);
    const body = await readJson(response);
    expect(body).toMatchObject({ count_requested: 1, count_succeeded: 1 });
    expect(fetchMock).toHaveBeenCalledWith('https://example.com/landing');

    const results = body.results as { id: number }[];
    const row = await findVariationById(fixture.schema, BigInt(results[0].id));
    expect(row?.sourceMode).toBe('external_url');
    expect(row?.sourceRef).toBe('https://example.com/landing');
  });

  it('rejects a missing url before calling out', async () => {
    const response = await generateExternal({ url: undefined });

    expect(response.status).toBe(400);
    await expect(readJson(response)).resolves.toEqual({
      error: 'url required for source_mode=external_url',
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('rejects a non-http scheme', async () => {
    const response = await generateExternal({ url: 'ftp://example.com/file' });

    expect(response.status).toBe(400);
    await expect(readJson(response)).resolves.toEqual({
      error: 'url must be http or https',
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('returns 502 when the page cannot be fetched', async () => {
    fetchMock.mockRejectedValue(new BrowserlessError('Browserless fetch failed'));

    const response = await generateExternal();

    expect(response.status).toBe(502);
    const body = await readJson(response);
    expect(body).toMatchObject({ count_requested: 1, count_succeeded: 0 });
    expect(geminiMock).not.toHaveBeenCalled();
  });

  it('returns 502 when the model fails after a successful fetch', async () => {
    geminiMock.mockRejectedValue(new Error('model exploded'));

    const response = await generateExternal();

    expect(response.status).toBe(502);
    await expect(readJson(response)).resolves.toMatchObject({
      count_requested: 1,
      count_succeeded: 0,
      count_failed: 1,
    });
  });
});
