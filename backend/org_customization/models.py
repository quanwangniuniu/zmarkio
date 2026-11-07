from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q

KEY_PATTERN = r"^[a-z][a-z0-9_]*$"
key_validator = RegexValidator(KEY_PATTERN, "Use lowercase letters, digits and underscores, starting with a letter.")


class Module(models.Model):
    key = models.CharField(max_length=64, unique=True, validators=[key_validator])
    label = models.CharField(max_length=128)
    default_visible = models.BooleanField(default=True)
    default_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'config"."module'
        ordering = ["default_order", "key"]
        constraints = [
            models.CheckConstraint(check=Q(key__regex=KEY_PATTERN), name="config_module_key_format"),
        ]

    def __str__(self):
        return self.key


class SurfaceType(models.Model):
    key = models.CharField(max_length=64, unique=True, validators=[key_validator])
    label = models.CharField(max_length=128)

    class Meta:
        db_table = 'config"."surface_type'
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(check=Q(key__regex=KEY_PATTERN), name="config_surface_type_key_format"),
        ]

    def __str__(self):
        return self.key


class Surface(models.Model):
    module = models.ForeignKey(Module, on_delete=models.PROTECT, related_name="surfaces")
    surface_type = models.ForeignKey(SurfaceType, on_delete=models.PROTECT, related_name="surfaces")
    name = models.CharField(max_length=64, validators=[key_validator])
    label = models.CharField(max_length=128)
    default_visible = models.BooleanField(default=True)
    default_order = models.PositiveIntegerField(default=0)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )

    class Meta:
        db_table = 'config"."surface'
        ordering = ["module__default_order", "default_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["module", "surface_type", "name"], name="config_surface_unique_key"
            ),
            models.CheckConstraint(check=Q(name__regex=KEY_PATTERN), name="config_surface_name_format"),
            models.CheckConstraint(check=~Q(parent=models.F("id")), name="config_surface_not_own_parent"),
        ]

    def __str__(self):
        return self.key

    @property
    def key(self):
        return f"{self.module.key}.{self.surface_type.key}.{self.name}"

    def clean(self):
        seen = {self.pk}
        parent = self.parent
        while parent is not None:
            if parent.pk in seen:
                raise ValidationError({"parent": "Surface parents cannot form a cycle."})
            seen.add(parent.pk)
            parent = parent.parent

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class RoutePrefix(models.Model):
    path = models.CharField(max_length=255)
    module = models.ForeignKey(
        Module, null=True, blank=True, on_delete=models.CASCADE, related_name="route_prefixes"
    )
    surface = models.ForeignKey(
        Surface, null=True, blank=True, on_delete=models.CASCADE, related_name="route_prefixes"
    )

    class Meta:
        db_table = 'config"."route_prefix'
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                check=Q(module__isnull=False, surface__isnull=True)
                | Q(module__isnull=True, surface__isnull=False),
                name="config_route_prefix_one_owner",
            ),
            models.CheckConstraint(check=Q(path__regex=r"^/api/.*/$"), name="config_route_prefix_api_path"),
            models.UniqueConstraint(fields=["module", "path"], name="config_route_prefix_unique_module_path"),
            models.UniqueConstraint(fields=["surface", "path"], name="config_route_prefix_unique_surface_path"),
        ]

    def __str__(self):
        return self.path
