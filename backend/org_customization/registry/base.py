"""
Contract types and key validation for the org-customization registry.

This module is contract only: no tables, no business logic. The registries
declare which modules and surfaces exist and how their keys are named; the
per-organization / per-project configuration tables (B1/B2) store overrides
keyed by these strings.

The naming spec in registry/README.md is FROZEN. Any change to the key
format, the type enum, or the module key list must be mirrored in A2
(frontend/src/lib/orgCustomization/keys.ts) and agreed with its owner.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping

from django.core.exceptions import ImproperlyConfigured

# Module keys are a single snake_case segment: ``tasks``, ``meta_ads``, ...
MODULE_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# Surface keys are ``<module>.<type>.<name>``; the name may itself contain
# further dot-separated snake_case segments (e.g. ``nav.item.ads_draft.facebook_meta``).
SURFACE_KEY_RE = re.compile(
    r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$"
)

# The ``type`` segment enum, frozen for now. ``usermenu`` is not in the
# original eight but appears in the frozen audit examples
# (``shell.usermenu.subscription``) and must stay registrable.
SURFACE_TYPES = (
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

# Reserved surface namespace: ``nav.*`` keys are global navigation surfaces.
# They are owned by the ``shell`` module for config purposes (the
# ``module_key`` denormalized onto B1/B2 rows).
NAV_NAMESPACE = "nav"
NAV_OWNER_MODULE = "shell"


@dataclass(frozen=True)
class ModuleSpec:
    """Declarative contract for one top-level module (see the frozen key list)."""

    key: str
    label: str
    default_visible: bool = True
    default_order: int = 0
    route_prefixes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SurfaceSpec:
    """Declarative contract for one UI surface inside a module."""

    key: str
    label: str
    default_visible: bool = True
    default_order: int = 0
    route_prefixes: tuple[str, ...] = ()


def module_key_for_surface(surface_key: str) -> str:
    """
    Owner module key for a surface key.

    Used to denormalize ``module_key`` onto OrganizationSurfaceConfig /
    ProjectSurfaceOverride rows (B1/B2) for per-module queries. The owner is
    the key's module segment, except ``nav.*`` which belongs to ``shell``.
    """
    first_segment = surface_key.split(".", 1)[0]
    return NAV_OWNER_MODULE if first_segment == NAV_NAMESPACE else first_segment


def validate_module_key(key: str) -> None:
    """Raise ImproperlyConfigured if ``key`` is not a well-formed module key."""
    if not MODULE_KEY_RE.match(key):
        raise ImproperlyConfigured(
            f"Malformed module key {key!r}: expected a single snake_case "
            f"segment matching {MODULE_KEY_RE.pattern!r}."
        )


def validate_surface_key(key: str, known_module_keys: Iterable[str]) -> None:
    """
    Raise ImproperlyConfigured if ``key`` is not a well-formed surface key.

    Checks the ``<module>.<type>.<name>`` format, that the type segment is in
    SURFACE_TYPES, and that the module segment is a registered module key or
    the reserved ``nav`` namespace.
    """
    if not SURFACE_KEY_RE.match(key):
        raise ImproperlyConfigured(
            f"Malformed surface key {key!r}: expected '<module>.<type>.<name>' "
            f"with snake_case segments (name may contain dots), matching "
            f"{SURFACE_KEY_RE.pattern!r}."
        )
    module_segment, type_segment, _ = key.split(".", 2)
    if type_segment not in SURFACE_TYPES:
        raise ImproperlyConfigured(
            f"Malformed surface key {key!r}: type {type_segment!r} is not one "
            f"of {SURFACE_TYPES}."
        )
    if module_segment != NAV_NAMESPACE and module_segment not in set(known_module_keys):
        raise ImproperlyConfigured(
            f"Malformed surface key {key!r}: module segment {module_segment!r} "
            f"is neither a registered module key nor the reserved "
            f"{NAV_NAMESPACE!r} namespace."
        )


def build_module_registry(specs: Iterable[ModuleSpec]) -> Mapping[str, ModuleSpec]:
    """
    Collect ModuleSpecs into a mapping keyed by module key.

    Duplicate keys and malformed key formats raise ImproperlyConfigured.
    """
    registry: dict[str, ModuleSpec] = {}
    for spec in specs:
        validate_module_key(spec.key)
        if spec.key in registry:
            raise ImproperlyConfigured(
                f"Duplicate module key {spec.key!r} in the module registry."
            )
        registry[spec.key] = spec
    return registry


def build_surface_registry(
    specs: Iterable[SurfaceSpec], known_module_keys: Iterable[str]
) -> Mapping[str, SurfaceSpec]:
    """
    Collect SurfaceSpecs into a mapping keyed by surface key.

    Duplicate keys and malformed key formats raise ImproperlyConfigured.
    """
    known = tuple(known_module_keys)
    registry: dict[str, SurfaceSpec] = {}
    for spec in specs:
        validate_surface_key(spec.key, known)
        if spec.key in registry:
            raise ImproperlyConfigured(
                f"Duplicate surface key {spec.key!r} in the surface registry."
            )
        registry[spec.key] = spec
    return registry
