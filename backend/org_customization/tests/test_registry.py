import pytest
from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse

from org_customization.registry import (
    build_name_registry,
    build_surface_registry,
    get_module_registry,
    get_surface_registry,
    get_surface_type_registry,
)
from org_customization.registry.base import ModuleSpec, SurfaceSpec, SurfaceTypeSpec

MODULE_KEYS = (
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

SURFACE_TYPE_KEYS = ("group", "item", "tab", "card", "panel", "provider", "filter", "section")

MODULES = build_name_registry((ModuleSpec("tasks", "Tasks"), ModuleSpec("shell", "Shell")), "module")
TYPES = build_name_registry(
    (SurfaceTypeSpec("tab", "Tab"), SurfaceTypeSpec("item", "Item")), "surface type"
)


def build_surfaces(*surfaces):
    return build_surface_registry(surfaces, MODULES, TYPES)


def test_shipped_surface_registry_is_valid():
    get_surface_registry.cache_clear()
    assert isinstance(get_surface_registry(), dict)


def test_module_registry_has_the_23_modules_in_order():
    assert tuple(get_module_registry()) == MODULE_KEYS


def test_module_route_prefixes_are_api_paths():
    for spec in get_module_registry().values():
        assert isinstance(spec.route_prefixes, tuple)
        for prefix in spec.route_prefixes:
            assert prefix.startswith("/api/") and prefix.endswith("/"), (spec.key, prefix)


def test_surface_type_registry_has_the_8_types():
    assert tuple(get_surface_type_registry()) == SURFACE_TYPE_KEYS


def test_duplicate_module_key_raises():
    with pytest.raises(ImproperlyConfigured, match="Duplicate module key 'tasks'"):
        build_name_registry((ModuleSpec("tasks", "A"), ModuleSpec("tasks", "B")), "module")


@pytest.mark.parametrize("key", ["Tasks", "meta-ads", "1tasks", "", "tasks.tab"])
def test_malformed_module_key_raises(key):
    with pytest.raises(ImproperlyConfigured, match="Malformed module key"):
        build_name_registry((ModuleSpec(key, "Bad"),), "module")


def test_valid_surfaces_with_parent_are_registered():
    registry = build_surfaces(
        SurfaceSpec("shell.item.ads_draft", "Ads Draft"),
        SurfaceSpec("shell.item.facebook_meta", "Facebook Meta", parent="shell.item.ads_draft"),
        SurfaceSpec("tasks.tab.gantt", "Gantt"),
    )
    assert registry["shell.item.facebook_meta"].parent == "shell.item.ads_draft"
    assert set(registry) == {"shell.item.ads_draft", "shell.item.facebook_meta", "tasks.tab.gantt"}


@pytest.mark.parametrize(
    "key",
    ["tasks.tab", "tasks.tab.gantt.extra", "tasks.tab.Gantt", "tasks..gantt", "tasks.tab.my-actions"],
)
def test_malformed_surface_key_raises(key):
    with pytest.raises(ImproperlyConfigured, match="Malformed surface key"):
        build_surfaces(SurfaceSpec(key, "Bad"))


def test_duplicate_surface_key_raises():
    with pytest.raises(ImproperlyConfigured, match="Duplicate surface key"):
        build_surfaces(SurfaceSpec("tasks.tab.gantt", "A"), SurfaceSpec("tasks.tab.gantt", "B"))


def test_surface_with_unknown_module_raises():
    with pytest.raises(ImproperlyConfigured, match="unknown module 'nav'"):
        build_surfaces(SurfaceSpec("nav.item.tasks", "Tasks"))


def test_surface_with_unknown_type_raises():
    with pytest.raises(ImproperlyConfigured, match="unknown type 'usermenu'"):
        build_surfaces(SurfaceSpec("shell.usermenu.subscription", "Subscription"))


def test_surface_with_unknown_parent_raises():
    with pytest.raises(ImproperlyConfigured, match="unknown parent"):
        build_surfaces(SurfaceSpec("shell.item.tiktok", "TikTok", parent="shell.item.missing"))


def test_surface_parent_cycle_raises():
    with pytest.raises(ImproperlyConfigured, match="parent cycle"):
        build_surfaces(
            SurfaceSpec("shell.item.a", "A", parent="shell.item.b"),
            SurfaceSpec("shell.item.b", "B", parent="shell.item.a"),
        )


@pytest.mark.django_db
def test_registry_endpoint_returns_lookups(member_client):
    response = member_client.get(reverse("org_customization:registry"))

    assert response.status_code == 200
    body = response.json()
    assert [module["key"] for module in body["modules"]] == list(MODULE_KEYS)
    assert [surface_type["key"] for surface_type in body["surface_types"]] == list(SURFACE_TYPE_KEYS)
    assert body["surfaces"] == []
    tasks = body["modules"][1]
    assert tasks == {
        "key": "tasks",
        "label": "Tasks",
        "default_visible": True,
        "default_order": 1,
        "route_prefixes": ["/api/tasks/", "/api/task-types/", "/api/task-form-autosave/"],
    }


@pytest.mark.django_db
def test_registry_endpoint_requires_login(api_client):
    response = api_client.get(reverse("org_customization:registry"))

    assert response.status_code == 401
