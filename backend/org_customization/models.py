"""
Per-organization module/surface customization config (MED-455).

Both tables live in the PUBLIC schema because ``Organization`` does (see the
header comment in ``core/tenant_config.py``). Do NOT add these models to
``core.tenant_config.get_tenant_models()``.

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
