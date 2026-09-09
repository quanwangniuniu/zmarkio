from django.test import TestCase

from core.models import Organization, Project, ProjectMember, CustomUser
from meetings.models import Meeting, MeetingTypeDefinition
from meetings.services import apply_meeting_knowledge_filters
from meetings.tasks import update_meeting_search_vector

class TestTranscriptSearch(TestCase):
    """Full-text search across meeting transcripts."""
    def setUp(self):
        self.organization = Organization.objects.create(
            name="Test Org", slug="test-org"
        )
        self.project = Project.objects.create(
            name="Test Project",
            organization=self.organization,
        )
        self.user = CustomUser.objects.create_user(
            email="user@example.com",
            password="password",
            username="testuser"
        )
        ProjectMember.objects.create(
            user=self.user,
            project=self.project,
            is_active=True
        )
        self.meeting_type = MeetingTypeDefinition.objects.create(
            project=self.project,
            slug="weekly",
            label="Weekly"
        )

    def test_task_populates_search_vector(self):
        meeting = Meeting.objects.create(
            project=self.project,
            title="Budget Review",
            type_definition=self.meeting_type,
            objective="Review Q3 Budget",
            transcript="Tim: we need to cut the budget. Sarah: I agree."
        )
        self.assertIsNone(meeting.search_vector)

        update_meeting_search_vector(meeting.pk)

        meeting.refresh_from_db()
        self.assertIsNotNone(meeting.search_vector)

    def test_task_missing_meeting_does_not_raise(self):
        try:
            update_meeting_search_vector(99999)
        except Exception as e:
            self.fail(f"Task raised an unexpected exception: {e}")

    def test_search_finds_transcript_content(self):
        Meeting.objects.create(
            project=self.project,
            title="Weekly Sync",
            type_definition=self.meeting_type,
            objective="o",
            transcript="Tim: We need to cut the budget. Sarah: I agree.",
        )

        update_meeting_search_vector(
            Meeting.objects.get(project=self.project).pk
        )

        qs = Meeting.objects.filter(project=self.project)
        results = apply_meeting_knowledge_filters(qs, {"q": "budget"})
        self.assertIn(
            Meeting.objects.get(project=self.project).pk,
            [m.pk for m in results],
        )

    def test_search_no_match_returns_empty(self):
        meeting = Meeting.objects.create(
            project=self.project,
            title="Weekly Sync",
            type_definition=self.meeting_type,
            objective="o",
            transcript="Tim: We need to cut the budget. Sarah: I agree.",
        )
        update_meeting_search_vector(meeting.pk)

        qs = Meeting.objects.filter(project=self.project)
        results = apply_meeting_knowledge_filters(qs, {"q": "blockchain"})
        self.assertEqual(results.count(), 0)

    def test_search_scoped_to_project(self):
        other_project = Project.objects.create(
            name="Other Project",
            organization=self.organization,
        )
        other_type = MeetingTypeDefinition.objects.create(
            project=other_project,
            slug="weekly",
            label="Weekly",
        )
        my_meeting = Meeting.objects.create(
            project=self.project,
            title="My Meeting",
            type_definition=self.meeting_type,
            objective="o",
            transcript="Tim: discuss the budget allocation.",
        )
        other_meeting = Meeting.objects.create(
            project=other_project,
            title="Other Meeting",
            type_definition=other_type,
            objective="o",
            transcript="Sarah: discuss the budget allocation.",
        )
        update_meeting_search_vector(my_meeting.pk)
        update_meeting_search_vector(other_meeting.pk)

        qs = Meeting.objects.filter(project=self.project)
        results = apply_meeting_knowledge_filters(qs, {"q": "budget"})
        result_ids = [m.pk for m in results]
        self.assertIn(my_meeting.pk, result_ids)
        self.assertNotIn(other_meeting.pk, result_ids)