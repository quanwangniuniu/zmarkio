"""
Creating tasks with optional ``origin_meeting_id`` establishes ``MeetingTaskOrigin`` in the same
transaction as the parent row.

Decision draft origin behavior is now owned by the .NET decision service and covered by service
tests there.
"""

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Organization, Project, ProjectMember, CustomUser
from meetings.models import (
    Meeting,
    MeetingTaskOrigin,
    MeetingTypeDefinition,
)
from task.models import Task


class TestMeetingOriginOnCreate(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.organization = Organization.objects.create(name="OrgO", slug="org-o")
        self.project = Project.objects.create(
            name="Project O",
            organization=self.organization,
        )
        self.other_project = Project.objects.create(
            name="Other",
            organization=self.organization,
        )
        self.other_planning = MeetingTypeDefinition.objects.create(
            project=self.other_project,
            slug="planning",
            label="Planning",
        )
        self.user = CustomUser.objects.create_user(
            email="o_user@example.com",
            password="password",
            username="o_user",
        )
        self.approver = CustomUser.objects.create_user(
            email="o_approver@example.com",
            password="password",
            username="o_approver",
        )
        ProjectMember.objects.create(
            user=self.user,
            project=self.project,
            is_active=True,
        )
        ProjectMember.objects.create(
            user=self.approver,
            project=self.project,
            is_active=True,
        )
        self.planning = MeetingTypeDefinition.objects.create(
            project=self.project,
            slug="planning",
            label="Planning",
        )
        self.meeting = Meeting.objects.create(
            project=self.project,
            title="Standup",
            type_definition=self.planning,
            objective="sync",
            is_archived=False,
        )
        self.client.force_authenticate(user=self.user)

    def test_create_task_with_origin_meeting_id_creates_origin_row(self):
        url = "/api/tasks/"
        response = self.client.post(
            url,
            {
                "summary": "From meeting",
                "description": "d",
                "type": "asset",
                "project_id": self.project.id,
                "origin_meeting_id": self.meeting.id,
                "current_approver_id": self.approver.id,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        task_id = response.data["id"]
        self.assertTrue(
            MeetingTaskOrigin.objects.filter(
                meeting_id=self.meeting.id,
                task_id=task_id,
            ).exists()
        )
        self.assertEqual(response.data["origin_meeting"]["id"], self.meeting.id)

    def test_create_task_rejects_archived_meeting_origin(self):
        self.meeting.is_archived = True
        self.meeting.save(update_fields=["is_archived"])
        response = self.client.post(
            "/api/tasks/",
            {
                "summary": "x",
                "description": "d",
                "type": "asset",
                "project_id": self.project.id,
                "origin_meeting_id": self.meeting.id,
                "current_approver_id": self.approver.id,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("origin_meeting_id", response.data)

    def test_create_task_rejects_other_project_meeting(self):
        other_meeting = Meeting.objects.create(
            project=self.other_project,
            title="Elsewhere",
            type_definition=self.other_planning,
            objective="o",
        )
        response = self.client.post(
            "/api/tasks/",
            {
                "summary": "x",
                "description": "d",
                "type": "asset",
                "project_id": self.project.id,
                "origin_meeting_id": other_meeting.id,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_patch_task_rejects_origin_meeting_id(self):
        t = Task.objects.create(
            project=self.project,
            owner=self.user,
            summary="Existing",
            description="d",
            type="asset",
        )
        response = self.client.patch(
            f"/api/tasks/{t.slug}/",
            {"summary": "New title", "origin_meeting_id": self.meeting.id},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_task_rejects_unknown_meeting_id(self):
        response = self.client.post(
            "/api/tasks/",
            {
                "summary": "x",
                "description": "d",
                "type": "asset",
                "project_id": self.project.id,
                "origin_meeting_id": 999_999_991,
                "current_approver_id": self.approver.id,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("origin_meeting_id", response.data)

    def test_patch_task_rejects_second_origin_when_task_already_has_origin(self):
        create = self.client.post(
            "/api/tasks/",
            {
                "summary": "Has origin",
                "description": "d",
                "type": "asset",
                "project_id": self.project.id,
                "origin_meeting_id": self.meeting.id,
                "current_approver_id": self.approver.id,
            },
            format="json",
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED)
        task_slug = create.data["slug"]
        other = Meeting.objects.create(
            project=self.project,
            title="Other meeting",
            type_definition=self.planning,
            objective="o",
        )
        patch = self.client.patch(
            f"/api/tasks/{task_slug}/",
            {"origin_meeting_id": other.id},
            format="json",
        )
        self.assertEqual(patch.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already", str(patch.data.get("origin_meeting_id", [])).lower())

    def test_patch_task_with_origin_rejected_when_no_origin_yet(self):
        t = Task.objects.create(
            project=self.project,
            owner=self.user,
            summary="No origin",
            description="d",
            type="asset",
        )
        patch = self.client.patch(
            f"/api/tasks/{t.slug}/",
            {"origin_meeting_id": self.meeting.id},
            format="json",
        )
        self.assertEqual(patch.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(
            "creating",
            str(patch.data.get("origin_meeting_id", [])).lower(),
        )

    def test_task_detail_returns_origin_meeting_null_without_origin(self):
        t = Task.objects.create(
            project=self.project,
            owner=self.user,
            summary="Plain",
            description="d",
            type="asset",
        )
        response = self.client.get(f"/api/tasks/{t.slug}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("origin_meeting", response.data)
        self.assertIsNone(response.data["origin_meeting"])
