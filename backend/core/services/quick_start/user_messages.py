"""User-facing Quick Start / Ollama error messages (no technical jargon)."""

from __future__ import annotations

# API `error` field values returned to the frontend.
LLM_ERROR_RATE_LIMITED = 'rate_limited'
LLM_ERROR_MALFORMED_OUTPUT = 'malformed_output'
LLM_ERROR_NETWORK = 'network_error'
LLM_ERROR_GENERIC = 'llm_generation_failed'
LLM_ERROR_CONFIGURATION = 'configuration_error'

RATE_LIMIT_RETRY_AFTER_SECONDS = 30

USER_MESSAGE_RATE_LIMITED = (
    'AI requests are coming in too quickly. Please wait 30 seconds, then try again.'
)
USER_MESSAGE_MALFORMED_OUTPUT = (
    'The AI draft came back incomplete. Please try Generate preview again — no changes to your description are needed.'
)
USER_MESSAGE_NETWORK = (
    'We could not reach the AI service. Check your connection and try again.'
)
USER_MESSAGE_GENERIC = (
    'We could not generate a preview right now. Nothing is wrong with your description — please try again in a moment.'
)
USER_MESSAGE_CONFIGURATION = (
    'AI setup is not complete. Ask your administrator to configure the AI model server.'
)


def llm_error_response(
    *,
    error_code: str,
    user_message: str,
    retry_after_seconds: int | None = None,
) -> dict[str, str | int]:
    payload: dict[str, str | int] = {
        'error': error_code,
        'detail': user_message,
    }
    if retry_after_seconds is not None:
        payload['retry_after_seconds'] = retry_after_seconds
    return payload


def user_message_for_validation_error(detail: str) -> tuple[str, str]:
    """
    Return (error_code, user_message) for QuickStartValidationError.

    Only blame the user's input when the failure is truly about prompt/supplement limits.
    """
    text = detail.strip()
    lower = text.lower()

    if 'prompt must be at least' in lower:
        return (
            'validation_failed',
            'Your campaign description is too short. Add a bit more detail and try again.',
        )
    if 'prompt must be at most' in lower:
        return (
            'validation_failed',
            'Your campaign description is too long. Shorten it and try again.',
        )
    if 'supplement must be at most' in lower:
        return (
            'validation_failed',
            'Additional context is too long. Shorten it and try again.',
        )
    if 'combined must be at most' in lower or 'prompt and supplement combined' in lower:
        return (
            'validation_failed',
            'Campaign description and additional context together are too long. Shorten one and try again.',
        )
    if lower == 'step must be 1 or 2.':
        return ('validation_failed', 'Something went wrong with this step. Go back and try again.')
    if 'not a valid choice' in lower:
        return ('validation_failed', 'Something went wrong with this step. Go back and try again.')

    # Plan/blueprint/schema validation — not the user's fault.
    return LLM_ERROR_MALFORMED_OUTPUT, USER_MESSAGE_MALFORMED_OUTPUT


def user_message_for_runtime_error(exc: BaseException) -> tuple[str, str, int | None]:
    """
    Map an Ollama RuntimeError (or similar) to (error_code, user_message, retry_after_seconds).
    """
    text = str(exc).lower()

    if '429' in text or 'rate-limited' in text or 'rate limited' in text or 'too many requests' in text:
        return (
            LLM_ERROR_RATE_LIMITED,
            USER_MESSAGE_RATE_LIMITED,
            RATE_LIMIT_RETRY_AFTER_SECONDS,
        )

    if 'malformed' in text or 'not valid json' in text or 'non-json' in text:
        return LLM_ERROR_MALFORMED_OUTPUT, USER_MESSAGE_MALFORMED_OUTPUT, None

    if 'network' in text:
        return LLM_ERROR_NETWORK, USER_MESSAGE_NETWORK, None

    if 'ollama_base_url' in text or 'not configured' in text:
        return LLM_ERROR_CONFIGURATION, USER_MESSAGE_CONFIGURATION, None

    return LLM_ERROR_GENERIC, USER_MESSAGE_GENERIC, None
