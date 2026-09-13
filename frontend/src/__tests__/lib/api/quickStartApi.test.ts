import axios, { AxiosError, type InternalAxiosRequestConfig } from 'axios';
import api from '@/lib/api';
import { QuickStartAPI, getQuickStartErrorDetail } from '@/lib/api/quickStartApi';
import type { QuickStartBlueprint } from '@/types/quickStart';
import { QUICK_START_BLUEPRINT_VERSION } from '@/types/quickStart';

jest.mock('@/lib/api', () => ({
  // Keep the real named exports (timeout tier constants) — only the
  // default axios instance is mocked. Re-exporting everything by hand here
  // would silently drift from lib/api.ts whenever a tier is added/renamed.
  ...jest.requireActual('@/lib/api'),
  __esModule: true,
  default: {
    post: jest.fn(),
  },
}));

const mockedPost = api.post as jest.Mock;

const sampleBlueprint: QuickStartBlueprint = {
  version: QUICK_START_BLUEPRINT_VERSION,
  project: { name: 'Test Project' },
  tasks: [],
  spreadsheets: [],
  calendar_events: [],
  decisions: [],
  miro_boards: [],
};

describe('QuickStartAPI', () => {
  beforeEach(() => {
    mockedPost.mockReset();
  });

  it('preview step 1 posts without extended timeout', async () => {
    mockedPost.mockResolvedValue({
      data: { step: 1, outline: null, blueprint: null },
    });

    const result = await QuickStartAPI.preview({
      step: 1,
      prompt: 'US Meta Q2 campaign with $30k monthly budget',
    });

    expect(result.step).toBe(1);
    expect(mockedPost).toHaveBeenCalledWith(
      '/api/core/projects/quick-start/preview/',
      expect.objectContaining({ step: 1 }),
      undefined
    );
  });

  it('preview step 2 uses LLM timeout', async () => {
    mockedPost.mockResolvedValue({
      data: {
        step: 2,
        outline: { project: { title: 'A', description: '' } },
        blueprint: sampleBlueprint,
      },
    });

    await QuickStartAPI.preview({
      step: 2,
      prompt: 'US Meta Q2 campaign with $30k monthly budget',
    });

    expect(mockedPost).toHaveBeenCalledWith(
      '/api/core/projects/quick-start/preview/',
      expect.objectContaining({ step: 2 }),
      { timeout: 120_000 }
    );
  });

  it('confirm posts blueprint to confirm endpoint', async () => {
    mockedPost.mockResolvedValue({
      data: {
        project: { id: 1, name: 'Test Project', is_active: true },
        created: {
          task_ids: [1],
          spreadsheet_ids: [2],
          calendar_event_ids: ['uuid-1'],
          decision_ids: [],
          miro_board_ids: [],
        },
        summary: {
          tasks: 1,
          spreadsheets: 1,
          calendar_events: 1,
          decisions: 0,
          miro_boards: 0,
        },
      },
    });

    const result = await QuickStartAPI.confirm({ blueprint: sampleBlueprint });

    expect(result.project.id).toBe(1);
    expect(mockedPost).toHaveBeenCalledWith(
      '/api/core/projects/quick-start/confirm/',
      { blueprint: sampleBlueprint }
    );
  });
});

describe('getQuickStartErrorDetail', () => {
  it('returns backend detail for Quick Start error shape', () => {
    const error = new AxiosError(
      'Request failed',
      AxiosError.ERR_BAD_RESPONSE,
      undefined,
      undefined,
      {
        status: 502,
        statusText: 'Bad Gateway',
        headers: {},
        config: {} as InternalAxiosRequestConfig,
        data: { error: 'llm_generation_failed', detail: 'Gemini unavailable' },
      }
    );

    expect(getQuickStartErrorDetail(error, 'Failed')).toBe('Gemini unavailable');
  });
});
