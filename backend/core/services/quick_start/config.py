"""Configuration for Quick Start (separate from Agent settings)."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from django.conf import settings


def _prompts_dir() -> Path:
    return Path(__file__).resolve().parent / 'prompts'


@dataclass(frozen=True)
class QuickStartConfig:
    """Runtime configuration for the Quick Start LLM chain."""

    prompts_dir: Path
    # Total budget for one preview (plan + blueprint stages, including retries).
    llm_timeout_seconds: int = 240
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
        from core.services.ollama_client import is_llm_configured

        return is_llm_configured()

    def require_llm_configured(self) -> None:
        from core.services.quick_start.exceptions import QuickStartConfigurationError

        if not self.is_llm_configured:
            raise QuickStartConfigurationError(
                'OLLAMA_BASE_URL is not configured. Quick Start requires Ollama.'
            )


@lru_cache(maxsize=1)
def get_quick_start_config() -> QuickStartConfig:
    """
    Load Quick Start config.

    Uses the same Ollama backend as Agent but does not import Agent workflow code.
    """
    return QuickStartConfig(
        prompts_dir=_prompts_dir(),
        llm_timeout_seconds=max(int(settings.QUICK_START_LLM_TIMEOUT_SECONDS), 30),
    )
