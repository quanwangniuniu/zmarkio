from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status
from datetime import timedelta

from core.models import Project, Organization
from task.models import Task, TaskRelation
from campaign.models import Campaign
from decision.models import Decision
from budget_approval.models import AdChannel, BudgetRequest, BudgetRequestStatus, BudgetPool
from meetings.models import Meeting, MeetingTypeDefinition
from spreadsheet.models import Spreadsheet
from dashboard import services

User = get_user_model()


class ProjectWorkspaceDashboardTest(TestCase):
    """
    Tests for SMP-472: Project Workspace Dashboard
    Verifies that the endpoint returns correct Decision / Task / Spreadsheet
    summaries scoped strictly to the requested project.
    """

    def setUp(self):
        # Create org
        self.org = Organization.objects.create(name="TestOrg")

        # Create users
        self.user, created = User.objects.get_or_create(
            username="testuser",
            defaults={"email": "test@test.com"},
        )
        if created:
            self.user.set_password("pass")
            self.user.save()
        # Provide names so workspace avatar initials are stable and human-friendly.
        self.user.first_name = "Test"
        self.user.last_name = "User"
        self.user.save(update_fields=["first_name", "last_name"])

        # Create two projects — to verify no cross-project data leakage
        self.project = Project.objects.create(
            name="Project Alpha", organization=self.org, owner=self.user
        )
        self.other_project = Project.objects.create(
            name="Project Beta", organization=self.org, owner=self.user
        )

        # Purge any committed data left over from a previous run's TransactionTestCase
        # tests.  When --keepdb is used across runs, sequence resets (RESTART IDENTITY)
        # in those tests mean our freshly created project pks can collide with project
        # pks from committed historical rows, causing the view to return stale task /
        # decision / spreadsheet rows alongside (or instead of) the rows we just made.
        Task.objects.filter(project__in=[self.project, self.other_project]).delete()
        Decision.objects.filter(project__in=[self.project, self.other_project]).delete()
        Spreadsheet.objects.filter(project__in=[self.project, self.other_project]).delete()

        # Ensure the test user has no organization so TenantSchemaMiddleware
        # keeps search_path on the public schema for all view queries.
        # The middleware checks current_organization_id first, then organization_id.
        self.user.organization = None
        self.user.current_organization = None
        self.user.save(update_fields=["organization", "current_organization"])

        # Auth client
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        # Use numeric pk so resolve_project_pk returns the int directly
        # (no slug DB lookup that would be sensitive to search_path changes).
        self.url = f"/api/dashboard/workspace/?project_id={self.project.pk}"

    # ── helpers ────────────────────────────────────────────────────────────

    def _make_task(self, project, status=Task.Status.SUBMITTED, due_date=None):
        # FSMField with protected=True cannot be set directly via objects.create().
        # Create with default DRAFT, then bypass the FSM via QuerySet.update().
        task = Task.objects.create(
            summary="Test Task",
            project=project,
            owner=self.user,
            type="execution",
            due_date=due_date,
        )
        if status != Task.Status.DRAFT:
            Task.objects.filter(pk=task.pk).update(status=status)
        return task

    def _make_decision(self, project, dec_status=Decision.Status.COMMITTED):
        return Decision.objects.create(
            title="Test Decision",
            project=project,
            author=self.user,
            status=dec_status,
        )

    def _make_spreadsheet(self, project, name="Sheet A"):
        return Spreadsheet.objects.create(name=name, project=project)

    # ── test cases ─────────────────────────────────────────────────────────

    def test_returns_200_for_authenticated_user(self):
        """Endpoint must be accessible by authenticated users."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_requires_authentication(self):
        """Unauthenticated requests must be rejected."""
        unauth_client = APIClient()
        response = unauth_client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_requires_project_id(self):
        """Requests without project_id must return 400."""
        response = self.client.get("/api/dashboard/workspace/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_project_id_returns_400(self):
        """Non-integer project_id must return 400."""
        response = self.client.get("/api/dashboard/workspace/?project_id=abc")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_response_has_three_zones(self):
        """Response must contain decisions, tasks, and spreadsheets keys."""
        response = self.client.get(self.url)
        data = response.json()
        self.assertIn("decisions", data)
        self.assertIn("tasks", data)
        self.assertIn("spreadsheets", data)

    def test_decisions_zone_returns_correct_project_decisions(self):
        """Only decisions from the requested project should appear."""
        self._make_decision(self.project)
        self._make_decision(self.other_project)  # must NOT appear

        response = self.client.get(self.url)
        decisions = response.json()["decisions"]
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0]["title"], "Test Decision")

    def test_tasks_zone_returns_correct_project_tasks(self):
        """Only tasks from the requested project should appear."""
        self._make_task(self.project)
        self._make_task(self.other_project)  # must NOT appear

        response = self.client.get(self.url)
        tasks = response.json()["tasks"]
        self.assertEqual(len(tasks), 1)

    def test_spreadsheets_zone_returns_correct_project_spreadsheets(self):
        """Only spreadsheets from the requested project should appear."""
        self._make_spreadsheet(self.project, name="Alpha Sheet")
        self._make_spreadsheet(self.other_project, name="Beta Sheet")  # must NOT appear

        response = self.client.get(self.url)
        sheets = response.json()["spreadsheets"]
        self.assertEqual(len(sheets), 1)
        self.assertEqual(sheets[0]["name"], "Alpha Sheet")

    def test_no_cross_project_data_leakage(self):
        """All three zones must be empty when other project has data but this one does not."""
        self._make_task(self.other_project)
        self._make_decision(self.other_project)
        self._make_spreadsheet(self.other_project)

        response = self.client.get(self.url)
        data = response.json()
        self.assertEqual(len(data["decisions"]), 0)
        self.assertEqual(len(data["tasks"]), 0)
        self.assertEqual(len(data["spreadsheets"]), 0)

    def test_overdue_tasks_are_included(self):
        """Tasks with past due_date and non-terminal status should appear."""
        yesterday = timezone.now().date() - timedelta(days=1)
        self._make_task(self.project, status=Task.Status.SUBMITTED, due_date=yesterday)

        response = self.client.get(self.url)
        tasks = response.json()["tasks"]
        self.assertEqual(len(tasks), 1)

    def test_decisions_limited_to_zone_cap(self):
        """Decisions list should be capped to the workspace zone limit."""
        for i in range(7):
            self._make_decision(self.project)

        response = self.client.get(self.url)
        self.assertLessEqual(len(response.json()["decisions"]), 20)

    def test_tasks_limited_to_zone_cap(self):
        """Tasks list should be capped to the workspace zone limit."""
        for i in range(7):
            self._make_task(self.project)

        response = self.client.get(self.url)
        self.assertLessEqual(len(response.json()["tasks"]), 20)

    def test_spreadsheets_limited_to_zone_cap(self):
        """Spreadsheets list should be capped to the workspace zone limit."""
        for i in range(7):
            self._make_spreadsheet(self.project, name=f"Sheet {i}")

        response = self.client.get(self.url)
        self.assertLessEqual(len(response.json()["spreadsheets"]), 20)

    def test_decision_fields_present(self):
        """Each decision item must have required fields for frontend navigation."""
        self._make_decision(self.project)
        response = self.client.get(self.url)
        decision = response.json()["decisions"][0]
        self.assertIn("id", decision)
        self.assertIn("title", decision)
        self.assertIn("status", decision)

    def test_task_fields_present(self):
        """Each task item must have required fields for frontend navigation."""
        self._make_task(self.project)
        response = self.client.get(self.url)
        task = response.json()["tasks"][0]
        self.assertIn("id", task)
        self.assertIn("summary", task)
        self.assertIn("status", task)
        self.assertIn("priority", task)
        self.assertIn("is_overdue", task)
        self.assertIn("owner_initials", task)

    def test_task_owner_initials_uses_owner_name(self):
        """Task rows should include owner_initials derived from owner name."""
        task = self._make_task(self.project)
        response = self.client.get(self.url)
        tasks = response.json()["tasks"]
        row = next((t for t in tasks if t["id"] == task.id), None)
        self.assertIsNotNone(row)
        self.assertEqual(row["owner_initials"], "TU")

    def test_spreadsheet_fields_present(self):
        """Each spreadsheet item must have required fields for frontend navigation."""
        self._make_spreadsheet(self.project)
        response = self.client.get(self.url)
        sheet = response.json()["spreadsheets"][0]
        self.assertIn("id", sheet)
        self.assertIn("name", sheet)
        self.assertIn("updated_at", sheet)

    def test_soft_deleted_decisions_not_shown(self):
        """Soft-deleted decisions (is_deleted=True) must not appear in dashboard."""
        decision = self._make_decision(self.project)
        decision.is_deleted = True
        decision.save()

        response = self.client.get(self.url)
        self.assertEqual(len(response.json()["decisions"]), 0)


    def _make_task_relation(self, source_task, target_task, relationship_type):
        """Helper: create a TaskRelation between two tasks"""
        from task.models import TaskRelation
        return TaskRelation.objects.create(
            source_task=source_task,
            target_task=target_task,
            relationship_type=relationship_type
        )

    def _make_pattern_job(self, spreadsheet, status='running'):
        """Helper: create a PatternJob for a spreadsheet"""
        from spreadsheet.models import PatternJob, WorkflowPattern, Sheet
        pattern = WorkflowPattern.objects.create(
            owner=self.user,
            name="Test Pattern"
        )
        sheet = Sheet.objects.create(
            spreadsheet=spreadsheet,
            name="Sheet1",
            position=0
        )
        return PatternJob.objects.create(
            pattern=pattern,
            spreadsheet=spreadsheet,
            sheet=sheet,
            status=status,
            created_by=self.user
        )

    def test_blocked_tasks_are_included(self):
        """Tasks blocked by another task must appear in dashboard."""
        from task.models import TaskRelation
        blocker = self._make_task(self.project, status=Task.Status.SUBMITTED)
        blocked = self._make_task(self.project, status=Task.Status.SUBMITTED)
        self._make_task_relation(blocker, blocked, TaskRelation.BLOCKS)

        response = self.client.get(self.url)
        task_ids = [t['id'] for t in response.json()['tasks']]
        self.assertIn(blocked.id, task_ids)

    def test_task_priority_queue_orders_overdue_then_blocked_then_decision_linked(self):
        """Task queue should prioritize overdue, then blocked, then decision-linked tasks."""
        from task.models import TaskRelation

        today = timezone.now().date()
        overdue_task = self._make_task(
            self.project,
            status=Task.Status.SUBMITTED,
            due_date=today - timedelta(days=1),
        )
        blocked_task = self._make_task(self.project, status=Task.Status.SUBMITTED, due_date=today + timedelta(days=2))
        blocker = self._make_task(self.project, status=Task.Status.SUBMITTED, due_date=today + timedelta(days=3))
        self._make_task_relation(blocker, blocked_task, TaskRelation.BLOCKS)
        decision = self._make_decision(self.project)
        decision_linked_task = self._make_decision_linked_task(
            self.project,
            decision,
            status=Task.Status.SUBMITTED,
        )
        neutral_task = self._make_task(self.project, status=Task.Status.SUBMITTED, due_date=today + timedelta(days=5))

        response = self.client.get(self.url)
        ordered_ids = [t["id"] for t in response.json()["tasks"]]
        self.assertEqual(
            ordered_ids[:4],
            [overdue_task.id, blocked_task.id, decision_linked_task.id, neutral_task.id],
        )

    def test_blocked_task_has_is_blocked_flag(self):
        """Blocked tasks must have is_blocked=True in response."""
        from task.models import TaskRelation
        blocker = self._make_task(self.project, status=Task.Status.SUBMITTED)
        blocked = self._make_task(self.project, status=Task.Status.SUBMITTED)
        self._make_task_relation(blocker, blocked, TaskRelation.BLOCKS)

        response = self.client.get(self.url)
        tasks = response.json()['tasks']
        blocked_task = next((t for t in tasks if t['id'] == blocked.id), None)
        self.assertIsNotNone(blocked_task)
        self.assertTrue(blocked_task['is_blocked'])

    def test_non_blocked_task_has_is_blocked_false(self):
        """Non-blocked tasks must have is_blocked=False."""
        task = self._make_task(self.project, status=Task.Status.SUBMITTED)
        response = self.client.get(self.url)
        tasks = response.json()['tasks']
        task_data = next((t for t in tasks if t['id'] == task.id), None)
        self.assertFalse(task_data['is_blocked'])

    def test_spreadsheet_has_running_job_flag(self):
        """Spreadsheets with running PatternJob must have has_running_job=True."""
        sheet = self._make_spreadsheet(self.project, name="Active Sheet")
        self._make_pattern_job(sheet, status='running')

        response = self.client.get(self.url)
        sheets = response.json()['spreadsheets']
        sheet_data = next((s for s in sheets if s['id'] == sheet.id), None)
        self.assertIsNotNone(sheet_data)
        self.assertTrue(sheet_data['has_running_job'])

    def test_spreadsheet_without_running_job_has_flag_false(self):
        """Spreadsheets without running PatternJob must have has_running_job=False."""
        sheet = self._make_spreadsheet(self.project)
        response = self.client.get(self.url)
        sheets = response.json()['spreadsheets']
        sheet_data = next((s for s in sheets if s['id'] == sheet.id), None)
        self.assertFalse(sheet_data['has_running_job'])

    def test_spreadsheet_priority_queue_shows_running_jobs_first(self):
        """Operations spreadsheet queue should prioritize running jobs over recency."""
        older_running = self._make_spreadsheet(self.project, name="Running Sheet")
        self._make_pattern_job(older_running, status='running')
        newer_idle = self._make_spreadsheet(self.project, name="Idle Sheet")

        # Force recency difference: idle is newer, but running sheet should still come first.
        Spreadsheet.objects.filter(id=older_running.id).update(updated_at=timezone.now() - timedelta(days=2))
        Spreadsheet.objects.filter(id=newer_idle.id).update(updated_at=timezone.now())

        response = self.client.get(self.url)
        sheets = response.json()['spreadsheets']
        sheet_ids = [s['id'] for s in sheets]
        self.assertEqual(sheet_ids[:2], [older_running.id, newer_idle.id])

    def _make_decision_linked_task(self, project, decision, status=Task.Status.SUBMITTED):
        """Helper: create a task linked to a decision via content_type + object_id."""
        from django.contrib.contenttypes.models import ContentType
        from decision.models import Decision as DecisionModel
        task = Task.objects.create(
            summary="Decision-linked Task",
            project=project,
            owner=self.user,
            type="execution",
            content_type=ContentType.objects.get_for_model(DecisionModel),
            object_id=str(decision.id),
        )
        if status != Task.Status.DRAFT:
            Task.objects.filter(pk=task.pk).update(status=status)
        return task

    def test_decision_linked_tasks_shown_first(self):
        """Tasks linked to a decision must appear in the dashboard task zone."""
        decision = self._make_decision(self.project)
        linked_task = self._make_decision_linked_task(self.project, decision)
        unlinked_task = self._make_task(self.project)

        response = self.client.get(self.url)
        task_ids = [t['id'] for t in response.json()['tasks']]
        self.assertIn(linked_task.id, task_ids)
        self.assertIn(unlinked_task.id, task_ids)

    def test_decision_linked_task_has_flag(self):
        """Tasks linked to a decision must have is_decision_linked=True."""
        decision = self._make_decision(self.project)
        linked_task = self._make_decision_linked_task(self.project, decision)

        response = self.client.get(self.url)
        tasks = response.json()['tasks']
        task_data = next((t for t in tasks if t['id'] == linked_task.id), None)
        self.assertIsNotNone(task_data)
        self.assertTrue(task_data['is_decision_linked'])

    def test_unlinked_task_has_no_decision_flag(self):
        """Tasks not linked to a decision must have is_decision_linked=False."""
        task = self._make_task(self.project)
        response = self.client.get(self.url)
        tasks = response.json()['tasks']
        task_data = next((t for t in tasks if t['id'] == task.id), None)
        self.assertFalse(task_data['is_decision_linked'])

    def test_decisions_with_unresolved_execution_shown(self):
        """Decisions with linked tasks not yet completed must appear in dashboard."""
        decision = self._make_decision(self.project)
        self._make_decision_linked_task(
            self.project, decision, status=Task.Status.SUBMITTED
        )
        response = self.client.get(self.url)
        decision_ids = [d['id'] for d in response.json()['decisions']]
        self.assertIn(decision.id, decision_ids)

    def test_decisions_with_all_tasks_completed_not_flagged(self):
        """Decisions whose all linked tasks are completed must have has_unresolved_tasks=False."""
        decision = self._make_decision(self.project)
        self._make_decision_linked_task(
            self.project, decision, status=Task.Status.APPROVED
        )
        response = self.client.get(self.url)
        decisions = response.json()['decisions']
        decision_data = next((d for d in decisions if d['id'] == decision.id), None)
        if decision_data:
            self.assertFalse(decision_data['has_unresolved_tasks'])

    def test_decision_priority_queue_orders_awaiting_then_unresolved_then_high_risk(self):
        """Decision queue should prioritize awaiting review, unresolved execution, then high risk."""
        awaiting = self._make_decision(self.project, dec_status=Decision.Status.AWAITING_APPROVAL)
        unresolved = self._make_decision(self.project, dec_status=Decision.Status.REVIEWED)
        self._make_decision_linked_task(self.project, unresolved, status=Task.Status.SUBMITTED)
        high_risk = self._make_decision(self.project, dec_status=Decision.Status.COMMITTED)
        high_risk.risk_level = 'HIGH'
        high_risk.save(update_fields=['risk_level'])
        self._make_decision(self.project, dec_status=Decision.Status.COMMITTED)

        response = self.client.get(self.url)
        ordered_ids = [d['id'] for d in response.json()['decisions']]
        self.assertEqual(ordered_ids[:3], [awaiting.id, unresolved.id, high_risk.id])

    def _make_workflow_pattern(self, spreadsheet, name="Test Pattern"):
        """Helper: create a WorkflowPattern originating from a spreadsheet."""
        from spreadsheet.models import WorkflowPattern
        return WorkflowPattern.objects.create(
            owner=self.user,
            name=name,
            origin_spreadsheet_id=spreadsheet.id,
            is_archived=False,
        )

    def test_workflow_patterns_shown_in_response(self):
        """WorkflowPatterns from project spreadsheets must appear in dashboard."""
        sheet = self._make_spreadsheet(self.project, name="Pattern Sheet")
        self._make_workflow_pattern(sheet, name="My Pattern")

        response = self.client.get(self.url)
        self.assertIn("patterns", response.json())
        self.assertEqual(len(response.json()["patterns"]), 1)
        self.assertEqual(response.json()["patterns"][0]["name"], "My Pattern")
        self.assertEqual(response.json()["patterns"][0]["origin_spreadsheet_id"], sheet.id)

    def test_archived_patterns_not_shown(self):
        """Archived WorkflowPatterns must not appear in dashboard."""
        from spreadsheet.models import WorkflowPattern
        sheet = self._make_spreadsheet(self.project)
        WorkflowPattern.objects.create(
            owner=self.user,
            name="Archived Pattern",
            origin_spreadsheet_id=sheet.id,
            is_archived=True,
        )
        response = self.client.get(self.url)
        self.assertEqual(len(response.json()["patterns"]), 0)

    def test_patterns_from_other_project_not_shown(self):
        """Patterns from other project spreadsheets must not appear."""
        other_sheet = self._make_spreadsheet(self.other_project, name="Other Sheet")
        self._make_workflow_pattern(other_sheet, name="Other Pattern")

        response = self.client.get(self.url)
        self.assertEqual(len(response.json()["patterns"]), 0)

    def test_patterns_limited_to_zone_cap(self):
        """Patterns list should be capped to the workspace zone limit."""
        sheet = self._make_spreadsheet(self.project)
        for i in range(7):
            self._make_workflow_pattern(sheet, name=f"Pattern {i}")

        response = self.client.get(self.url)
        self.assertLessEqual(len(response.json()["patterns"]), 20)

    def test_pattern_priority_queue_shows_running_origin_first(self):
        """Patterns from spreadsheets with running jobs should appear before idle origins."""
        running_sheet = self._make_spreadsheet(self.project, name="Running Origin")
        idle_sheet = self._make_spreadsheet(self.project, name="Idle Origin")
        running_pattern = self._make_workflow_pattern(running_sheet, name="Running Pattern")
        idle_pattern = self._make_workflow_pattern(idle_sheet, name="Idle Pattern")
        self._make_pattern_job(running_sheet, status='running')

        response = self.client.get(self.url)
        pattern_ids = [p["id"] for p in response.json()["patterns"]]
        self.assertEqual(pattern_ids[:2], [str(running_pattern.id), str(idle_pattern.id)])

# =============================================================================
# RollupServicesTest — unit tests for dashboard/services.py
# =============================================================================

class RollupServicesTest(TestCase):
    """
    Unit tests for the get_rollup() and get_available_fields() service functions.
    Each field in FIELD_REGISTRY gets at least one positive and one isolation test.
    """

    # Counter used to generate unique names across tests within the same class
    _counter = 0

    def setUp(self):
        RollupServicesTest._counter = 0

        self.org = Organization.objects.create(name="RollupOrg")
        self.user = User.objects.create_user(username="rollupuser", email="r@r.com", password="pass")

        self.project = Project.objects.create(name="Main", organization=self.org, owner=self.user)
        self.other = Project.objects.create(name="Other", organization=self.org, owner=self.user)

        # Shared MeetingTypeDefinition (required non-null FK on Meeting)
        self.meeting_type = MeetingTypeDefinition.objects.create(
            project=self.project, label="General"
        )
        # Shared AdChannel (required non-null FK on BudgetPool)
        self.ad_channel = AdChannel.objects.create(name="channel", project=self.project)

        # Wipe any stale rows left from previous --keepdb runs
        for model in (Task, Decision, Campaign, Meeting, Spreadsheet):
            model.objects.filter(project__in=[self.project, self.other]).delete()
        BudgetPool.objects.filter(project__in=[self.project, self.other]).delete()

    # ── factories ─────────────────────────────────────────────────────────────

    def _task(self, project=None, status=Task.Status.DRAFT, due_date=None, days_ago=None):
        project = project or self.project
        task = Task.objects.create(
            summary="t", project=project, owner=self.user, type="execution",
            due_date=due_date,
        )
        if status != Task.Status.DRAFT:
            Task.objects.filter(pk=task.pk).update(status=status)
        if days_ago is not None:
            created = timezone.now() - timedelta(days=days_ago)
            Task.objects.filter(pk=task.pk).update(created_at=created)
        return task

    def _done_task(self, project=None, days_ago=None):
        """Create an APPROVED (done) task, optionally backdating updated_at."""
        project = project or self.project
        task = self._task(project, status=Task.Status.APPROVED)
        if days_ago is not None:
            updated = timezone.now() - timedelta(days=days_ago)
            Task.objects.filter(pk=task.pk).update(updated_at=updated)
        return task

    def _block(self, blocker, blocked):
        return TaskRelation.objects.create(
            source_task=blocker,
            target_task=blocked,
            relationship_type=TaskRelation.BLOCKS,
        )

    def _decision(self, project=None, status=Decision.Status.COMMITTED, risk_level='LOW'):
        project = project or self.project
        d = Decision.objects.create(
            title="d", project=project, author=self.user, status=status,
        )
        if risk_level != 'LOW':
            Decision.objects.filter(pk=d.pk).update(risk_level=risk_level)
        return d

    def _uid(self):
        RollupServicesTest._counter += 1
        return RollupServicesTest._counter

    def _campaign(self, project=None, status=Campaign.Status.PLANNING):
        project = project or self.project
        return Campaign.objects.create(
            name=f"campaign-{self._uid()}",
            project=project,
            owner=self.user,
            status=status,
            start_date=timezone.now().date(),
        )

    def _meeting(self, project=None, days_from_now=1):
        project = project or self.project
        # Each project needs its own MeetingTypeDefinition
        mtd, _ = MeetingTypeDefinition.objects.get_or_create(
            project=project, label="General"
        )
        return Meeting.objects.create(
            title=f"meeting-{self._uid()}",
            project=project,
            type_definition=mtd,
            scheduled_date=(timezone.now() + timedelta(days=days_from_now)).date(),
        )

    def _budget_request(self, project=None, req_status=BudgetRequestStatus.SUBMITTED):
        project = project or self.project
        channel, _ = AdChannel.objects.get_or_create(name="channel", project=project)
        pool = BudgetPool.objects.create(
            name=f"pool-{self._uid()}",
            project=project,
            ad_channel=channel,
            total_amount=1000,
            currency="USD",
        )
        return BudgetRequest.objects.create(
            budget_pool=pool,
            requested_by=self.user,
            ad_channel=channel,
            amount=100,
            status=req_status,
        )

    def _spreadsheet(self, project=None):
        project = project or self.project
        return Spreadsheet.objects.create(name=f"sheet-{self._uid()}", project=project)

    # ── get_available_fields ──────────────────────────────────────────────────

    def test_get_available_fields_returns_list_of_dicts(self):
        fields = services.get_available_fields()
        self.assertIsInstance(fields, list)
        self.assertTrue(len(fields) > 0)

    def test_get_available_fields_each_has_key_label_group(self):
        for f in services.get_available_fields():
            self.assertIn('key', f)
            self.assertIn('label', f)
            self.assertIn('group', f)

    # ── get_rollup — structure ────────────────────────────────────────────────

    def test_get_rollup_returns_results_and_errors_keys(self):
        result = services.get_rollup([self.project.pk], ['task_total'])
        self.assertIn('results', result)
        self.assertIn('errors', result)

    def test_get_rollup_empty_project_ids_returns_empty(self):
        result = services.get_rollup([], ['task_total'])
        self.assertEqual(result['results'], [])

    def test_get_rollup_unknown_field_ignored(self):
        result = services.get_rollup([self.project.pk], ['no_such_field'])
        self.assertEqual(result['results'], [])

    def test_get_rollup_result_contains_project_id_and_name(self):
        result = services.get_rollup([self.project.pk], ['task_total'])
        self.assertEqual(len(result['results']), 1)
        row = result['results'][0]
        self.assertEqual(row['project_id'], self.project.pk)
        self.assertEqual(row['project_name'], self.project.name)

    def test_get_rollup_multiple_projects_returns_one_row_each(self):
        result = services.get_rollup([self.project.pk, self.other.pk], ['task_total'])
        ids = {r['project_id'] for r in result['results']}
        self.assertEqual(ids, {self.project.pk, self.other.pk})

    # ── task_total ────────────────────────────────────────────────────────────

    def test_task_total_counts_all_tasks(self):
        self._task(); self._task()
        row = services.get_rollup([self.project.pk], ['task_total'])['results'][0]
        self.assertEqual(row['task_total'], 2)

    def test_task_total_isolated_to_project(self):
        self._task(self.other)
        row = services.get_rollup([self.project.pk], ['task_total'])['results'][0]
        self.assertEqual(row['task_total'], 0)

    # ── task_done ─────────────────────────────────────────────────────────────

    def test_task_done_counts_approved_and_locked(self):
        self._task(status=Task.Status.APPROVED)
        self._task(status=Task.Status.LOCKED)
        self._task(status=Task.Status.SUBMITTED)  # not done
        row = services.get_rollup([self.project.pk], ['task_done'])['results'][0]
        self.assertEqual(row['task_done'], 2)

    def test_task_done_isolated_to_project(self):
        self._task(self.other, status=Task.Status.APPROVED)
        row = services.get_rollup([self.project.pk], ['task_done'])['results'][0]
        self.assertEqual(row['task_done'], 0)

    # ── task_overdue ──────────────────────────────────────────────────────────

    def test_task_overdue_counts_past_due_active_tasks(self):
        yesterday = timezone.now().date() - timedelta(days=1)
        self._task(due_date=yesterday, status=Task.Status.SUBMITTED)
        row = services.get_rollup([self.project.pk], ['task_overdue'])['results'][0]
        self.assertEqual(row['task_overdue'], 1)

    def test_task_overdue_excludes_done_tasks(self):
        yesterday = timezone.now().date() - timedelta(days=1)
        self._task(due_date=yesterday, status=Task.Status.APPROVED)
        row = services.get_rollup([self.project.pk], ['task_overdue'])['results'][0]
        self.assertEqual(row['task_overdue'], 0)

    def test_task_overdue_excludes_cancelled_tasks(self):
        yesterday = timezone.now().date() - timedelta(days=1)
        self._task(due_date=yesterday, status=Task.Status.CANCELLED)
        row = services.get_rollup([self.project.pk], ['task_overdue'])['results'][0]
        self.assertEqual(row['task_overdue'], 0)

    def test_task_overdue_excludes_future_due_tasks(self):
        tomorrow = timezone.now().date() + timedelta(days=1)
        self._task(due_date=tomorrow, status=Task.Status.SUBMITTED)
        row = services.get_rollup([self.project.pk], ['task_overdue'])['results'][0]
        self.assertEqual(row['task_overdue'], 0)

    def test_task_overdue_isolated_to_project(self):
        yesterday = timezone.now().date() - timedelta(days=1)
        self._task(self.other, due_date=yesterday, status=Task.Status.SUBMITTED)
        row = services.get_rollup([self.project.pk], ['task_overdue'])['results'][0]
        self.assertEqual(row['task_overdue'], 0)

    # ── task_blocked ──────────────────────────────────────────────────────────

    def test_task_blocked_counts_tasks_with_incoming_blocks_relation(self):
        blocker = self._task(status=Task.Status.SUBMITTED)
        blocked = self._task(status=Task.Status.SUBMITTED)
        self._block(blocker, blocked)
        row = services.get_rollup([self.project.pk], ['task_blocked'])['results'][0]
        self.assertEqual(row['task_blocked'], 1)

    def test_task_blocked_excludes_done_blocked_tasks(self):
        blocker = self._task(status=Task.Status.SUBMITTED)
        blocked = self._task(status=Task.Status.APPROVED)
        self._block(blocker, blocked)
        row = services.get_rollup([self.project.pk], ['task_blocked'])['results'][0]
        self.assertEqual(row['task_blocked'], 0)

    def test_task_blocked_zero_when_no_relations(self):
        self._task(status=Task.Status.SUBMITTED)
        row = services.get_rollup([self.project.pk], ['task_blocked'])['results'][0]
        self.assertEqual(row['task_blocked'], 0)

    def test_task_blocked_isolated_to_project(self):
        blocker = self._task(self.other, status=Task.Status.SUBMITTED)
        blocked = self._task(self.other, status=Task.Status.SUBMITTED)
        self._block(blocker, blocked)
        row = services.get_rollup([self.project.pk], ['task_blocked'])['results'][0]
        self.assertEqual(row['task_blocked'], 0)

    # ── task_under_review ─────────────────────────────────────────────────────

    def test_task_under_review_counts_under_review_status(self):
        self._task(status=Task.Status.UNDER_REVIEW)
        self._task(status=Task.Status.SUBMITTED)  # different status
        row = services.get_rollup([self.project.pk], ['task_under_review'])['results'][0]
        self.assertEqual(row['task_under_review'], 1)

    def test_task_under_review_isolated_to_project(self):
        self._task(self.other, status=Task.Status.UNDER_REVIEW)
        row = services.get_rollup([self.project.pk], ['task_under_review'])['results'][0]
        self.assertEqual(row['task_under_review'], 0)

    # ── task_rejected ─────────────────────────────────────────────────────────

    def test_task_rejected_counts_rejected_status(self):
        self._task(status=Task.Status.REJECTED)
        self._task(status=Task.Status.SUBMITTED)
        row = services.get_rollup([self.project.pk], ['task_rejected'])['results'][0]
        self.assertEqual(row['task_rejected'], 1)

    def test_task_rejected_isolated_to_project(self):
        self._task(self.other, status=Task.Status.REJECTED)
        row = services.get_rollup([self.project.pk], ['task_rejected'])['results'][0]
        self.assertEqual(row['task_rejected'], 0)

    # ── task_due_soon ─────────────────────────────────────────────────────────

    def test_task_due_soon_counts_tasks_due_within_7_days(self):
        soon = timezone.now().date() + timedelta(days=3)
        self._task(due_date=soon, status=Task.Status.SUBMITTED)
        row = services.get_rollup([self.project.pk], ['task_due_soon'])['results'][0]
        self.assertEqual(row['task_due_soon'], 1)

    def test_task_due_soon_excludes_done_tasks(self):
        soon = timezone.now().date() + timedelta(days=3)
        self._task(due_date=soon, status=Task.Status.APPROVED)
        row = services.get_rollup([self.project.pk], ['task_due_soon'])['results'][0]
        self.assertEqual(row['task_due_soon'], 0)

    def test_task_due_soon_excludes_tasks_due_beyond_7_days(self):
        far = timezone.now().date() + timedelta(days=10)
        self._task(due_date=far, status=Task.Status.SUBMITTED)
        row = services.get_rollup([self.project.pk], ['task_due_soon'])['results'][0]
        self.assertEqual(row['task_due_soon'], 0)

    def test_task_due_soon_isolated_to_project(self):
        soon = timezone.now().date() + timedelta(days=1)
        self._task(self.other, due_date=soon, status=Task.Status.SUBMITTED)
        row = services.get_rollup([self.project.pk], ['task_due_soon'])['results'][0]
        self.assertEqual(row['task_due_soon'], 0)

    # ── task_completed_7d ─────────────────────────────────────────────────────

    def test_task_completed_7d_counts_recently_approved_tasks(self):
        self._done_task(days_ago=3)   # within 7d — should count
        self._done_task(days_ago=10)  # older — should not count
        row = services.get_rollup([self.project.pk], ['task_completed_7d'])['results'][0]
        self.assertEqual(row['task_completed_7d'], 1)

    def test_task_completed_7d_excludes_non_done_tasks(self):
        t = self._task(status=Task.Status.SUBMITTED)
        Task.objects.filter(pk=t.pk).update(updated_at=timezone.now() - timedelta(days=1))
        row = services.get_rollup([self.project.pk], ['task_completed_7d'])['results'][0]
        self.assertEqual(row['task_completed_7d'], 0)

    def test_task_completed_7d_isolated_to_project(self):
        self._done_task(project=self.other, days_ago=1)
        row = services.get_rollup([self.project.pk], ['task_completed_7d'])['results'][0]
        self.assertEqual(row['task_completed_7d'], 0)

    # ── task_created_7d ───────────────────────────────────────────────────────

    def test_task_created_7d_counts_recently_created_tasks(self):
        self._task(days_ago=2)   # within 7d
        self._task(days_ago=10)  # older
        row = services.get_rollup([self.project.pk], ['task_created_7d'])['results'][0]
        self.assertEqual(row['task_created_7d'], 1)

    def test_task_created_7d_isolated_to_project(self):
        self._task(self.other, days_ago=1)
        row = services.get_rollup([self.project.pk], ['task_created_7d'])['results'][0]
        self.assertEqual(row['task_created_7d'], 0)

    # ── decision_total ────────────────────────────────────────────────────────

    def test_decision_total_counts_non_deleted_decisions(self):
        self._decision()
        self._decision()
        row = services.get_rollup([self.project.pk], ['decision_total'])['results'][0]
        self.assertEqual(row['decision_total'], 2)

    def test_decision_total_excludes_soft_deleted(self):
        d = self._decision()
        Decision.objects.filter(pk=d.pk).update(is_deleted=True)
        row = services.get_rollup([self.project.pk], ['decision_total'])['results'][0]
        self.assertEqual(row['decision_total'], 0)

    def test_decision_total_isolated_to_project(self):
        self._decision(self.other)
        row = services.get_rollup([self.project.pk], ['decision_total'])['results'][0]
        self.assertEqual(row['decision_total'], 0)

    # ── decision_pending ──────────────────────────────────────────────────────

    def test_decision_pending_counts_awaiting_approval(self):
        self._decision(status=Decision.Status.AWAITING_APPROVAL)
        self._decision(status=Decision.Status.COMMITTED)  # not pending
        row = services.get_rollup([self.project.pk], ['decision_pending'])['results'][0]
        self.assertEqual(row['decision_pending'], 1)

    def test_decision_pending_isolated_to_project(self):
        self._decision(self.other, status=Decision.Status.AWAITING_APPROVAL)
        row = services.get_rollup([self.project.pk], ['decision_pending'])['results'][0]
        self.assertEqual(row['decision_pending'], 0)

    # ── decision_high_risk ────────────────────────────────────────────────────

    def test_decision_high_risk_counts_high_risk_decisions(self):
        self._decision(risk_level='HIGH')
        self._decision(risk_level='LOW')
        row = services.get_rollup([self.project.pk], ['decision_high_risk'])['results'][0]
        self.assertEqual(row['decision_high_risk'], 1)

    def test_decision_high_risk_excludes_soft_deleted(self):
        d = self._decision(risk_level='HIGH')
        Decision.objects.filter(pk=d.pk).update(is_deleted=True)
        row = services.get_rollup([self.project.pk], ['decision_high_risk'])['results'][0]
        self.assertEqual(row['decision_high_risk'], 0)

    def test_decision_high_risk_isolated_to_project(self):
        self._decision(self.other, risk_level='HIGH')
        row = services.get_rollup([self.project.pk], ['decision_high_risk'])['results'][0]
        self.assertEqual(row['decision_high_risk'], 0)

    # ── campaign_active ───────────────────────────────────────────────────────

    def test_campaign_active_counts_non_terminal_campaigns(self):
        self._campaign(status=Campaign.Status.PLANNING)
        self._campaign(status=Campaign.Status.TESTING)
        self._campaign(status=Campaign.Status.PAUSED)    # inactive
        self._campaign(status=Campaign.Status.COMPLETED) # inactive
        self._campaign(status=Campaign.Status.ARCHIVED)  # inactive
        row = services.get_rollup([self.project.pk], ['campaign_active'])['results'][0]
        self.assertEqual(row['campaign_active'], 2)

    def test_campaign_active_isolated_to_project(self):
        self._campaign(self.other, status=Campaign.Status.PLANNING)
        row = services.get_rollup([self.project.pk], ['campaign_active'])['results'][0]
        self.assertEqual(row['campaign_active'], 0)

    # ── campaign_total ────────────────────────────────────────────────────────

    def test_campaign_total_counts_all_campaigns(self):
        self._campaign(status=Campaign.Status.PLANNING)
        self._campaign(status=Campaign.Status.COMPLETED)
        row = services.get_rollup([self.project.pk], ['campaign_total'])['results'][0]
        self.assertEqual(row['campaign_total'], 2)

    def test_campaign_total_isolated_to_project(self):
        self._campaign(self.other)
        row = services.get_rollup([self.project.pk], ['campaign_total'])['results'][0]
        self.assertEqual(row['campaign_total'], 0)

    # ── budget_request_pending ────────────────────────────────────────────────

    def test_budget_request_pending_counts_submitted_and_under_review(self):
        self._budget_request(req_status=BudgetRequestStatus.SUBMITTED)
        self._budget_request(req_status=BudgetRequestStatus.UNDER_REVIEW)
        self._budget_request(req_status=BudgetRequestStatus.APPROVED)  # not pending
        row = services.get_rollup([self.project.pk], ['budget_request_pending'])['results'][0]
        self.assertEqual(row['budget_request_pending'], 2)

    def test_budget_request_pending_isolated_to_project(self):
        self._budget_request(self.other, req_status=BudgetRequestStatus.SUBMITTED)
        row = services.get_rollup([self.project.pk], ['budget_request_pending'])['results'][0]
        self.assertEqual(row['budget_request_pending'], 0)

    # ── meeting_upcoming ──────────────────────────────────────────────────────

    def test_meeting_upcoming_counts_future_non_archived_meetings(self):
        self._meeting(days_from_now=1)
        self._meeting(days_from_now=5)
        row = services.get_rollup([self.project.pk], ['meeting_upcoming'])['results'][0]
        self.assertEqual(row['meeting_upcoming'], 2)

    def test_meeting_upcoming_excludes_archived_meetings(self):
        m = self._meeting(days_from_now=1)
        Meeting.objects.filter(pk=m.pk).update(is_archived=True)
        row = services.get_rollup([self.project.pk], ['meeting_upcoming'])['results'][0]
        self.assertEqual(row['meeting_upcoming'], 0)

    def test_meeting_upcoming_excludes_past_meetings(self):
        past_date = (timezone.now() - timedelta(days=1)).date()
        Meeting.objects.create(
            title=f"past-{self._uid()}",
            project=self.project,
            type_definition=self.meeting_type,
            scheduled_date=past_date,
        )
        row = services.get_rollup([self.project.pk], ['meeting_upcoming'])['results'][0]
        self.assertEqual(row['meeting_upcoming'], 0)

    def test_meeting_upcoming_isolated_to_project(self):
        self._meeting(self.other, days_from_now=1)
        row = services.get_rollup([self.project.pk], ['meeting_upcoming'])['results'][0]
        self.assertEqual(row['meeting_upcoming'], 0)

    # ── spreadsheet_total ─────────────────────────────────────────────────────

    def test_spreadsheet_total_counts_non_deleted_spreadsheets(self):
        self._spreadsheet()
        self._spreadsheet()
        row = services.get_rollup([self.project.pk], ['spreadsheet_total'])['results'][0]
        self.assertEqual(row['spreadsheet_total'], 2)

    def test_spreadsheet_total_excludes_soft_deleted(self):
        s = self._spreadsheet()
        Spreadsheet.objects.filter(pk=s.pk).update(is_deleted=True)
        row = services.get_rollup([self.project.pk], ['spreadsheet_total'])['results'][0]
        self.assertEqual(row['spreadsheet_total'], 0)

    def test_spreadsheet_total_isolated_to_project(self):
        self._spreadsheet(self.other)
        row = services.get_rollup([self.project.pk], ['spreadsheet_total'])['results'][0]
        self.assertEqual(row['spreadsheet_total'], 0)

    # ── multi-project batch ───────────────────────────────────────────────────

    def test_rollup_returns_correct_counts_for_each_project_in_batch(self):
        """Two projects with different task counts must each get the right row."""
        self._task(); self._task()           # 2 for main
        self._task(self.other)               # 1 for other
        result = services.get_rollup(
            [self.project.pk, self.other.pk], ['task_total']
        )
        by_id = {r['project_id']: r['task_total'] for r in result['results']}
        self.assertEqual(by_id[self.project.pk], 2)
        self.assertEqual(by_id[self.other.pk], 1)

    def test_rollup_missing_project_id_excluded_from_results(self):
        """Project IDs that don't exist in the DB must not appear in results."""
        result = services.get_rollup([99999999], ['task_total'])
        self.assertEqual(result['results'], [])

    def test_rollup_zero_returned_for_project_with_no_matching_rows(self):
        """A valid project with no tasks must return 0, not be absent from results."""
        row = services.get_rollup([self.project.pk], ['task_total'])['results'][0]
        self.assertEqual(row['task_total'], 0)
