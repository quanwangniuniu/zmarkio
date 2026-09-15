"""
Slug-only resource lookups.

Covers the shared slug infrastructure:
- slug generation rules (source field, duplicates, numeric titles, empty fallback, rename stability)
- resolve_lookup_kwargs routing (slug vs UUID pk vs numeric)
- API behaviour: slug URL resolves (200); task numeric URL resolves (200); other resources numeric URL 404
- migration-style backfill helper
"""
import uuid

from django.contrib.auth import get_user_model
from django.core.management import call_command
from rest_framework.test import APITestCase

from core.models import Organization, Project, ProjectMember
from core.slug_backfill import backfill_slugs
from core.slug_mixins import resolve_lookup_kwargs
from task.models import Task
from meetings.models import Meeting, MeetingTypeDefinition
from decision.models import Decision
from spreadsheet.models import Spreadsheet
from automationWorkflow.models import Workflow

User = get_user_model()


class ResolveLookupKwargsTest(APITestCase):
    """Pure routing rules of the shared lookup helper."""

    def test_plain_string_resolves_by_slug(self):
        self.assertEqual(resolve_lookup_kwargs("april-budget"), {"slug": "april-budget"})

    def test_numeric_string_resolves_by_slug_not_pk(self):
        # slug-only: numeric identifiers are never treated as primary keys
        self.assertEqual(resolve_lookup_kwargs("123"), {"slug": "123"})

    def test_uuid_resolves_by_pk(self):
        value = str(uuid.uuid4())
        self.assertEqual(resolve_lookup_kwargs(value), {"pk": value})

    def test_custom_pk_and_slug_fields(self):
        value = str(uuid.uuid4())
        self.assertEqual(
            resolve_lookup_kwargs(value, "workflow_id", "workflow__slug"),
            {"workflow_id": value},
        )
        self.assertEqual(
            resolve_lookup_kwargs("client-flow", "workflow_id", "workflow__slug"),
            {"workflow__slug": "client-flow"},
        )


class SlugGenerationTest(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="slugger@example.com", username="slugger", password="testpass123"
        )
        self.organization = Organization.objects.create(name="Slug Org")
        self.project = Project.objects.create(
            name="Slug Project", organization=self.organization
        )

    def _task(self, summary):
        return Task.objects.create(
            summary=summary, type="asset", project=self.project, owner=self.user
        )

    def test_slug_from_source_field(self):
        task = self._task("Design Homepage Banner")
        self.assertEqual(task.slug, "design-homepage-banner")

    def test_duplicate_titles_get_counter_suffix(self):
        first = self._task("Quarterly Review")
        second = self._task("Quarterly Review")
        self.assertEqual(first.slug, "quarterly-review")
        self.assertEqual(second.slug, "quarterly-review-1")

    def test_pure_numeric_title_is_prefixed(self):
        # a slug that looks like a legacy numeric id would be ambiguous
        task = self._task("2024")
        self.assertEqual(task.slug, "task-2024")

    def test_unslugifiable_title_falls_back_to_uuid_slug(self):
        task = self._task("测试中文标题")
        self.assertRegex(task.slug, r"^task-[0-9a-f]{8}$")

    def test_slug_is_stable_across_rename(self):
        task = self._task("Original Name")
        original_slug = task.slug
        task.summary = "Renamed Completely"
        task.save()
        task = Task.objects.get(pk=task.pk)
        self.assertEqual(task.slug, original_slug)

    def test_project_slug_generated(self):
        self.assertEqual(self.project.slug, "slug-project")


class SlugOnlyApiLookupTest(APITestCase):
    """Slug URLs resolve; numeric task URLs also resolve; other resources stay slug-only."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="member@example.com", username="member", password="testpass123"
        )
        self.organization = Organization.objects.create(name="API Org")
        self.project = Project.objects.create(
            name="API Project", organization=self.organization
        )
        ProjectMember.objects.create(
            user=self.user, project=self.project, role="Team Leader", is_active=True
        )
        self.user.active_project = self.project
        self.user.save(update_fields=["active_project"])
        
        self.task = Task.objects.create(
            summary="Slug Lookup Task",
            type="asset",
            project=self.project,
            owner=self.user,
        )
        self.meeting_type = MeetingTypeDefinition.objects.create(
            project=self.project,
            slug="planning",
            label="Planning",
        )
        self.meeting = Meeting.objects.create(
            title="Slug Lookup Meeting",
            project=self.project,
            type_definition=self.meeting_type,
        )
        self.decision = Decision.objects.create(
            title="Slug Lookup Decision",
            project=self.project,
            author=self.user,
        )
        self.spreadsheet = Spreadsheet.objects.create(
            name="Slug Lookup Spreadsheet",
            project=self.project,
        )
        self.workflow = Workflow.objects.create(
            name="Slug Lookup Workflow",
            project=self.project,
            created_by=self.user,
        )
        
        self.client.force_authenticate(user=self.user)

    def test_task_slug_url_resolves(self):
        response = self.client.get(f"/api/tasks/{self.task.slug}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], self.task.id)
        self.assertEqual(response.data["slug"], self.task.slug)

    def test_task_numeric_url_resolves(self):
        response = self.client.get(f"/api/tasks/{self.task.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], self.task.id)
        self.assertEqual(response.data["slug"], self.task.slug)

    def test_unknown_slug_404(self):
        response = self.client.get("/api/tasks/no-such-task/")
        self.assertEqual(response.status_code, 404)

    def test_project_slug_url_resolves(self):
        response = self.client.get(f"/api/core/projects/{self.project.slug}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], self.project.id)

    def test_project_numeric_url_is_rejected(self):
        response = self.client.get(f"/api/core/projects/{self.project.id}/")
        self.assertEqual(response.status_code, 404)

    def test_meeting_slug_url_resolves(self):
        response = self.client.get(f"/api/projects/{self.project.slug}/meetings/{self.meeting.slug}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], self.meeting.id)

    def test_meeting_numeric_url_is_rejected(self):
        response = self.client.get(f"/api/projects/{self.project.slug}/meetings/{self.meeting.id}/")
        self.assertEqual(response.status_code, 404)

    def test_spreadsheet_slug_url_resolves(self):
        response = self.client.get(f"/api/spreadsheet/spreadsheets/{self.spreadsheet.slug}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], self.spreadsheet.id)

    def test_spreadsheet_numeric_url_is_rejected(self):
        response = self.client.get(f"/api/spreadsheet/spreadsheets/{self.spreadsheet.id}/")
        self.assertEqual(response.status_code, 404)

    def test_workflow_slug_url_resolves(self):
        response = self.client.get(f"/api/workflows/{self.workflow.slug}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], self.workflow.id)

    def test_workflow_numeric_url_is_rejected(self):
        response = self.client.get(f"/api/workflows/{self.workflow.id}/")
        self.assertEqual(response.status_code, 404)


class QaRegressionTest(APITestCase):
    """
    Regressions found and fixed during QA. Each guards a slug fix that the
    widened ``id: number | string`` type had hidden from the compiler.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email="qa@example.com", username="qa", password="testpass123"
        )
        self.organization = Organization.objects.create(name="QA Org")
        self.project = Project.objects.create(
            name="QA Project", organization=self.organization
        )
        ProjectMember.objects.create(
            user=self.user, project=self.project, role="Team Leader", is_active=True
        )
        self.user.active_project = self.project
        self.user.save(update_fields=["active_project"])
        self.client.force_authenticate(user=self.user)

    def test_task_list_accepts_project_slug_query_param(self):
        # resolve_project_pk: ?project_id=<slug> must resolve. Member pickers and the
        # Jira-style task view passed the project slug here and used to 404/400.
        Task.objects.create(
            summary="QA Query Task", type="asset", project=self.project, owner=self.user
        )
        response = self.client.get(f"/api/tasks/?project_id={self.project.slug}")
        self.assertEqual(response.status_code, 200)

    def test_task_list_still_tolerates_numeric_project_id(self):
        # Numeric tolerance is kept as an internal safety net (query params are not migrated).
        response = self.client.get(f"/api/tasks/?project_id={self.project.id}")
        self.assertEqual(response.status_code, 200)

    def test_campaign_subresource_resolves_by_slug(self):
        # Campaign sub-resources used a UUID route converter and fed the slug straight
        # into a UUID lookup, throwing 404/500. They now resolve via resolve_lookup_kwargs.
        from datetime import date
        from campaign.models import Campaign

        campaign = Campaign.objects.create(
            name="QA Slug Campaign",
            objective=Campaign.Objective.AWARENESS,
            platforms=["META"],
            start_date=date(2026, 1, 1),
            project=self.project,
            owner=self.user,
        )
        response = self.client.get(f"/api/campaigns/{campaign.slug}/check-ins/")
        self.assertEqual(response.status_code, 200)

    def test_notion_draft_list_returns_slug(self):
        # The Notion list serialiser omitted slug, so the UI navigated to /notion/<id>.
        from notion_editor.models import Draft

        Draft.objects.create(title="QA Notion Draft", user=self.user)
        response = self.client.get("/api/notion/api/drafts/")
        self.assertEqual(response.status_code, 200)
        items = response.data.get("results", response.data)
        self.assertTrue(items)
        self.assertTrue(all("slug" in item for item in items))

    def test_mailchimp_save_provisions_missing_settings(self):
        # Legacy drafts without CampaignSettings failed save with "Campaign settings not
        # found"; the save now provisions the settings on first edit.
        from mailchimp.models import Campaign as MailchimpCampaign, CampaignSettings

        campaign = MailchimpCampaign.objects.create(user=self.user)
        self.assertFalse(CampaignSettings.objects.filter(campaign=campaign).exists())
        response = self.client.patch(
            f"/api/mailchimp/email-drafts/{campaign.slug}/template-content/",
            {"template_data": {"sections": {}}},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(CampaignSettings.objects.filter(campaign=campaign).exists())

    def test_project_set_active_by_slug(self):
        # Project switch (Select-Project) posts the slug to set-active; this used to 404
        # because callers passed the slug into a numeric path. Resolves via slug now,
        # and the response payload carries slug (ProjectSummarySerializer).
        response = self.client.post(f"/api/core/projects/{self.project.slug}/set_active/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["active_project"]["slug"], self.project.slug)
        self.user.refresh_from_db()
        self.assertEqual(self.user.active_project_id, self.project.id)


class SlugBackfillTest(APITestCase):
    """core/slug_backfill.py mirrors the mixin rules for migration RunPython."""

    def setUp(self):
        self.user = User.objects.create_user(
            email="backfill@example.com", username="backfill", password="testpass123"
        )
        self.organization = Organization.objects.create(name="Backfill Org")
        self.project = Project.objects.create(
            name="Backfill Project", organization=self.organization
        )

    def test_backfill_populates_null_slugs(self):
        tasks = []
        for summary in ["Alpha Launch", "Alpha Launch", "2030", "中文"]:
            task = Task.objects.create(
                summary=summary, type="asset", project=self.project, owner=self.user
            )
            tasks.append(task)
        for idx, task in enumerate(tasks):
            Task.objects.filter(pk=task.pk).update(slug=f"temp-empty-{idx}")

        backfill_slugs(Task, source_field="summary")

        slugs = list(
            Task.objects.filter(pk__in=[t.pk for t in tasks])
            .order_by("pk")
            .values_list("slug", flat=True)
        )
        self.assertEqual(slugs[0], "alpha-launch")
        self.assertEqual(slugs[1], "alpha-launch-1")
        self.assertEqual(slugs[2], "task-2030")
        self.assertRegex(slugs[3], r"^task-[0-9a-f]{8}$")
        self.assertFalse(Task.objects.filter(slug__isnull=True).exists())

    def test_backfill_management_command(self):
        # Verify management command runs without errors
        meeting_type = MeetingTypeDefinition.objects.create(
            project=self.project,
            slug="planning",
            label="Planning",
        )
        meeting = Meeting.objects.create(
            title="Meeting to Backfill",
            project=self.project,
            type_definition=meeting_type,
        )
        Meeting.objects.filter(pk=meeting.pk).update(slug="temp-empty-0")
        
        # Run command
        call_command("backfill_slugs")
        
        meeting.refresh_from_db()
        self.assertIsNotNone(meeting.slug)
        self.assertEqual(meeting.slug, "meeting-to-backfill")


class SlugOnlyApiLookupExtraModulesTest(APITestCase):
    """Defence-in-depth for the remaining slug-mixin modules.

    SlugOnlyApiLookupTest above covers task/project/meeting/decision/spreadsheet/
    workflow. These modules share the same SlugLookupViewSetMixin, so the contract
    is already proven centrally; this class asserts it end-to-end per module so a
    future change to any one detail route can't silently drop slug resolution.

    Pattern: GET by the mixin-generated slug must 200; GET by a bare numeric
    segment (treated as a slug, never matched) must 404 — regardless of whether
    the model's pk is numeric or a UUID.
    """

    def setUp(self):
        import datetime
        from miro.models import Board
        from klaviyo.models import EmailDraft as KlaviyoDraft
        from ad_copy_variation.models import AdCopyVariation
        from campaign.models import Campaign as MarketingCampaign, CampaignTemplate
        from mailchimp.models import Campaign as MailchimpCampaign, CampaignSettings
        from notion_editor.models import Draft as NotionDraft
        from agent.models import AgentWorkflowDefinition, AgentWorkflowTemplate

        self.user = User.objects.create_user(
            email="extra@example.com", username="extra", password="testpass123"
        )
        self.organization = Organization.objects.create(name="Extra Org")
        self.project = Project.objects.create(
            name="Extra Project", organization=self.organization
        )
        ProjectMember.objects.create(
            user=self.user, project=self.project, role="Team Leader", is_active=True
        )
        self.user.active_project = self.project
        self.user.save(update_fields=["active_project"])
        self.client.force_authenticate(user=self.user)

        self.board = Board.objects.create(
            project=self.project,
            title="Slug Lookup Board",
            share_token="tok-board-001",
        )
        self.klaviyo_draft = KlaviyoDraft.objects.create(
            user=self.user,
            name="Slug Lookup Klaviyo",
            subject="Subject Line",
        )
        self.variation = AdCopyVariation.objects.create(
            project=self.project,
            source_mode="custom",
            headline="Slug Lookup Variation",
            created_by=self.user,
        )
        self.campaign = MarketingCampaign.objects.create(
            project=self.project,
            name="Slug Lookup Campaign",
            objective=MarketingCampaign.Objective.AWARENESS,
            platforms=[MarketingCampaign.Platform.META],
            start_date=datetime.date(2026, 1, 1),
            owner=self.user,
        )
        self.mc_campaign = MailchimpCampaign.objects.create(user=self.user)
        CampaignSettings.objects.create(
            campaign=self.mc_campaign, subject_line="Slug Lookup Mailchimp"
        )
        self.mc_campaign.refresh_from_db()  # post_save signal regenerates the slug
        self.notion_draft = NotionDraft.objects.create(
            user=self.user, title="Slug Lookup Note"
        )
        self.campaign_template = CampaignTemplate.objects.create(
            creator=self.user,
            name="Slug Lookup Template",
            sharing_scope="PERSONAL",
        )
        self.agent_workflow = AgentWorkflowDefinition.objects.create(
            name="Slug Lookup Workflow",
            project=self.project,
            created_by=self.user,
        )
        self.agent_template = AgentWorkflowTemplate.objects.create(
            name="Slug Lookup Agent Template",
            created_by=self.user,
        )

        # Admin/CSM internal resources (experience_group, csm).
        from experience_group.models import ExperienceGroup
        from customer.models import CustomerOrganisation
        from csm.models import CustomerUser, Queue, TicketForm

        self.experience_group = ExperienceGroup.objects.create(
            project=self.project, name="Slug Lookup Experience Group", created_by=self.user,
        )
        self.ticket_form = TicketForm.objects.create(
            project=self.project, name="Slug Lookup Ticket Form", created_by=self.user,
        )
        self.customer_org = CustomerOrganisation.objects.create(name="Slug Lookup Cust Org")
        CustomerUser.objects.create(
            user=self.user, organisation=self.customer_org,
            user_type="admin", is_active=True,
        )
        self.queue = Queue.objects.create(
            organisation=self.customer_org, name="Slug Lookup Queue",
        )

    # miro --------------------------------------------------------------
    def test_miro_board_slug_url_resolves(self):
        response = self.client.get(f"/api/miro/boards/{self.board.slug}/")
        self.assertEqual(response.status_code, 200)

    def test_miro_board_numeric_url_is_rejected(self):
        response = self.client.get("/api/miro/boards/999999/")
        self.assertEqual(response.status_code, 404)

    # klaviyo -----------------------------------------------------------
    def test_klaviyo_draft_slug_url_resolves(self):
        response = self.client.get(f"/api/klaviyo/klaviyo-drafts/{self.klaviyo_draft.slug}/")
        self.assertEqual(response.status_code, 200)

    def test_klaviyo_draft_numeric_url_is_rejected(self):
        response = self.client.get("/api/klaviyo/klaviyo-drafts/999999/")
        self.assertEqual(response.status_code, 404)

    # ad_copy_variation -------------------------------------------------
    def test_variation_slug_url_resolves(self):
        response = self.client.get(f"/api/ad_copy_variation/variations/{self.variation.slug}/")
        self.assertEqual(response.status_code, 200)

    def test_variation_numeric_url_is_rejected(self):
        response = self.client.get("/api/ad_copy_variation/variations/999999/")
        self.assertEqual(response.status_code, 404)

    # campaign ----------------------------------------------------------
    def test_campaign_slug_url_resolves(self):
        response = self.client.get(f"/api/campaigns/{self.campaign.slug}/")
        self.assertEqual(response.status_code, 200)

    def test_campaign_numeric_url_is_rejected(self):
        response = self.client.get("/api/campaigns/999999/")
        self.assertEqual(response.status_code, 404)

    # mailchimp ---------------------------------------------------------
    def test_mailchimp_draft_slug_url_resolves(self):
        response = self.client.get(f"/api/mailchimp/email-drafts/{self.mc_campaign.slug}/")
        self.assertEqual(response.status_code, 200)

    def test_mailchimp_draft_numeric_url_is_rejected(self):
        response = self.client.get("/api/mailchimp/email-drafts/999999/")
        self.assertEqual(response.status_code, 404)

    # notion_editor -----------------------------------------------------
    def test_notion_draft_slug_url_resolves(self):
        response = self.client.get(f"/api/notion/api/drafts/{self.notion_draft.slug}/")
        self.assertEqual(response.status_code, 200)

    def test_notion_draft_numeric_url_is_rejected(self):
        response = self.client.get("/api/notion/api/drafts/999999/")
        self.assertEqual(response.status_code, 404)

    # campaign template -------------------------------------------------
    def test_campaign_template_slug_url_resolves(self):
        response = self.client.get(f"/api/campaign-templates/{self.campaign_template.slug}/")
        self.assertEqual(response.status_code, 200)

    def test_campaign_template_numeric_url_is_rejected(self):
        response = self.client.get("/api/campaign-templates/999999/")
        self.assertEqual(response.status_code, 404)

    # agent workflow definition -----------------------------------------
    def test_agent_workflow_slug_url_resolves(self):
        response = self.client.get(f"/api/agent/workflows/{self.agent_workflow.slug}/")
        self.assertEqual(response.status_code, 200)

    def test_agent_workflow_numeric_url_is_rejected(self):
        response = self.client.get("/api/agent/workflows/999999/")
        self.assertEqual(response.status_code, 404)

    # agent workflow template -------------------------------------------
    def test_agent_template_slug_url_resolves(self):
        response = self.client.get(f"/api/agent/templates/{self.agent_template.slug}/")
        self.assertEqual(response.status_code, 200)

    def test_agent_template_numeric_url_is_rejected(self):
        response = self.client.get("/api/agent/templates/999999/")
        self.assertEqual(response.status_code, 404)

    # experience_group (admin) ------------------------------------------
    def test_experience_group_slug_url_resolves(self):
        response = self.client.get(f"/api/experience-groups/{self.experience_group.slug}/")
        self.assertEqual(response.status_code, 200)

    def test_experience_group_numeric_url_is_rejected(self):
        response = self.client.get("/api/experience-groups/999999/")
        self.assertEqual(response.status_code, 404)

    def test_experience_group_list_accepts_project_slug(self):
        # Admin list `?project=` resolves a project slug
        # (not just a numeric pk). Before the fix this returned HTTP 400.
        response = self.client.get(
            f"/api/experience-groups/?project={self.project.slug}"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        rows = data["results"] if isinstance(data, dict) and "results" in data else data
        returned_slugs = [row["slug"] for row in rows]
        self.assertIn(self.experience_group.slug, returned_slugs)

    # csm ticket form (admin) -------------------------------------------
    def test_ticket_form_slug_url_resolves(self):
        response = self.client.get(f"/api/csm/ticket-forms/{self.ticket_form.slug}/")
        self.assertEqual(response.status_code, 200)

    def test_ticket_form_numeric_url_is_rejected(self):
        response = self.client.get("/api/csm/ticket-forms/999999/")
        self.assertEqual(response.status_code, 404)

    # csm queue ---------------------------------------------------------
    def test_queue_slug_url_resolves(self):
        response = self.client.get(f"/api/csm/queues/{self.queue.slug}/")
        self.assertEqual(response.status_code, 200)

    def test_queue_numeric_url_is_rejected(self):
        response = self.client.get("/api/csm/queues/999999/")
        self.assertEqual(response.status_code, 404)
