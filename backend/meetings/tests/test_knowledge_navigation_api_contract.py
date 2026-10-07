"""
Four API-level contracts for meeting ↔ decision ↔ task knowledge navigation.

These assert the wire format the frontend relies on (no serializer-only shortcuts).
"""

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Organization, Project, ProjectMember, CustomUser
from decision.models import Decision
from meetings.models import (
    ArtifactLink,
    Meeting,
    MeetingDecisionOrigin,
    MeetingTaskOrigin,
    MeetingTypeDefinition,
)
from task.models import Task
from meetings.tests.decision_origin_helpers import create_meeting_decision_origin


class TestKnowledgeNavigationAPIContract(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.organization = Organization.objects.create(name="OrgNav", slug="org-nav")
        self.project = Project.objects.create(
            name="Project Nav",
            organization=self.organization,
        )
        self.user = CustomUser.objects.create_user(
            email="nav_user@example.com",
            password="password",
            username="nav_user",
        )
        ProjectMember.objects.create(
            user=self.user,
            project=self.project,
            is_active=True,
        )
        self.planning = MeetingTypeDefinition.objects.create(
            project=self.project,
            slug="planning",
            label="Planning",
        )
        self.client.force_authenticate(user=self.user)

    def test_meeting_detail_returns_generated_decisions(self):
        m = Meeting.objects.create(
            project=self.project,
            title="M",
            type_definition=self.planning,
            objective="o",
        )
        d = Decision.objects.create(
            project=self.project,
            author=self.user,
            title="Linked D",
        )
        create_meeting_decision_origin(meeting=m, decision=d)

        url = f"/api/projects/{self.project.slug}/meetings/{m.slug}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("generated_decisions", response.data)
        self.assertEqual(len(response.data["generated_decisions"]), 1)
        self.assertEqual(response.data["generated_decisions_count"], 1)
        self.assertEqual(response.data["generated_tasks_count"], 0)
        self.assertEqual(response.data["generated_decisions"][0]["id"], d.id)
        self.assertEqual(
            response.data["generated_decisions"][0]["url"],
            f"/decisions/{d.slug}?project_id={self.project.id}",
        )
        self.assertEqual(
            response.data["generated_decisions"][0]["detail_url"],
            response.data["generated_decisions"][0]["url"],
        )
        self.assertEqual(len(response.data.get("related_decisions", [])), 0)

    def test_meeting_detail_excludes_soft_deleted_generated_decision(self):
        m = Meeting.objects.create(
            project=self.project,
            title="M",
            type_definition=self.planning,
            objective="o",
        )
        d = Decision.objects.create(
            project=self.project,
            author=self.user,
            title="Will delete",
        )
        create_meeting_decision_origin(meeting=m, decision=d)
        d.is_deleted = True
        d.save(update_fields=["is_deleted", "updated_at"])

        url = f"/api/projects/{self.project.slug}/meetings/{m.slug}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["generated_decisions"], [])
        self.assertEqual(response.data["generated_decisions_count"], 0)

    def test_origin_decision_not_duplicated_in_related_when_also_artifact(self):
        m = Meeting.objects.create(
            project=self.project,
            title="Both",
            type_definition=self.planning,
            objective="o",
        )
        d = Decision.objects.create(
            project=self.project,
            author=self.user,
            title="Same D",
        )
        create_meeting_decision_origin(meeting=m, decision=d)
        ArtifactLink.objects.create(
            meeting=m,
            artifact_type="decision",
            artifact_id=d.id,
        )
        url = f"/api/projects/{self.project.slug}/meetings/{m.slug}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["generated_decisions"]), 1)
        self.assertEqual(len(response.data["related_decisions"]), 0)

    def test_meeting_detail_related_decisions_from_artifacts_only(self):
        """Workspace Artifacts → decision: appears under related_decisions, not generated."""
        m = Meeting.objects.create(
            project=self.project,
            title="M artifact",
            type_definition=self.planning,
            objective="o",
        )
        d = Decision.objects.create(
            project=self.project,
            author=self.user,
            title="Artifact-linked D",
        )
        ArtifactLink.objects.create(
            meeting=m,
            artifact_type="decision",
            artifact_id=d.id,
        )

        url = f"/api/projects/{self.project.slug}/meetings/{m.slug}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["generated_decisions"]), 0)
        self.assertEqual(len(response.data["related_decisions"]), 1)
        self.assertEqual(response.data["related_decisions"][0]["id"], d.id)
        self.assertEqual(response.data["related_decisions"][0]["title"], "Artifact-linked D")
        self.assertEqual(
            response.data["related_decisions"][0]["url"],
            f"/decisions/{d.slug}?project_id={self.project.id}",
        )

    def test_meeting_detail_returns_generated_tasks(self):
        m = Meeting.objects.create(
            project=self.project,
            title="M2",
            type_definition=self.planning,
            objective="o",
        )
        t = Task.objects.create(
            summary="Task title",
            project=self.project,
            type="report",
            owner=self.user,
        )
        MeetingTaskOrigin.objects.create(meeting=m, task=t)

        url = f"/api/projects/{self.project.slug}/meetings/{m.slug}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("generated_tasks", response.data)
        self.assertEqual(len(response.data["generated_tasks"]), 1)
        self.assertEqual(response.data["generated_tasks"][0]["id"], t.id)
        self.assertEqual(response.data["generated_tasks"][0]["title"], "Task title")
        self.assertEqual(
            response.data["generated_tasks"][0]["url"],
            f"/tasks/{t.slug}",
        )

    def test_meeting_detail_related_tasks_from_artifacts_only(self):
        m = Meeting.objects.create(
            project=self.project,
            title="M task artifact",
            type_definition=self.planning,
            objective="o",
        )
        t = Task.objects.create(
            summary="Artifact task",
            project=self.project,
            type="report",
            owner=self.user,
        )
        ArtifactLink.objects.create(
            meeting=m,
            artifact_type="task",
            artifact_id=t.id,
        )

        url = f"/api/projects/{self.project.slug}/meetings/{m.slug}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["generated_tasks"]), 0)
        self.assertEqual(len(response.data["related_tasks"]), 1)
        self.assertEqual(response.data["related_tasks"][0]["id"], t.id)
        self.assertEqual(response.data["related_tasks"][0]["title"], "Artifact task")
        self.assertEqual(response.data["related_tasks"][0]["url"], f"/tasks/{t.slug}")

    def test_task_detail_returns_origin_meeting(self):
        m = Meeting.objects.create(
            project=self.project,
            title="Task origin meeting",
            type_definition=self.planning,
            objective="o",
        )
        t = Task.objects.create(
            summary="Task with meeting",
            project=self.project,
            type="report",
            owner=self.user,
        )
        MeetingTaskOrigin.objects.create(meeting=m, task=t)

        url = f"/api/tasks/{t.slug}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("origin_meeting", response.data)
        om = response.data["origin_meeting"]
        self.assertIsNotNone(om)
        self.assertEqual(om["id"], m.id)
        self.assertEqual(om["title"], "Task origin meeting")
        self.assertEqual(
            om["url"],
            f"/projects/{self.project.id}/meetings/{m.slug}",
        )
        self.assertEqual(om["detail_url"], om["url"])
        self.assertEqual(om["project_id"], self.project.id)
        self.assertEqual(om["type"], self.planning.slug)
