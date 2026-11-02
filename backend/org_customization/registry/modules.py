from dataclasses import replace

from .base import ModuleSpec

_MODULES = (
    ModuleSpec(
        key="overview",
        label="Overview",
        route_prefixes=("/api/dashboard/",),
    ),
    ModuleSpec(
        key="tasks",
        label="Tasks",
        route_prefixes=("/api/tasks/", "/api/task-types/", "/api/task-form-autosave/"),
    ),
    ModuleSpec(
        key="campaigns",
        label="Campaigns",
        route_prefixes=(
            "/api/campaigns/",
            "/api/campaign-templates/",
            "/api/campaign-calendar-links/",
            "/api/campaign-decision-links/",
            "/api/campaign-task-links/",
        ),
    ),
    ModuleSpec(
        key="meta_ads",
        label="Meta Ads",
        route_prefixes=("/api/meta_ads/",),
    ),
    ModuleSpec(
        key="decisions",
        label="Decisions",
        route_prefixes=("/api/decisions/",),
    ),
    ModuleSpec(
        key="budget_pools",
        label="Budget Pools",
        route_prefixes=("/api/budgets/pools/",),
    ),
    ModuleSpec(
        key="spreadsheets",
        label="Spreadsheets",
        route_prefixes=("/api/spreadsheet/", "/api/projects/<project_id>/spreadsheets/"),
    ),
    ModuleSpec(
        key="variations_studio",
        label="Variations Studio",
        route_prefixes=("/api/ad_copy_variation/",),
    ),
    ModuleSpec(
        key="ads_draft",
        label="Ads Draft",
        route_prefixes=("/api/facebook_meta/", "/api/tiktok/", "/api/google_ads/"),
    ),
    ModuleSpec(
        key="email_draft",
        label="Email Draft",
        route_prefixes=("/api/mailchimp/", "/api/klaviyo/"),
    ),
    ModuleSpec(
        key="notion",
        label="Notion",
        route_prefixes=("/api/notion/",),
    ),
    ModuleSpec(
        key="meetings",
        label="Meetings",
        route_prefixes=("/api/projects/<project_id>/meetings/",),
    ),
    ModuleSpec(
        key="calendar",
        label="Calendar",
        route_prefixes=(
            "/api/calendars/",
            "/api/events/",
            "/api/booking-links/",
            "/api/freebusy/",
            "/api/subscriptions/",
            "/api/views/",
            "/api/derived-events/",
            "/api/public/",
        ),
    ),
    ModuleSpec(
        key="messages",
        label="Messages",
        route_prefixes=("/api/chat/",),
    ),
    ModuleSpec(
        key="miro",
        label="Miro",
        route_prefixes=("/api/miro/",),
    ),
    ModuleSpec(
        key="workflows",
        label="Workflows",
        route_prefixes=("/api/workflows/",),
    ),
    ModuleSpec(
        key="timeline",
        label="Timeline",
    ),
    ModuleSpec(
        key="csm",
        label="Customer Service",
        route_prefixes=("/api/csm/",),
    ),
    ModuleSpec(
        key="admin",
        label="Administration",
        route_prefixes=("/api/access_control/", "/api/audit/", "/api/teams/"),
    ),
    ModuleSpec(
        key="integrations",
        label="Integrations",
        route_prefixes=(
            "/api/slack/",
            "/api/v1/zoom/",
            "/api/v1/linear/",
            "/api/google-docs/",
            "/api/google-calendar/",
            "/api/facebook_integration/",
        ),
    ),
    ModuleSpec(
        key="agent",
        label="AI Agent",
        route_prefixes=("/api/agent/",),
    ),
    ModuleSpec(
        key="notifications",
        label="Notifications",
        route_prefixes=("/api/notifications/", "/api/notification-preferences/"),
    ),
    ModuleSpec(
        key="shell",
        label="App Shell",
    ),
)

MODULES = tuple(replace(spec, default_order=index) for index, spec in enumerate(_MODULES))
