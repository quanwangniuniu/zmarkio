"""
MED-453 acceptance tests for the module / surface registry contract.

No database: the registry is pure in-code contract data.
"""
import pytest
from django.core.exceptions import ImproperlyConfigured

from org_customization.registry import (
    MODULE_SPECS,
    SURFACE_SPECS,
    get_module_registry,
    get_surface_registry,
    validate_registry,
)
from org_customization.registry.base import (
    SURFACE_TYPES,
    ModuleSpec,
    SurfaceSpec,
    build_module_registry,
    build_surface_registry,
    module_key_for_surface,
)

# The frozen complete module key list (MED-453). A2's MODULE_KEYS must match
# this character for character.
EXPECTED_MODULE_KEYS = (
    "overview",
    "tasks",
    "campaigns",
    "meta_ads",
    "decisions",
    "budget_pools",
    "spreadsheets",
    "variations_studio",
    "ads_draft",
    "email_draft",
    "notion",
    "meetings",
    "calendar",
    "messages",
    "miro",
    "workflows",
    "timeline",
    "csm",
    "admin",
    "integrations",
    "agent",
    "notifications",
    "shell",
)

# The surface key examples frozen in the naming spec — all must be registrable.
FROZEN_EXAMPLE_SURFACE_KEYS = (
    "nav.group.manage",
    "nav.item.tasks",
    "nav.item.ads_draft.facebook_meta",
    "tasks.tab.gantt",
    "overview.card.meetings",
    "integrations.provider.slack",
    "shell.usermenu.subscription",
)


class TestModuleRegistry:
    def test_returns_the_23_frozen_module_keys(self):
        registry = get_module_registry()
        assert tuple(registry.keys()) == EXPECTED_MODULE_KEYS

    def test_every_module_declares_route_prefixes(self):
        for spec in get_module_registry().values():
            assert isinstance(spec.route_prefixes, tuple), spec.key
            assert len(spec.route_prefixes) > 0, spec.key
            for prefix in spec.route_prefixes:
                assert isinstance(prefix, str) and prefix, spec.key

    def test_module_specs_are_unique_and_stable(self):
        keys = [spec.key for spec in MODULE_SPECS]
        assert len(keys) == len(set(keys))
        assert get_module_registry() == get_module_registry()

    def test_duplicate_module_key_raises(self):
        specs = [
            ModuleSpec("tasks", "Tasks"),
            ModuleSpec("tasks", "Tasks again"),
        ]
        with pytest.raises(ImproperlyConfigured, match="Duplicate module key"):
            build_module_registry(specs)

    @pytest.mark.parametrize(
        "bad_key",
        ["Tasks", "task-s", "nav.item", "meta.ads", "", "1tasks", "tasks."],
    )
    def test_malformed_module_key_raises(self, bad_key):
        with pytest.raises(ImproperlyConfigured, match="Malformed module key"):
            build_module_registry([ModuleSpec(bad_key, "Label")])


class TestSurfaceRegistry:
    def test_frozen_example_keys_are_registered(self):
        registry = get_surface_registry()
        for key in FROZEN_EXAMPLE_SURFACE_KEYS:
            assert key in registry

    def test_surface_keys_match_format(self):
        for key in get_surface_registry():
            module_segment, type_segment, _ = key.split(".", 2)
            assert module_segment in EXPECTED_MODULE_KEYS or module_segment == "nav"
            assert type_segment in SURFACE_TYPES

    def test_duplicate_surface_key_raises(self):
        specs = [
            SurfaceSpec("tasks.tab.gantt", "Gantt"),
            SurfaceSpec("tasks.tab.gantt", "Gantt again"),
        ]
        with pytest.raises(ImproperlyConfigured, match="Duplicate surface key"):
            build_surface_registry(specs, EXPECTED_MODULE_KEYS)

    @pytest.mark.parametrize(
        "bad_key",
        [
            "tasks.tab",                      # name segment missing
            "tasks.tabs.gantt",               # type not in the frozen enum
            "tasks.TAB.gantt",                # uppercase
            "tasks..gantt",                   # empty segment
            "unknown_module.tab.gantt",       # module segment not registered
            "task.tab.gantt.",                # trailing dot
            "tasks.tab.Gantt",                # uppercase name
        ],
    )
    def test_malformed_surface_key_raises(self, bad_key):
        with pytest.raises(ImproperlyConfigured, match="Malformed surface key"):
            build_surface_registry([SurfaceSpec(bad_key, "Label")], EXPECTED_MODULE_KEYS)

    def test_nav_namespace_is_reserved_for_navigation_surfaces(self):
        registry = build_surface_registry(
            [SurfaceSpec("nav.group.manage", "Manage")], EXPECTED_MODULE_KEYS
        )
        assert "nav.group.manage" in registry

    def test_dotted_name_segments_are_allowed(self):
        registry = build_surface_registry(
            [SurfaceSpec("nav.item.ads_draft.facebook_meta", "Meta")],
            EXPECTED_MODULE_KEYS,
        )
        assert "nav.item.ads_draft.facebook_meta" in registry


class TestKeyNamingSpec:
    def test_surface_type_enum_is_frozen(self):
        assert SURFACE_TYPES == (
            "group",
            "item",
            "tab",
            "card",
            "panel",
            "provider",
            "filter",
            "section",
            "usermenu",
        )

    def test_module_key_for_surface_uses_key_prefix(self):
        assert module_key_for_surface("tasks.tab.gantt") == "tasks"
        assert module_key_for_surface("overview.card.meetings") == "overview"

    def test_module_key_for_surface_maps_nav_to_shell(self):
        assert module_key_for_surface("nav.group.manage") == "shell"
        assert module_key_for_surface("nav.item.ads_draft.facebook_meta") == "shell"

    def test_validate_registry_passes(self):
        validate_registry()
