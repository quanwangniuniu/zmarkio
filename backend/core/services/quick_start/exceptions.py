"""Quick Start domain exceptions."""


class QuickStartError(Exception):
    """Base error for Quick Start flows."""


class QuickStartConfigurationError(QuickStartError):
    """Missing or invalid environment configuration (e.g. OLLAMA_BASE_URL)."""


class QuickStartValidationError(QuickStartError):
    """Request or blueprint schema validation failed."""


class QuickStartLLMError(QuickStartError):
    """LLM call failed or returned unusable output."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str = 'llm_generation_failed',
        user_message: str | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.user_message = user_message or message
        self.retry_after_seconds = retry_after_seconds
