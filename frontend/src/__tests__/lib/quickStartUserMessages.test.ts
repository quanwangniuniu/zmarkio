import {
  QUICK_START_PREVIEW_ERROR_FALLBACK,
  resolveQuickStartError,
} from '@/lib/quickStartUserMessages';
import { AxiosError, AxiosHeaders } from 'axios';

describe('resolveQuickStartError', () => {
  it('maps rate_limited API error to friendly message', () => {
    const error = new AxiosError('Request failed');
    error.response = {
      status: 502,
      statusText: 'Bad Gateway',
      headers: {},
      config: { headers: new AxiosHeaders() },
      data: {
        error: 'rate_limited',
        detail: 'Ollama rate limited (HTTP 429).',
        retry_after_seconds: 30,
      },
    };

    const resolved = resolveQuickStartError(error, QUICK_START_PREVIEW_ERROR_FALLBACK);
    expect(resolved.message).toContain('30 seconds');
    expect(resolved.errorCode).toBe('rate_limited');
    expect(resolved.retryAfterSeconds).toBe(30);
  });

  it('does not blame campaign for generic validation_failed', () => {
    const error = new AxiosError('Request failed');
    error.response = {
      status: 400,
      statusText: 'Bad Request',
      headers: {},
      config: { headers: new AxiosHeaders() },
      data: {
        error: 'validation_failed',
        detail: 'plan.campaign_summary is required.',
      },
    };

    const resolved = resolveQuickStartError(error, QUICK_START_PREVIEW_ERROR_FALLBACK);
    expect(resolved.message).not.toMatch(/check your campaign|description is/i);
    expect(resolved.message).toContain('Nothing is wrong with your description');
  });

  it('maps prompt length validation to a specific message', () => {
    const error = new AxiosError('Request failed');
    error.response = {
      status: 400,
      statusText: 'Bad Request',
      headers: {},
      config: { headers: new AxiosHeaders() },
      data: {
        error: 'validation_failed',
        detail: 'prompt must be at least 10 characters after trimming.',
      },
    };

    const resolved = resolveQuickStartError(error, QUICK_START_PREVIEW_ERROR_FALLBACK);
    expect(resolved.message).toContain('too short');
  });
});
