"""
Quick Start module skeleton tests (qs-1.1).

Ensures the independent package imports and config/prompt paths resolve.
"""

import pytest
from unittest.mock import MagicMock

from core.services.quick_start import (
    QuickStartConfirmService,
    QuickStartLLMChain,
    QuickStartPreviewService,
    QuickStartValidationError,
    get_quick_start_config,
)
from core.services.quick_start.blueprint import empty_blueprint, normalize_selected_modules
from core.services.quick_start.constants import BLUEPRINT_VERSION, MVP_MODULES


class TestQuickStartImports:
    def test_public_exports_importable(self):
        assert QuickStartLLMChain is not None
        assert QuickStartPreviewService is not None
        assert QuickStartConfirmService is not None

    def test_config_loads(self):
        config = get_quick_start_config()
        assert config.prompts_dir.is_dir()

    def test_system_prompt_file_exists(self):
        config = get_quick_start_config()
        assert config.system_prompt_path.is_file()
        assert config.system_prompt_path.read_text(encoding='utf-8').strip()


class TestQuickStartBlueprintStub:
    def test_empty_blueprint_shape(self):
        blueprint = empty_blueprint()
        assert blueprint['version'] == BLUEPRINT_VERSION
        assert blueprint['project']['name'] == ''
        assert blueprint['tasks'] == []

    def test_normalize_selected_modules_defaults(self):
        modules = normalize_selected_modules({'tasks': False})
        assert modules['tasks'] is False
        assert modules['spreadsheet'] is True
        assert modules['decisions'] is True
        assert modules['miro'] is True
        assert set(MVP_MODULES).issubset(modules.keys())


class TestQuickStartConfirmService:
    def test_confirm_rejects_empty_blueprint(self):
        service = QuickStartConfirmService()
        mock_user = MagicMock()
        with pytest.raises(QuickStartValidationError):
            service.confirm(user=mock_user, blueprint=empty_blueprint())
