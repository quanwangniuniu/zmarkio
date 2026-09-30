import axios from 'axios';
import api from '../api';
import { getApiErrorDetail } from './errorMessage';
import type {
  QuickStartApiErrorBody,
  QuickStartConfirmRequest,
  QuickStartConfirmResponse,
  QuickStartPreviewRequest,
  QuickStartPreviewResponse,
} from '@/types/quickStart';

const PREVIEW_PATH = '/api/core/projects/quick-start/preview/';
const CONFIRM_PATH = '/api/core/projects/quick-start/confirm/';

/**
 * Kept above the backend QUICK_START_LLM_TIMEOUT_SECONDS budget (240s, both
 * Ollama stages) so the server's error response arrives before axios gives up.
 */
const PREVIEW_LLM_TIMEOUT_MS = 270_000;

export function getQuickStartErrorDetail(error: unknown, fallback: string): string {
  if (axios.isAxiosError(error)) {
    const data = error.response?.data;
    if (data && typeof data === 'object' && 'detail' in data) {
      const detail = (data as QuickStartApiErrorBody).detail;
      if (typeof detail === 'string' && detail.trim()) {
        return detail;
      }
    }
  }
  return getApiErrorDetail(error, fallback);
}

export const QuickStartAPI = {
  /**
   * Step 1 validates the prompt only; step 2 runs the LLM and returns outline + blueprint.
   */
  preview: async (
    payload: QuickStartPreviewRequest
  ): Promise<QuickStartPreviewResponse> => {
    const config =
      payload.step === 2 ? { timeout: PREVIEW_LLM_TIMEOUT_MS } : undefined;
    const response = await api.post<QuickStartPreviewResponse>(
      PREVIEW_PATH,
      payload,
      config
    );
    return response.data;
  },

  /** Persist project and enabled modules from a confirmed blueprint. */
  confirm: async (
    payload: QuickStartConfirmRequest
  ): Promise<QuickStartConfirmResponse> => {
    const response = await api.post<QuickStartConfirmResponse>(
      CONFIRM_PATH,
      payload
    );
    return response.data;
  },
};
