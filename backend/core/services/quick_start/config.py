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

    ollama_base_url: str
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
        return bool(self.ollama_base_url and self.ollama_base_url.strip())

    def require_llm_configured(self) -> None:
        from core.services.quick_start.exceptions import QuickStartConfigurationError

        if not self.is_llm_configured:
            raise QuickStartConfigurationError(
                'OLLAMA_BASE_URL is not configured. Quick Start requires the Ollama LLM server.'
            )


@lru_cache(maxsize=1)
def get_quick_start_config() -> QuickStartConfig:
    """
    Load Quick Start config from Django settings / environment.

    Uses the same OLLAMA_BASE_URL as Agent but does not import Agent workflow code.
    """
    base_url = getattr(settings, 'OLLAMA_BASE_URL', None) or ''
    timeout_raw = (
        getattr(settings, 'QUICK_START_LLM_TIMEOUT_SECONDS', None)
        or os.environ.get('QUICK_START_LLM_TIMEOUT_SECONDS', '120')
    )
    try:
        timeout = int(timeout_raw)
    except (TypeError, ValueError):
        timeout = 120

    return QuickStartConfig(
        ollama_base_url=base_url.strip() if isinstance(base_url, str) else '',
        llm_timeout_seconds=max(timeout, 30),
        prompts_dir=_prompts_dir(),
    )
