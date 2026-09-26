"""
MED-456 acceptance tests for the tenant-schema project override models.

Runs under the tenant schema via core.test_utils.TenantTestCase (the existing
tenant fixture). Covers: three-state nullable override fields with no
defaults, unique constraints, cascade delete, and that the tables are
actually created in the org schema (registered in get_tenant_models after
Project).
"""
from django.db import IntegrityError, connection

from core.models import Project
from core.test_utils import TenantTestCase
from org_customization.models import ProjectModuleOverride, ProjectSurfaceOverride


class ProjectOverrideModelTest(TenantTestCase):
    def _project(self, name="Override Project"):
        return Project.objects.create(organization_id=self.test_org.id, name=name)

    # ------------------------------------------------------------------
    # Three-state semantics: null = inherit, True/False = explicit override.
    # ------------------------------------------------------------------
    def test_module_override_three_state_roundtrip(self):
        project = self._project()
        row = ProjectModuleOverride.objects.create(project=project, module_key="tasks")
        self.assertIsNone(row.is_enabled)  # created without explicit value -> inherit

        row.is_enabled = True
        row.save()
        row.refresh_from_db()
        self.assertIs(row.is_enabled, True)

        row.is_enabled = False
        row.save()
        row.refresh_from_db()
        self.assertIs(row.is_enabled, False)

    def test_surface_override_three_state_roundtrip(self):
        project = self._project()
        row = ProjectSurfaceOverride.objects.create(
            project=project, surface_key="tasks.tab.gantt", module_key="tasks"
        )
        self.assertIsNone(row.is_visible)  # inherit

        row.is_visible = False
        row.save()
        row.refresh_from_db()
        self.assertIs(row.is_visible, False)

    def test_override_fields_have_no_defaults(self):
        for field_name in ("is_enabled", "label_override", "sort_order"):
            field = ProjectModuleOverride._meta.get_field(field_name)
            self.assertTrue(field.null, field_name)
            self.assertFalse(field.has_default(), field_name)
        for field_name in ("is_visible", "label_override", "sort_order"):
            field = ProjectSurfaceOverride._meta.get_field(field_name)
            self.assertTrue(field.null, field_name)
            self.assertFalse(field.has_default(), field_name)

    # ------------------------------------------------------------------
    # Unique constraints
    # ------------------------------------------------------------------
    def test_module_override_unique_per_project(self):
        project = self._project()
        ProjectModuleOverride.objects.create(project=project, module_key="tasks")
        with self.assertRaises(IntegrityError):
            ProjectModuleOverride.objects.create(project=project, module_key="tasks")

    def test_surface_override_unique_per_project(self):
        project = self._project()
        ProjectSurfaceOverride.objects.create(
            project=project, surface_key="tasks.tab.gantt", module_key="tasks"
        )
        with self.assertRaises(IntegrityError):
            ProjectSurfaceOverride.objects.create(
                project=project, surface_key="tasks.tab.gantt", module_key="tasks"
            )

    def test_same_key_allowed_on_another_project(self):
        project_a = self._project("Project A")
        project_b = self._project("Project B")
        ProjectModuleOverride.objects.create(project=project_a, module_key="tasks")
        row = ProjectModuleOverride.objects.create(project=project_b, module_key="tasks")
        self.assertIsNotNone(row.pk)

    # ------------------------------------------------------------------
    # Cascade delete
    # ------------------------------------------------------------------
    def test_deleting_project_removes_overrides(self):
        project = self._project()
        ProjectModuleOverride.objects.create(project=project, module_key="tasks")
        ProjectSurfaceOverride.objects.create(
            project=project, surface_key="tasks.tab.gantt", module_key="tasks"
        )
        project.delete()
        self.assertFalse(ProjectModuleOverride.objects.exists())
        self.assertFalse(ProjectSurfaceOverride.objects.exists())

    # ------------------------------------------------------------------
    # Tenant-schema placement
    # ------------------------------------------------------------------
    def test_tables_live_in_the_org_schema(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT schemaname FROM pg_tables WHERE tablename = %s",
                ["org_customization_projectmoduleoverride"],
            )
            schemas = {row[0] for row in cursor.fetchall()}
        self.assertIn(self.test_schema, schemas)

    def test_module_key_is_plain_string(self):
        # Keys are strings, not FKs: rows for removed modules are ignored by
        # the B4 resolver instead of breaking constraints.
        project = self._project()
        row = ProjectSurfaceOverride.objects.create(
            project=project, surface_key="gone.module.card", module_key="gone"
        )
        self.assertEqual(row.module_key, "gone")
