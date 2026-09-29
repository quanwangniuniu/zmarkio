"""Environment-backed configuration for Quick Start (separate from Agent settings)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from django.conf import settings


def _prompts_dir() -> Path:
    return Path(__file__).resolve().parent / 'prompts'


@dataclass(frozen=True)
class QuickStartConfig:
    """Runtime configuration for the Quick Start LLM chain."""

    gemini_api_key: str
    llm_timeout_seconds: int
    prompts_dir: Path
    plan_system_prompt_filename: str = 'system_plan_v1.txt'
    blueprint_system_prompt_filename: str = 'system_blueprint_v1.txt'
    plan_temperature: float = 0.2
    blueprint_temperature: float = 0.3

    @property
    def system_prompt_path(self) -> Path:
        """Backward-compatible alias for the blueprint-stage system prompt."""
        return self.prompts_dir / self.blueprint_system_prompt_filename

    @property
    def is_llm_configured(self) -> bool:
        from core.services.ollama_client import is_ollama_backend

        if is_ollama_backend():
            return True
        return bool(self.gemini_api_key and self.gemini_api_key.strip())

    def require_llm_configured(self) -> None:
        from core.services.quick_start.exceptions import QuickStartConfigurationError

        if not self.is_llm_configured:
            raise QuickStartConfigurationError(
                'GEMINI_API_KEY is not configured. Quick Start requires Gemini.'
            )


@lru_cache(maxsize=1)
def get_quick_start_config() -> QuickStartConfig:
    """
    Load Quick Start config from Django settings / environment.

    Uses the same GEMINI_API_KEY as Agent but does not import Agent workflow code.
    """
    key = (
        getattr(settings, 'GEMINI_API_KEY', None)
        or os.environ.get('GEMINI_API_KEY', '')
        or ''
    )
    timeout_raw = (
        getattr(settings, 'QUICK_START_LLM_TIMEOUT_SECONDS', None)
        or os.environ.get('QUICK_START_LLM_TIMEOUT_SECONDS', '120')
    )
    try:
        timeout = int(timeout_raw)
    except (TypeError, ValueError):
        timeout = 120

    return QuickStartConfig(
        gemini_api_key=key.strip() if isinstance(key, str) else '',
        llm_timeout_seconds=max(timeout, 30),
        prompts_dir=_prompts_dir(),
    )
