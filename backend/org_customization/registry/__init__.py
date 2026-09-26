"""
Module / surface registries for org customization.

The key lists below are the shared contract with A2
(frontend/src/lib/orgCustomization/keys.ts): they must match character for
character. See registry/README.md for the frozen naming spec and the guide
for adding a module.
"""
from __future__ import annotations

from typing import Mapping

from org_customization.registry.base import (
    ModuleSpec,
    SurfaceSpec,
    build_module_registry,
    build_surface_registry,
    module_key_for_surface,  # noqa: F401  (re-exported: B1/B2 module_key derivation)
)

# ---------------------------------------------------------------------------
# The 23 module keys — frozen complete list (MED-453). Individual surfaces
# are filled in by the 23 module issues in M4; the module keys live here once.
# Declaration order sets default_order (10, 20, ...); each module owns the
# backend request path prefixes used by enforcement (see README for the
# matching rules).
# ---------------------------------------------------------------------------
MODULE_SPECS: tuple[ModuleSpec, ...] = (
    ModuleSpec("overview", "Overview", default_order=10, route_prefixes=("/api/dashboard/",)),
    ModuleSpec("tasks", "Tasks", default_order=20, route_prefixes=("/api/tasks/",)),
    ModuleSpec("campaigns", "Campaigns", default_order=30, route_prefixes=("/api/campaigns/",)),
    ModuleSpec("meta_ads", "Meta Ads", default_order=40, route_prefixes=("/api/meta_ads/",)),
    ModuleSpec("decisions", "Decisions", default_order=50, route_prefixes=("/api/decisions/",)),
    ModuleSpec(
        "budget_pools",
        "Budget Pools",
        default_order=60,
        route_prefixes=("/api/budgets/", "/budgets/"),
    ),
    ModuleSpec(
        "spreadsheets", "Spreadsheets", default_order=70, route_prefixes=("/api/spreadsheet/",)
    ),
    ModuleSpec(
        "variations_studio",
        "Variations Studio",
        default_order=80,
        route_prefixes=("/api/ad_copy_variation/",),
    ),
    ModuleSpec(
        "ads_draft",
        "Ads Draft",
        default_order=90,
        route_prefixes=("/api/facebook_meta/", "/api/google_ads/", "/api/tiktok/"),
    ),
    ModuleSpec(
        "email_draft",
        "Email Draft",
        default_order=100,
        route_prefixes=("/api/klaviyo/", "/api/mailchimp/"),
    ),
    ModuleSpec("notion", "Notion", default_order=110, route_prefixes=("/api/notion/",)),
    ModuleSpec(
        "meetings",
        "Meetings",
        default_order=120,
        route_prefixes=("/api/projects/{project_id}/meetings",),
    ),
    ModuleSpec(
        "calendar",
        "Calendar",
        default_order=130,
        route_prefixes=("/api/calendars/", "/api/events/"),
    ),
    ModuleSpec("messages", "Messages", default_order=140, route_prefixes=("/api/chat/",)),
    ModuleSpec("miro", "Miro", default_order=150, route_prefixes=("/api/miro/",)),
    ModuleSpec("workflows", "Workflows", default_order=160, route_prefixes=("/api/workflows/",)),
    ModuleSpec("timeline", "Timeline", default_order=170, route_prefixes=("/api/tasks/gantt/",)),
    ModuleSpec("csm", "CSM", default_order=180, route_prefixes=("/api/csm/",)),
    ModuleSpec(
        "admin",
        "Admin",
        default_order=190,
        route_prefixes=("/api/access_control/", "/api/audit/", "/api/policy/"),
    ),
    ModuleSpec(
        "integrations",
        "Integrations",
        default_order=200,
        route_prefixes=(
            "/api/slack/",
            "/api/v1/zoom/",
            "/api/v1/linear/",
            "/api/google-calendar/",
            "/api/google-docs/",
            "/api/facebook_integration/",
        ),
    ),
    ModuleSpec("agent", "Agent", default_order=210, route_prefixes=("/api/agent/",)),
    ModuleSpec(
        "notifications",
        "Notifications",
        default_order=220,
        route_prefixes=("/api/notifications/",),
    ),
    ModuleSpec("shell", "Shell", default_order=230, route_prefixes=("/api/stripe/", "/users/")),
)

# ---------------------------------------------------------------------------
# Seed surfaces — the examples frozen in the naming spec. The individual
# surfaces of each module are registered by the M4 module issues on top of
# this list (keep A2's SurfaceKey union in sync on every addition).
# ---------------------------------------------------------------------------
SURFACE_SPECS: tuple[SurfaceSpec, ...] = (
    SurfaceSpec("nav.group.manage", "Manage"),
    SurfaceSpec("nav.item.tasks", "Tasks", default_order=10, route_prefixes=("/tasks",)),
    SurfaceSpec(
        "nav.item.ads_draft.facebook_meta",
        "Meta Ads Draft",
        default_order=20,
        route_prefixes=("/ads",),
    ),
    SurfaceSpec(
        "tasks.tab.gantt",
        "Gantt",
        default_order=30,
        route_prefixes=("/tasks",),
    ),
    SurfaceSpec(
        "overview.card.meetings",
        "Meetings",
        default_order=40,
        route_prefixes=("/overview",),
    ),
    SurfaceSpec(
        "integrations.provider.slack",
        "Slack",
        default_order=10,
        route_prefixes=("/integrations",),
    ),
    SurfaceSpec(
        "shell.usermenu.subscription",
        "Subscription",
        default_order=10,
        route_prefixes=("/subscription",),
    ),
)


def get_module_registry() -> Mapping[str, ModuleSpec]:
    """Return the module registry keyed by module key. Validates on every call."""
    return build_module_registry(MODULE_SPECS)


def get_surface_registry() -> Mapping[str, SurfaceSpec]:
    """Return the surface registry keyed by surface key. Validates on every call."""
    return build_surface_registry(SURFACE_SPECS, get_module_registry().keys())


def validate_registry() -> None:
    """Validate both registries. Called at Django startup (apps.py ready())."""
    get_module_registry()
    get_surface_registry()
