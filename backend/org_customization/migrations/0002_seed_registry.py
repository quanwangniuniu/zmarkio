from django.db import migrations

MODULES = [
    ("overview", "Overview", ["/api/dashboard/"]),
    ("tasks", "Tasks", ["/api/tasks/", "/api/task-types/", "/api/task-form-autosave/"]),
    (
        "campaigns",
        "Campaigns",
        [
            "/api/campaigns/",
            "/api/campaign-templates/",
            "/api/campaign-calendar-links/",
            "/api/campaign-decision-links/",
            "/api/campaign-task-links/",
        ],
    ),
    ("meta_ads", "Meta Ads", ["/api/meta_ads/"]),
    ("decisions", "Decisions", ["/api/decisions/"]),
    ("budget_pools", "Budget Pools", ["/api/budgets/pools/"]),
    ("spreadsheets", "Spreadsheets", ["/api/spreadsheet/", "/api/projects/<project_id>/spreadsheets/"]),
    ("variations_studio", "Variations Studio", ["/api/ad_copy_variation/"]),
    ("ads_draft", "Ads Draft", ["/api/facebook_meta/", "/api/tiktok/", "/api/google_ads/"]),
    ("email_draft", "Email Draft", ["/api/mailchimp/", "/api/klaviyo/"]),
    ("notion", "Notion", ["/api/notion/"]),
    ("meetings", "Meetings", ["/api/projects/<project_id>/meetings/"]),
    (
        "calendar",
        "Calendar",
        [
            "/api/calendars/",
            "/api/events/",
            "/api/booking-links/",
            "/api/freebusy/",
            "/api/subscriptions/",
            "/api/views/",
            "/api/derived-events/",
            "/api/public/",
        ],
    ),
    ("messages", "Messages", ["/api/chat/"]),
    ("miro", "Miro", ["/api/miro/"]),
    ("workflows", "Workflows", ["/api/workflows/"]),
    ("timeline", "Timeline", []),
    ("csm", "Customer Service", ["/api/csm/"]),
    ("admin", "Administration", ["/api/access_control/", "/api/audit/", "/api/teams/"]),
    (
        "integrations",
        "Integrations",
        [
            "/api/slack/",
            "/api/v1/zoom/",
            "/api/v1/linear/",
            "/api/google-docs/",
            "/api/google-calendar/",
            "/api/facebook_integration/",
        ],
    ),
    ("agent", "AI Agent", ["/api/agent/"]),
    ("notifications", "Notifications", ["/api/notifications/", "/api/notification-preferences/"]),
    ("shell", "App Shell", []),
]

SURFACE_TYPES = [
    ("group", "Group"),
    ("item", "Item"),
    ("tab", "Tab"),
    ("card", "Card"),
    ("panel", "Panel"),
    ("provider", "Provider"),
    ("filter", "Filter"),
    ("section", "Section"),
]


def seed(apps, schema_editor):
    Module = apps.get_model("org_customization", "Module")
    SurfaceType = apps.get_model("org_customization", "SurfaceType")
    RoutePrefix = apps.get_model("org_customization", "RoutePrefix")

    for order, (key, label, paths) in enumerate(MODULES):
        module = Module.objects.create(key=key, label=label, default_order=order)
        RoutePrefix.objects.bulk_create(RoutePrefix(module=module, path=path) for path in paths)
    SurfaceType.objects.bulk_create(SurfaceType(key=key, label=label) for key, label in SURFACE_TYPES)


def unseed(apps, schema_editor):
    apps.get_model("org_customization", "RoutePrefix").objects.all().delete()
    apps.get_model("org_customization", "SurfaceType").objects.all().delete()
    apps.get_model("org_customization", "Module").objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("org_customization", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
