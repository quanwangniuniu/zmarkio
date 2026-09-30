import axios from 'axios';
import { getApiErrorDetail } from '@/lib/api/errorMessage';
import type { QuickStartApiErrorBody } from '@/types/quickStart';

/** Fallback when the API does not return a structured error body. */
export const QUICK_START_PREVIEW_ERROR_FALLBACK =
  'We could not generate a preview right now. Nothing is wrong with your description — please try again in a moment.';

export const QUICK_START_CONFIRM_ERROR_FALLBACK =
  'We could not create the project right now. Please try again.';

const MESSAGE_BY_ERROR_CODE: Record<string, string> = {
  rate_limited:
    'AI requests are coming in too quickly. Please wait 30 seconds, then try again.',
  malformed_output:
    'The AI draft came back incomplete. Please try Generate preview again — no changes to your description are needed.',
  network_error:
    'We could not reach the AI service. Check your connection and try again.',
  llm_generation_failed: QUICK_START_PREVIEW_ERROR_FALLBACK,
  configuration_error:
    'AI setup is not complete. Ask your administrator to configure the Ollama server.',
};

/** Patterns that indicate a technical message we should not show to users. */
const TECHNICAL_DETAIL_PATTERN =
  /json|traceback|http\s*\d{3}|gemini|unterminated|runtimeerror|exception|429|too many requests|plan\.|blueprint\.|tasks\[/i;

export type QuickStartResolvedError = {
  message: string;
  errorCode?: string;
  retryAfterSeconds?: number;
};

function isTechnicalDetail(detail: string): boolean {
  return TECHNICAL_DETAIL_PATTERN.test(detail);
}

function mapValidationDetailToUserMessage(detail: string): string | null {
  const lower = detail.toLowerCase();

  if (lower.includes('prompt must be at least')) {
    return 'Your campaign description is too short. Add a bit more detail and try again.';
  }
  if (lower.includes('prompt must be at most')) {
    return 'Your campaign description is too long. Shorten it and try again.';
  }
  if (lower.includes('supplement must be at most')) {
    return 'Additional context is too long. Shorten it and try again.';
  }
  if (lower.includes('combined must be at most') || lower.includes('prompt and supplement combined')) {
    return 'Campaign description and additional context together are too long. Shorten one and try again.';
  }

  return null;
}

export function resolveQuickStartError(
  error: unknown,
  fallback: string
): QuickStartResolvedError {
  if (axios.isAxiosError(error)) {
    const data = error.response?.data;
    if (data && typeof data === 'object') {
      const body = data as QuickStartApiErrorBody & {
        retry_after_seconds?: number;
      };
      const code = typeof body.error === 'string' ? body.error : undefined;
      const retryAfterSeconds =
        typeof body.retry_after_seconds === 'number' && body.retry_after_seconds > 0
          ? body.retry_after_seconds
          : code === 'rate_limited'
            ? 30
            : undefined;

      if (code === 'validation_failed') {
        const detail = typeof body.detail === 'string' ? body.detail.trim() : '';
        const mapped = detail ? mapValidationDetailToUserMessage(detail) : null;
        if (mapped) {
          return { message: mapped, errorCode: code };
        }
        return { message: fallback, errorCode: 'llm_generation_failed' };
      }

      if (code && MESSAGE_BY_ERROR_CODE[code]) {
        return {
          message: MESSAGE_BY_ERROR_CODE[code],
          errorCode: code,
          retryAfterSeconds,
        };
      }

      if (typeof body.detail === 'string' && body.detail.trim()) {
        const detail = body.detail.trim();
        const inputMapped = mapValidationDetailToUserMessage(detail);
        if (inputMapped) {
          return { message: inputMapped, errorCode: 'validation_failed' };
        }
        if (!isTechnicalDetail(detail)) {
          return {
            message: detail,
            errorCode: code,
            retryAfterSeconds,
          };
        }
        if (code === 'rate_limited' || /429|too many/i.test(detail)) {
          return {
            message: MESSAGE_BY_ERROR_CODE.rate_limited,
            errorCode: 'rate_limited',
            retryAfterSeconds: retryAfterSeconds ?? 30,
          };
        }
        if (code === 'llm_generation_failed' || /malformed|not valid json/i.test(detail)) {
          return {
            message: MESSAGE_BY_ERROR_CODE.malformed_output,
            errorCode: 'malformed_output',
          };
        }
      }
    }
  }

  const generic = getApiErrorDetail(error, fallback);
  const inputMapped = mapValidationDetailToUserMessage(generic);
  if (inputMapped) {
    return { message: inputMapped, errorCode: 'validation_failed' };
  }
  if (!isTechnicalDetail(generic)) {
    return { message: generic };
  }
  return { message: fallback };
}
