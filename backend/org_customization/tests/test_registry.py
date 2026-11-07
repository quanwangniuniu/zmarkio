import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.urls import reverse

from org_customization.models import Module, RoutePrefix, Surface, SurfaceType

pytestmark = pytest.mark.django_db

MODULE_KEYS = [
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
]

SURFACE_TYPE_KEYS = ["group", "item", "tab", "card", "panel", "provider", "filter", "section"]


def make_surface(module, surface_type, name, parent=None):
    return Surface.objects.create(
        module=Module.objects.get(key=module),
        surface_type=SurfaceType.objects.get(key=surface_type),
        name=name,
        label=name.replace("_", " ").title(),
        parent=parent,
    )


def assert_rejected(create):
    with pytest.raises(IntegrityError), transaction.atomic():
        create()


def test_tables_live_in_the_config_schema():
    with connection.cursor() as cursor:
        cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'config'")
        tables = {row[0] for row in cursor.fetchall()}
    assert tables == {"module", "surface_type", "surface", "route_prefix"}


def test_seeded_modules_are_the_23_in_order():
    assert list(Module.objects.values_list("key", flat=True)) == MODULE_KEYS
    assert list(Module.objects.values_list("default_order", flat=True)) == list(range(23))


def test_seeded_surface_types_are_the_8_types():
    assert list(SurfaceType.objects.values_list("key", flat=True)) == SURFACE_TYPE_KEYS


def test_seeded_route_prefixes_are_api_paths_owned_by_modules():
    assert RoutePrefix.objects.filter(module__isnull=False).count() == RoutePrefix.objects.count() > 0
    for path in RoutePrefix.objects.values_list("path", flat=True):
        assert path.startswith("/api/") and path.endswith("/"), path


def test_duplicate_module_key_is_rejected():
    assert_rejected(lambda: Module.objects.create(key="tasks", label="Tasks again"))


@pytest.mark.parametrize("key", ["Tasks", "meta-ads", "1tasks", "", "tasks.tab"])
def test_malformed_module_key_is_rejected(key):
    assert_rejected(lambda: Module.objects.create(key=key, label="Bad"))


def test_duplicate_surface_type_key_is_rejected():
    assert_rejected(lambda: SurfaceType.objects.create(key="tab", label="Tab again"))


def test_surface_key_is_built_from_module_type_and_name():
    surface = make_surface("tasks", "tab", "gantt")
    assert surface.key == "tasks.tab.gantt"


def test_duplicate_surface_is_rejected():
    make_surface("tasks", "tab", "gantt")
    assert_rejected(lambda: make_surface("tasks", "tab", "gantt"))


@pytest.mark.parametrize("name", ["Gantt", "my-actions", "", "gantt.extra"])
def test_malformed_surface_name_is_rejected(name):
    assert_rejected(lambda: make_surface("tasks", "tab", name))


def test_surface_with_parent_is_saved():
    ads_draft = make_surface("shell", "item", "ads_draft")
    facebook_meta = make_surface("shell", "item", "facebook_meta", parent=ads_draft)
    assert facebook_meta.parent.key == "shell.item.ads_draft"


def test_surface_parent_cycle_is_rejected():
    first = make_surface("shell", "item", "first")
    second = make_surface("shell", "item", "second", parent=first)
    first.parent = second
    with pytest.raises(ValidationError, match="cycle"):
        first.save()


def test_route_prefix_needs_exactly_one_owner():
    surface = make_surface("tasks", "tab", "gantt")
    tasks = Module.objects.get(key="tasks")
    assert_rejected(lambda: RoutePrefix.objects.create(path="/api/orphan/"))
    assert_rejected(lambda: RoutePrefix.objects.create(path="/api/both/", module=tasks, surface=surface))


def test_route_prefix_must_be_an_api_path():
    tasks = Module.objects.get(key="tasks")
    assert_rejected(lambda: RoutePrefix.objects.create(path="tasks", module=tasks))


def test_registry_endpoint_returns_lookups(member_client):
    make_surface("shell", "item", "facebook_meta", parent=make_surface("shell", "item", "ads_draft"))

    response = member_client.get(reverse("org_customization:registry"))

    assert response.status_code == 200
    body = response.json()
    assert [module["key"] for module in body["modules"]] == MODULE_KEYS
    assert [surface_type["key"] for surface_type in body["surface_types"]] == SURFACE_TYPE_KEYS
    assert body["modules"][1] == {
        "key": "tasks",
        "label": "Tasks",
        "default_visible": True,
        "default_order": 1,
        "route_prefixes": ["/api/tasks/", "/api/task-types/", "/api/task-form-autosave/"],
    }
    assert body["surfaces"][1] == {
        "key": "shell.item.facebook_meta",
        "label": "Facebook Meta",
        "default_visible": True,
        "default_order": 0,
        "route_prefixes": [],
        "parent": "shell.item.ads_draft",
    }


def test_registry_endpoint_reflects_database_changes(member_client):
    Module.objects.filter(key="miro").update(label="Whiteboard", default_visible=False)

    response = member_client.get(reverse("org_customization:registry"))

    miro = next(module for module in response.json()["modules"] if module["key"] == "miro")
    assert miro["label"] == "Whiteboard"
    assert miro["default_visible"] is False


def test_registry_endpoint_requires_login(api_client):
    response = api_client.get(reverse("org_customization:registry"))

    assert response.status_code == 401
