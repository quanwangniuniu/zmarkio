"""Registration, boot validation and diagnostics for the column registry."""
import importlib
import sys
from copy import deepcopy
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import SystemCheckError
from rest_framework.test import APIRequestFactory, force_authenticate

from agent import column_registry as registry
from agent.apps import check_column_registry
from agent.views import AgentConfigStatusView


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch):
    monkeypatch.delenv('AGENT_COLUMN_REGISTRY_TEST_MODE', raising=False)
    monkeypatch.setattr(registry, 'SCHEMA_REGISTRY', {
        'test': {
            'name': 'Test',
            'columns': {
                'revenue': {'aliases': ['Total Sales'], 'category': 'financial'},
            },
        },
    })
    monkeypatch.setattr(registry, '_SCHEMA_INDEXES', {})
    registry.validate_registry()


@pytest.mark.parametrize('name,aliases', [
    ('revenue', ['Replacement']),
    (' REVENUE ', []),
    ('other', ['total_sales']),
    ('total_sales', []),
    ('other', ['REVENUE']),
])
def test_collision_is_rejected_before_definitions_or_indexes_change(name, aliases):
    before = deepcopy(registry.SCHEMA_REGISTRY)
    indexes = deepcopy(registry._SCHEMA_INDEXES)
    with pytest.raises(registry.ColumnRegistryCollisionError, match='test'):
        registry.register_column('test', name, {'aliases': aliases})
    assert registry.SCHEMA_REGISTRY == before
    assert registry._SCHEMA_INDEXES == indexes
    # A rejected registration does not poison the valid registry indefinitely.
    registry.validate_registry()
    assert registry._try_rule_match(['Total Sales']).mappings == {'Total Sales': 'revenue'}


def test_duplicate_schema_does_not_replace_original():
    original = registry.SCHEMA_REGISTRY['test']
    with pytest.raises(registry.ColumnRegistryCollisionError, match="schema 'test'"):
        registry.register_schema('test', {'name': 'Replacement', 'columns': {}})
    assert registry.SCHEMA_REGISTRY['test'] is original


def test_new_schema_is_validated_before_registration():
    with pytest.raises(registry.ColumnRegistryCollisionError, match='broken'):
        registry.register_schema('broken', {
            'name': 'Broken',
            'columns': {'revenue': {'aliases': ['Sales']}, 'sales': {}},
        })
    assert 'broken' not in registry.SCHEMA_REGISTRY
    assert 'broken' not in registry._SCHEMA_INDEXES


def test_plain_dict_format_and_existing_references_are_preserved():
    schemas = registry.SCHEMA_REGISTRY
    columns = schemas['test']['columns']
    registry.register_column('test', 'clicks', {'aliases': ['Click Count']})
    assert type(schemas) is dict
    assert type(columns) is dict
    assert type(columns['clicks']['aliases']) is list
    assert schemas is registry.SCHEMA_REGISTRY
    assert columns is registry.SCHEMA_REGISTRY['test']['columns']
    result = registry._try_rule_match(['Click Count'])
    assert result.mappings == {'Click Count': 'clicks'}
    assert result.categories == {'clicks': registry.CAT_UNKNOWN}


def test_repeated_aliases_for_same_column_and_names_in_other_schemas_are_allowed():
    registry.register_schema('other', {
        'name': 'Other',
        'columns': {'revenue': {'aliases': ['REVENUE', 'revenue', 'Total Sales']}},
    })
    registry.validate_registry()
    assert len(registry.SCHEMA_REGISTRY) == 2


def test_plugin_reload_raises_at_registration(monkeypatch, tmp_path):
    plugin_name = 'med244_test_plugin'
    (tmp_path / f'{plugin_name}.py').write_text(
        "from agent.column_registry import register_column\n"
        "register_column('test', 'clicks', {'aliases': ['Click Count']})\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        plugin = importlib.import_module(plugin_name)
        with pytest.raises(registry.ColumnRegistryCollisionError, match='clicks'):
            importlib.reload(plugin)
        assert registry._try_rule_match(['Click Count']).mappings['Click Count'] == 'clicks'
    finally:
        sys.modules.pop(plugin_name, None)


@pytest.mark.parametrize('flag', ['', '0', 'false', 'no'])
def test_only_explicit_test_mode_allows_overwrite(monkeypatch, flag):
    monkeypatch.setenv('AGENT_COLUMN_REGISTRY_TEST_MODE', flag)
    with pytest.raises(registry.ColumnRegistryCollisionError):
        registry.register_column('test', 'revenue', {})


@pytest.mark.parametrize('flag', ['1', 'true', 'yes', 'on'])
def test_test_mode_overwrites_and_removes_obsolete_aliases(monkeypatch, flag):
    monkeypatch.setenv('AGENT_COLUMN_REGISTRY_TEST_MODE', flag)
    registry.register_column('test', 'revenue', {'aliases': ['New Sales']})
    assert registry._try_rule_match(['New Sales']).mappings['New Sales'] == 'revenue'
    assert registry._try_rule_match(['Total Sales']) is None
    registry.register_schema('test', {'name': 'Replacement', 'columns': {}})
    assert registry.SCHEMA_REGISTRY['test']['name'] == 'Replacement'
    assert registry._try_rule_match(['New Sales']) is None


def test_test_mode_alias_overwrite_is_checked_again_when_flag_is_removed(monkeypatch):
    monkeypatch.setenv('AGENT_COLUMN_REGISTRY_TEST_MODE', '1')
    registry.register_column('test', 'sales', {'aliases': ['Total Sales']})
    assert registry._try_rule_match(['Total Sales']).mappings['Total Sales'] == 'sales'
    assert check_column_registry(None) == []
    monkeypatch.delenv('AGENT_COLUMN_REGISTRY_TEST_MODE')
    with pytest.raises(registry.ColumnRegistryCollisionError):
        registry.detect_columns(['Total Sales'])
    assert check_column_registry(None)[0].is_serious()


def test_system_check_revalidates_current_definitions_and_recovers():
    columns = registry.SCHEMA_REGISTRY['test']['columns']
    columns['other'] = {'aliases': ['Total Sales']}
    errors = check_column_registry(None)
    assert len(errors) == 1
    assert all(name in errors[0].msg for name in ['test', 'revenue', 'other'])
    with pytest.raises(SystemCheckError, match='Column registry name collision'):
        call_command('check')
    with pytest.raises(registry.ColumnRegistryCollisionError):
        registry.detect_columns(['Total Sales'])
    del columns['other']
    assert check_column_registry(None) == []


def test_boot_collision_leaves_diagnostics_importable(caplog):
    source = Path(registry.__file__).read_text().replace(
        '"campaign name", "campaign", "campaign_name"',
        '"campaign name", "amount_spent", "campaign_name"',
        1,
    )
    module = ModuleType('med244_boot_registry')
    exec(compile(source, registry.__file__, 'exec'), module.__dict__)
    assert 'Column registry validation failed' in caplog.text
    assert module._SCHEMA_INDEXES == {}
    with pytest.raises(module.ColumnRegistryCollisionError, match='amount spent'):
        module.validate_registry()


def config_status():
    request = APIRequestFactory().get('/api/agent/config/status/')
    force_authenticate(request, user=SimpleNamespace(is_authenticated=True, is_staff=False))
    return AgentConfigStatusView.as_view()(request)


def test_authenticated_user_gets_current_collision_and_recovery():
    columns = registry.SCHEMA_REGISTRY['test']['columns']
    columns['other'] = {'aliases': ['Total Sales']}
    response = config_status()
    assert response.status_code == 200
    assert response.data['column_registry']['ok'] is False
    assert 'other' in response.data['column_registry']['error']
    del columns['other']
    assert config_status().data['column_registry'] == {'ok': True}


def test_diagnostics_does_not_query_database_templates():
    with patch('agent.models.DataSchemaTemplate.objects') as manager:
        response = config_status()
    assert response.data['column_registry'] == {'ok': True}
    manager.filter.assert_not_called()


def test_registry_diagnostics_preserve_existing_config_flags():
    registry.SCHEMA_REGISTRY['test']['columns']['other'] = {'aliases': ['Total Sales']}
    response = config_status()
    assert response.status_code == 200
    assert response.data['column_registry']['ok'] is False
    assert 'ollama' in response.data
    assert 'gemini' not in response.data
    assert 'anthropic' not in response.data
