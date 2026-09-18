"""Independent Gemini chain for Quick Start (no Agent workflow orchestration)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any, Mapping

from core.services.gemini_client import call_gemini_json

from core.services.quick_start.blueprint import normalize_selected_modules
from core.services.quick_start.brief import build_campaign_brief
from core.services.quick_start.config import QuickStartConfig, get_quick_start_config
from core.services.quick_start.exceptions import QuickStartLLMError, QuickStartValidationError
from core.services.quick_start.user_messages import (
    LLM_ERROR_MALFORMED_OUTPUT,
    USER_MESSAGE_MALFORMED_OUTPUT,
)
from core.services.quick_start.module_defaults import ensure_quick_start_module_minimums
from core.services.quick_start.plan_validation import validate_campaign_plan
from core.services.quick_start.prompt_builder import (
    build_blueprint_user_prompt,
    build_plan_user_prompt,
)
from core.services.quick_start.json_coercion import coerce_gemini_json_object
from core.services.quick_start.user_messages import user_message_for_runtime_error
from core.services.quick_start.validation import validate_and_normalize_blueprint, validate_prompt_text

logger = logging.getLogger(__name__)

GeminiJsonCaller = Callable[..., dict[str, Any]]


class QuickStartLLMChain:
    """
    Generates a ProjectBlueprint via two Gemini calls:
    1) CampaignPlan from user input
    2) ProjectBlueprint expanded from the plan
    """

    def __init__(
        self,
        config: QuickStartConfig | None = None,
        *,
        call_json: GeminiJsonCaller | None = None,
    ) -> None:
        self.config = config or get_quick_start_config()
        self._uses_live_gemini = call_json is None
        self._call_json = call_json or call_gemini_json

    def _read_prompt_file(self, filename: str) -> str:
        path = self.config.prompts_dir / filename
        if not path.is_file():
            raise QuickStartLLMError(f'Prompt file missing: {path}')
        return path.read_text(encoding='utf-8').strip()

    def _call_stage(
        self,
        *,
        stage: str,
        system_filename: str,
        user_prompt: str,
        temperature: float,
        expect_plan: bool = False,
        expect_blueprint: bool = False,
    ) -> dict[str, Any]:
        system_prompt = self._read_prompt_file(system_filename)
        retry_suffix = (
            '\n\nIMPORTANT: Respond with exactly one JSON object (root must be {{...}}), '
            'not an array and no markdown fences.'
        )
        last_error: QuickStartLLMError | None = None

        for attempt in range(2):
            prompt = user_prompt if attempt == 0 else user_prompt + retry_suffix
            try:
                raw = self._call_json(
                    system_prompt=system_prompt,
                    user_prompt=prompt,
                    temperature=temperature,
                    timeout=self.config.llm_timeout_seconds,
                )
            except RuntimeError as exc:
                logger.warning('Quick Start Gemini call failed (%s): %s', stage, exc)
                error_code, user_message, retry_after = user_message_for_runtime_error(exc)
                if attempt == 0 and error_code in ('rate_limited', 'network_error'):
                    time.sleep(2.0 if error_code == 'rate_limited' else 1.5)
                    continue
                raise QuickStartLLMError(
                    str(exc),
                    error_code=error_code,
                    user_message=user_message,
                    retry_after_seconds=retry_after,
                ) from exc

            try:
                return coerce_gemini_json_object(
                    raw,
                    stage=stage,
                    expect_plan=expect_plan,
                    expect_blueprint=expect_blueprint,
                )
            except QuickStartLLMError as exc:
                last_error = exc
                logger.warning(
                    'Quick Start JSON coercion failed (%s) attempt=%s: %s',
                    stage,
                    attempt + 1,
                    exc,
                )

        assert last_error is not None
        if isinstance(last_error, QuickStartLLMError) and last_error.user_message:
            raise last_error
        error_code, user_message, retry_after = user_message_for_runtime_error(last_error)
        raise QuickStartLLMError(
            str(last_error),
            error_code=error_code,
            user_message=user_message,
            retry_after_seconds=retry_after,
        ) from last_error

    def generate_campaign_plan(
        self,
        *,
        brief: Mapping[str, Any],
        selected_modules: dict[str, bool],
    ) -> dict[str, Any]:
        user_prompt = build_plan_user_prompt(brief=brief, selected_modules=selected_modules)
        raw = self._call_stage(
            stage='campaign_plan',
            system_filename=self.config.plan_system_prompt_filename,
            user_prompt=user_prompt,
            temperature=self.config.plan_temperature,
            expect_plan=True,
        )
        try:
            return validate_campaign_plan(raw, selected_modules)
        except QuickStartValidationError as exc:
            logger.warning('Quick Start plan validation failed: %s', exc)
            raise QuickStartLLMError(
                str(exc),
                error_code=LLM_ERROR_MALFORMED_OUTPUT,
                user_message=USER_MESSAGE_MALFORMED_OUTPUT,
            ) from exc
        except Exception as exc:
            logger.warning('Quick Start plan validation failed: %s', exc)
            raise

    def generate_blueprint_from_plan(
        self,
        *,
        brief: Mapping[str, Any],
        plan: Mapping[str, Any],
        selected_modules: dict[str, bool],
    ) -> dict[str, Any]:
        user_prompt = build_blueprint_user_prompt(
            brief=brief,
            plan=plan,
            selected_modules=selected_modules,
        )
        raw = self._call_stage(
            stage='project_blueprint',
            system_filename=self.config.blueprint_system_prompt_filename,
            user_prompt=user_prompt,
            temperature=self.config.blueprint_temperature,
            expect_blueprint=True,
        )
        try:
            blueprint = validate_and_normalize_blueprint(raw, selected_modules)
            blueprint = ensure_quick_start_module_minimums(blueprint, selected_modules)
            return validate_and_normalize_blueprint(blueprint, selected_modules)
        except QuickStartValidationError as exc:
            logger.warning('Quick Start blueprint validation failed: %s', exc)
            raise QuickStartLLMError(
                str(exc),
                error_code=LLM_ERROR_MALFORMED_OUTPUT,
                user_message=USER_MESSAGE_MALFORMED_OUTPUT,
            ) from exc
        except Exception as exc:
            logger.warning('Quick Start blueprint validation failed: %s', exc)
            raise

    def generate_blueprint(
        self,
        *,
        prompt: str,
        supplement: str | None = None,
        chips: Mapping[str, Any] | None = None,
        selected_modules: Mapping[str, bool] | None = None,
        locale: str | None = None,
    ) -> dict[str, Any]:
        """Call Gemini (plan then blueprint) and return a validated blueprint dict."""
        if self._uses_live_gemini:
            self.config.require_llm_configured()
        cleaned_prompt = validate_prompt_text(prompt)
        modules = normalize_selected_modules(selected_modules)

        brief = build_campaign_brief(
            prompt=cleaned_prompt,
            supplement=supplement,
            chips=chips,
            locale=locale,
        )

        plan = self.generate_campaign_plan(brief=brief, selected_modules=modules)
        logger.info(
            'Quick Start stage-1 plan ready: project=%s tasks=%s',
            plan.get('project', {}).get('name'),
            len(plan.get('tasks_plan') or []),
        )

        blueprint = self.generate_blueprint_from_plan(
            brief=brief,
            plan=plan,
            selected_modules=modules,
        )
        logger.info(
            'Quick Start stage-2 blueprint ready: tasks=%s events=%s',
            len(blueprint.get('tasks') or []),
            len(blueprint.get('calendar_events') or []),
        )
        return blueprint
