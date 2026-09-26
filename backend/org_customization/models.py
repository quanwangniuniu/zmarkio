"""
Per-organization / per-project customization config (MED-455, MED-456).

Schema placement follows core/tenant_config.py:

* OrganizationModuleConfig / OrganizationSurfaceConfig live in the PUBLIC
  schema because ``Organization`` does.
* ProjectModuleOverride / ProjectSurfaceOverride live in the PER-ORG (tenant)
  schema because ``Project`` does — they are registered in
  ``core.tenant_config.get_tenant_models()`` after ``Project``.

Do NOT move the org-level tables into the tenant registry.

``module_key`` / ``surface_key`` are strings, not foreign keys: the registry
lives in code (``org_customization.registry``), so when a module is removed
the orphaned rows are simply ignored by the B4 resolver. Use
``module_key_for_surface()`` when denormalizing ``module_key`` onto surface
rows (``nav.*`` surfaces belong to ``shell``).
"""
from django.conf import settings
from django.db import models

from core.models import Organization, TimeStampedModel


class OrganizationModuleConfig(TimeStampedModel):
    """Org-level module toggle / label / order override."""

    organization = models.ForeignKey(
        Organization,
        related_name='module_configs',
        on_delete=models.CASCADE,
    )
    module_key = models.CharField(max_length=64)
    is_enabled = models.BooleanField(default=True)
    label_override = models.CharField(max_length=100, blank=True, null=True)
    sort_order = models.IntegerField(null=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        unique_together = ('organization', 'module_key')


class OrganizationSurfaceConfig(TimeStampedModel):
    """Org-level surface visibility / label / order override."""

    organization = models.ForeignKey(
        Organization,
        related_name='surface_configs',
        on_delete=models.CASCADE,
    )
    surface_key = models.CharField(max_length=128, db_index=True)
    # Denormalized from the surface key (module_key_for_surface) for
    # per-module queries.
    module_key = models.CharField(max_length=64, db_index=True)
    is_visible = models.BooleanField(default=True)
    label_override = models.CharField(max_length=100, blank=True, null=True)
    sort_order = models.IntegerField(null=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        unique_together = ('organization', 'surface_key')


class ProjectModuleOverride(TimeStampedModel):
    """
    Project-level module config override (tenant schema).

    Three-state semantics on the override fields (``is_enabled``,
    ``label_override``, ``sort_order``): ``null`` = inherit from the level
    above (org config / registry default); a non-null value is an explicit
    override. Deliberately NO field defaults — creating a row must not
    silently mean "override everything", or the inherit semantics would be
    lost.
    """

    project = models.ForeignKey(
        'core.Project',
        related_name='module_overrides',
        on_delete=models.CASCADE,
    )
    module_key = models.CharField(max_length=64)
    is_enabled = models.BooleanField(null=True)  # null = inherit from org config
    label_override = models.CharField(max_length=100, blank=True, null=True)
    sort_order = models.IntegerField(null=True)  # null = inherit
    # No updated_by here (unlike the org-level tables): the project-override
    # scope keeps these rows minimal.

    class Meta:
        unique_together = ('project', 'module_key')


class ProjectSurfaceOverride(TimeStampedModel):
    """
    Project-level surface config override (tenant schema).

    Three-state semantics on the override fields (``is_visible``,
    ``label_override``, ``sort_order``): ``null`` = inherit from the level
    above (org config / registry default); a non-null value is an explicit
    override. Deliberately NO field defaults.
    """

    project = models.ForeignKey(
        'core.Project',
        related_name='surface_overrides',
        on_delete=models.CASCADE,
    )
    surface_key = models.CharField(max_length=128, db_index=True)
    module_key = models.CharField(max_length=64, db_index=True)
    is_visible = models.BooleanField(null=True)  # null = inherit
    label_override = models.CharField(max_length=100, blank=True, null=True)
    sort_order = models.IntegerField(null=True)  # null = inherit

    class Meta:
        unique_together = ('project', 'surface_key')
