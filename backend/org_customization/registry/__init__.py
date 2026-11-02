import re
from functools import lru_cache

from django.core.exceptions import ImproperlyConfigured

from .base import ModuleSpec, SurfaceSpec, SurfaceTypeSpec
from .modules import MODULES
from .surfaces import SURFACE_TYPES, SURFACES

NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _index(specs, kind):
    registry = {}
    for spec in specs:
        if spec.key in registry:
            raise ImproperlyConfigured(f"Duplicate {kind} key '{spec.key}'.")
        registry[spec.key] = spec
    return registry


def build_name_registry(specs, kind):
    registry = _index(specs, kind)
    for key in registry:
        if not NAME_RE.match(key):
            raise ImproperlyConfigured(f"Malformed {kind} key '{key}'.")
    return registry


def build_surface_registry(surfaces, modules, surface_types):
    registry = _index(surfaces, "surface")
    for spec in registry.values():
        parts = spec.key.split(".")
        if len(parts) != 3 or not all(NAME_RE.match(part) for part in parts):
            raise ImproperlyConfigured(
                f"Malformed surface key '{spec.key}', expected <module>.<type>.<name>."
            )
        if parts[0] not in modules:
            raise ImproperlyConfigured(f"Surface '{spec.key}' uses unknown module '{parts[0]}'.")
        if parts[1] not in surface_types:
            raise ImproperlyConfigured(f"Surface '{spec.key}' uses unknown type '{parts[1]}'.")
        if spec.parent is not None and spec.parent not in registry:
            raise ImproperlyConfigured(f"Surface '{spec.key}' has unknown parent '{spec.parent}'.")
    for spec in registry.values():
        seen = {spec.key}
        parent = spec.parent
        while parent is not None:
            if parent in seen:
                raise ImproperlyConfigured(f"Surface '{spec.key}' has a parent cycle.")
            seen.add(parent)
            parent = registry[parent].parent
    return registry


@lru_cache(maxsize=None)
def get_module_registry() -> dict[str, ModuleSpec]:
    return build_name_registry(MODULES, "module")


@lru_cache(maxsize=None)
def get_surface_type_registry() -> dict[str, SurfaceTypeSpec]:
    return build_name_registry(SURFACE_TYPES, "surface type")


@lru_cache(maxsize=None)
def get_surface_registry() -> dict[str, SurfaceSpec]:
    return build_surface_registry(SURFACES, get_module_registry(), get_surface_type_registry())
