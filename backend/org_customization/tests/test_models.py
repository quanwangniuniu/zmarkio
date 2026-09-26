"""
MED-455 acceptance tests for the public-schema org config models.

Covers: unique constraints, cascade delete, updated_by nulling, and the
invariant that these models stay out of the tenant registry
(core.tenant_config.get_tenant_models()).
"""
import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from core.tenant_config import get_tenant_models
from org_customization.models import OrganizationModuleConfig, OrganizationSurfaceConfig

User = get_user_model()


@pytest.fixture
@pytest.mark.django_db
def organization():
    from core.models import Organization

    return Organization.objects.create(name="B1 Test Organization")


@pytest.fixture
@pytest.mark.django_db
def user(organization):
    return User.objects.create_user(
        username="b1-testuser",
        email="b1-testuser@test.com",
        password="testpass123",
        organization=organization,
    )


@pytest.mark.django_db
class TestUniqueConstraints:
    def test_org_module_key_is_unique(self, organization):
        OrganizationModuleConfig.objects.create(organization=organization, module_key="tasks")
        with pytest.raises(IntegrityError), transaction.atomic():
            OrganizationModuleConfig.objects.create(organization=organization, module_key="tasks")

    def test_org_surface_key_is_unique(self, organization):
        OrganizationSurfaceConfig.objects.create(
            organization=organization, surface_key="tasks.tab.gantt", module_key="tasks"
        )
        with pytest.raises(IntegrityError), transaction.atomic():
            OrganizationSurfaceConfig.objects.create(
                organization=organization, surface_key="tasks.tab.gantt", module_key="tasks"
            )

    def test_same_key_in_other_org_is_allowed(self, organization):
        from core.models import Organization

        other = Organization.objects.create(name="B1 Other Organization")
        OrganizationModuleConfig.objects.create(organization=organization, module_key="tasks")
        other_row = OrganizationModuleConfig.objects.create(organization=other, module_key="tasks")
        assert other_row.pk is not None


@pytest.mark.django_db
class TestCascadeDelete:
    def test_deleting_org_removes_module_configs(self, organization):
        OrganizationModuleConfig.objects.create(organization=organization, module_key="tasks")
        OrganizationModuleConfig.objects.create(organization=organization, module_key="overview")
        organization.delete()
        assert not OrganizationModuleConfig.objects.exists()

    def test_deleting_org_removes_surface_configs(self, organization):
        OrganizationSurfaceConfig.objects.create(
            organization=organization, surface_key="tasks.tab.gantt", module_key="tasks"
        )
        organization.delete()
        assert not OrganizationSurfaceConfig.objects.exists()


@pytest.mark.django_db
class TestUpdatedByNulling:
    def test_deleting_user_nulls_updated_by_on_module_config(self, organization, user):
        row = OrganizationModuleConfig.objects.create(
            organization=organization, module_key="tasks", updated_by=user
        )
        user.delete()
        row.refresh_from_db()
        assert row.updated_by is None

    def test_deleting_user_nulls_updated_by_on_surface_config(self, organization, user):
        row = OrganizationSurfaceConfig.objects.create(
            organization=organization,
            surface_key="tasks.tab.gantt",
            module_key="tasks",
            updated_by=user,
        )
        user.delete()
        row.refresh_from_db()
        assert row.updated_by is None


@pytest.mark.django_db
class TestSchemaAndFieldSemantics:
    def test_models_stay_out_of_the_tenant_registry(self):
        tenant_models = set(get_tenant_models())
        assert OrganizationModuleConfig not in tenant_models
        assert OrganizationSurfaceConfig not in tenant_models

    def test_defaults(self, organization):
        module = OrganizationModuleConfig.objects.create(organization=organization, module_key="tasks")
        surface = OrganizationSurfaceConfig.objects.create(
            organization=organization, surface_key="tasks.tab.gantt", module_key="tasks"
        )
        assert module.is_enabled is True
        assert surface.is_visible is True
        assert module.label_override is None
        assert module.sort_order is None
        assert module.updated_by is None

    def test_keys_are_plain_strings_not_foreign_keys(self, organization):
        # A key missing from the in-code registry must save fine; B4 ignores
        # orphaned rows at resolve time.
        row = OrganizationModuleConfig.objects.create(
            organization=organization, module_key="removed_module_someday"
        )
        assert row.module_key == "removed_module_someday"

    def test_surface_module_key_is_denormalized_string(self, organization):
        row = OrganizationSurfaceConfig.objects.create(
            organization=organization,
            surface_key="nav.group.manage",
            module_key="shell",  # nav.* rows denormalize to shell
        )
        assert row.module_key == "shell"
