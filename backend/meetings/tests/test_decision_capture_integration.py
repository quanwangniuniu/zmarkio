from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Organization, Project, ProjectMember, CustomUser
from decision.models import Decision
from meetings.models import Meeting, MeetingDecisionOrigin, MeetingTypeDefinition
from meetings.tests.decision_origin_helpers import create_meeting_decision_origin


class TestDecisionCaptureIntegration(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.organization = Organization.objects.create(
            name="OrgDecisionCapture",
            slug="org-decision-capture",
        )
        self.project = Project.objects.create(
            name="Decision Capture Project",
            organization=self.organization,
        )
        self.user = CustomUser.objects.create_user(
            email="decision_capture@example.com",
            password="password",
            username="decision_capture",
        )
        ProjectMember.objects.create(
            user=self.user,
            project=self.project,
            is_active=True,
        )
        self.meeting_type = MeetingTypeDefinition.objects.create(
            project=self.project,
            slug="planning",
            label="Planning",
        )
        self.client.force_authenticate(user=self.user)

    def _meeting(self, **overrides):
        data = {
            "project": self.project,
            "title": "Campaign Planning Meeting",
            "type_definition": self.meeting_type,
            "objective": "Decide campaign budget allocation.",
            "summary": "Discussed moving spend from Meta to Google Search.",
        }
        data.update(overrides)
        return Meeting.objects.create(**data)

    def test_create_decision_from_meeting_preserves_lineage(self):
        meeting = self._meeting()
        url = f"/api/projects/{self.project.slug}/meetings/{meeting.slug}/decisions/"

        response = self.client.post(
            url,
            {
                "title": "Move budget to Google Search",
                "contextSummary": "Meta CPA is too high; move budget to Search.",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        decision = Decision.objects.get(title="Move budget to Google Search")
        origin = MeetingDecisionOrigin.objects.get(decision=decision)

        self.assertEqual(origin.meeting, meeting)
        self.assertEqual(origin.created_by, self.user)
        self.assertIsNotNone(origin.origin_timestamp)
        self.assertEqual(origin.creation_context["meeting_id"], meeting.id)
        self.assertEqual(origin.creation_context["meeting_title"], meeting.title)
        self.assertEqual(response.data["originMeeting"]["id"], meeting.id)

    def test_retrieve_meeting_decisions_supports_multiple_decisions(self):
        meeting = self._meeting()
        url = f"/api/projects/{self.project.slug}/meetings/{meeting.slug}/decisions/"

        first = self.client.post(url, {"title": "Decision A"}, format="json")
        second = self.client.post(url, {"title": "Decision B"}, format="json")

        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["items"]), 2)
        self.assertEqual(
            MeetingDecisionOrigin.objects.filter(meeting=meeting).count(),
            2,
        )

    def test_meeting_delete_cascades_decision_origins(self):
        """Test that deleting a meeting also deletes its decision origins (CASCADE)."""
        meeting = self._meeting()
        decision = Decision.objects.create(
            project=self.project,
            author=self.user,
            title="Protected origin decision",
        )
        create_meeting_decision_origin(
            meeting=meeting,
            decision=decision,
            user=self.user,
        )

        meeting.delete()
        self.assertFalse(MeetingDecisionOrigin.objects.filter(meeting_id=meeting.id).exists())

    def test_meeting_status_does_not_control_decision_lifecycle(self):
        meeting = self._meeting(status=Meeting.STATUS_COMPLETED)
        url = f"/api/projects/{self.project.slug}/meetings/{meeting.slug}/decisions/"

        response = self.client.post(
            url,
            {"title": "Lifecycle independent decision"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        decision = Decision.objects.get(title="Lifecycle independent decision")
        self.assertNotEqual(decision.status, meeting.status)
        self.assertEqual(Meeting.objects.get(pk=meeting.pk).status, Meeting.STATUS_COMPLETED)

    def test_create_decision_from_meeting_rejects_cross_project_meeting(self):
        other_project = Project.objects.create(
            name="Other Decision Capture Project",
            organization=self.organization,
        )
        other_meeting_type = MeetingTypeDefinition.objects.create(
            project=other_project,
            slug="other-planning",
            label="Other Planning",
        )
        other_meeting = Meeting.objects.create(
            project=other_project,
            title="Other project meeting",
            type_definition=other_meeting_type,
        )

        response = self.client.post(
            f"/api/projects/{self.project.slug}/meetings/{other_meeting.slug}/decisions/",
            {"title": "Should not be created"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(
            Decision.objects.filter(title="Should not be created").exists()
        )
        self.assertFalse(
            MeetingDecisionOrigin.objects.filter(meeting=other_meeting).exists()
        )

    def test_decision_can_only_have_one_origin_meeting(self):
        first_meeting = self._meeting(title="First origin meeting")
        second_meeting = self._meeting(title="Second origin meeting")
        decision = Decision.objects.create(
            project=self.project,
            author=self.user,
            title="Single origin decision",
        )

        create_meeting_decision_origin(
            meeting=first_meeting,
            decision=decision,
            user=self.user,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                create_meeting_decision_origin(
                    meeting=second_meeting,
                    decision=decision,
                    user=self.user,
                )

        self.assertEqual(
            MeetingDecisionOrigin.objects.filter(decision=decision).count(),
            1,
        )
        self.assertEqual(
            MeetingDecisionOrigin.objects.get(decision=decision).meeting,
            first_meeting,
        )

    def test_meeting_decisions_endpoint_excludes_other_and_unlinked_decisions(self):
        source_meeting = self._meeting(title="Source meeting")
        other_meeting = self._meeting(title="Other meeting")

        source_decision = Decision.objects.create(
            project=self.project,
            author=self.user,
            title="Source meeting decision",
        )
        other_decision = Decision.objects.create(
            project=self.project,
            author=self.user,
            title="Other meeting decision",
        )
        Decision.objects.create(
            project=self.project,
            author=self.user,
            title="Unlinked decision",
        )

        create_meeting_decision_origin(
            meeting=source_meeting,
            decision=source_decision,
            user=self.user,
        )
        create_meeting_decision_origin(
            meeting=other_meeting,
            decision=other_decision,
            user=self.user,
        )

        response = self.client.get(
            f"/api/projects/{self.project.slug}/meetings/{source_meeting.slug}/decisions/"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        titles = {item["title"] for item in response.data["items"]}

        self.assertIn("Source meeting decision", titles)
        self.assertNotIn("Other meeting decision", titles)
        self.assertNotIn("Unlinked decision", titles)
