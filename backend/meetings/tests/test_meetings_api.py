from datetime import timedelta

from meetings.tasks import update_meeting_search_vector

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Organization, Project, ProjectMember, CustomUser
from meetings.models import (
    AgendaItem,
    Meeting,
    MeetingTypeDefinition,
    MeetingDocument,
    ParticipantLink,
)
from notifications.models import Notification, NotificationEventType



class TestMeetingAPI(TestCase):
    def setUp(self):
        self.client = APIClient()

        self.organization = Organization.objects.create(name="Org", slug="org")
        self.project_a = Project.objects.create(
            name="Project A",
            organization=self.organization,
        )
        self.project_b = Project.objects.create(
            name="Project B",
            organization=self.organization,
        )

        self.user_a = CustomUser.objects.create_user(
            email="user_a@example.com",
            password="password",
            username="user_a",
        )
        ProjectMember.objects.create(
            user=self.user_a,
            project=self.project_a,
            is_active=True,
        )

        self.user_b = CustomUser.objects.create_user(
            email="user_b@example.com",
            password="password",
            username="user_b",
        )
        ProjectMember.objects.create(
            user=self.user_b,
            project=self.project_b,
            is_active=True,
        )

        self.client.force_authenticate(user=self.user_a)

    def _meeting_type(self, project, *, slug: str, label: str | None = None):
        obj, _ = MeetingTypeDefinition.objects.get_or_create(
            project=project,
            slug=slug,
            defaults={"label": label or slug},
        )
        return obj

    def _extract_ids(self, response):
        """Normalize list vs paginated responses and return ID set."""
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        data = response.data
        if isinstance(data, dict) and "results" in data:
            items = data["results"]
        else:
            items = data

        self.assertIsInstance(items, list)
        return {item["id"] for item in items}

    def test_project_isolation_meeting_list(self):
        meeting_a = Meeting.objects.create(
            project=self.project_a,
            title="Meeting A",
            type_definition=self._meeting_type(self.project_a, slug="planning"),
            objective="Objective A",
        )
        meeting_b = Meeting.objects.create(
            project=self.project_b,
            title="Meeting B",
            type_definition=self._meeting_type(self.project_b, slug="review"),
            objective="Objective B",
        )

        url = f"/api/projects/{self.project_a.slug}/meetings/"
        response = self.client.get(url)

        ids = self._extract_ids(response)
        self.assertIn(meeting_a.id, ids)
        self.assertNotIn(meeting_b.id, ids)

    def test_project_isolation_meeting_list_forbidden_for_other_project(self):
        url = f"/api/projects/{self.project_b.slug}/meetings/"
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_reorder_agenda_items(self):
        meeting = Meeting.objects.create(
            project=self.project_a,
            title="Meeting",
            type_definition=self._meeting_type(self.project_a, slug="planning"),
            objective="Objective",
        )
        AgendaItem.objects.create(
            meeting=meeting, content="Item 1", order_index=0, is_priority=False
        )
        AgendaItem.objects.create(
            meeting=meeting, content="Item 2", order_index=1, is_priority=False
        )
        AgendaItem.objects.create(
            meeting=meeting, content="Item 3", order_index=2, is_priority=False
        )

        url = f"/api/projects/{self.project_a.slug}/meetings/{meeting.slug}/agenda-items/reorder/"
        items = list(AgendaItem.objects.filter(meeting=meeting).order_by("order_index"))

        payload = {
            "items": [
                {"id": items[0].id, "order_index": 2},
                {"id": items[1].id, "order_index": 0},
                {"id": items[2].id, "order_index": 1},
            ]
        }

        response = self.client.patch(url, data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        ordered = list(
            AgendaItem.objects.filter(meeting=meeting).order_by("order_index", "id")
        )
        self.assertEqual(
            [a.content for a in ordered],
            ["Item 2", "Item 3", "Item 1"],
        )

    def test_reorder_agenda_items_invalid_payload(self):
        meeting = Meeting.objects.create(
            project=self.project_a,
            title="Meeting",
            type_definition=self._meeting_type(self.project_a, slug="planning"),
            objective="Objective",
        )

        url = f"/api/projects/{self.project_a.slug}/meetings/{meeting.slug}/agenda-items/reorder/"

        response = self.client.patch(
            url,
            data={"items": "not-a-list"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_participant_unique_constraint(self):
        meeting = Meeting.objects.create(
            project=self.project_a,
            title="Meeting",
            type_definition=self._meeting_type(self.project_a, slug="planning"),
            objective="Objective",
        )

        url = f"/api/projects/{self.project_a.slug}/meetings/{meeting.slug}/participants/"

        response1 = self.client.post(
            url,
            data={"user": self.user_a.id, "role": "host"},
            format="json",
        )
        self.assertEqual(response1.status_code, status.HTTP_201_CREATED)
        self.assertEqual(
            ParticipantLink.objects.filter(
                meeting=meeting,
                user=self.user_a,
            ).count(),
            1,
        )

        response2 = self.client.post(
            url,
            data={"user": self.user_a.id, "role": "host"},
            format="json",
        )
        self.assertEqual(response2.status_code, status.HTTP_400_BAD_REQUEST)

    def test_meeting_cascade_delete_agenda_items(self):
        meeting = Meeting.objects.create(
            project=self.project_a,
            title="Meeting",
            type_definition=self._meeting_type(self.project_a, slug="planning"),
            objective="Objective",
        )
        AgendaItem.objects.create(
            meeting=meeting, content="Item 1", order_index=0, is_priority=False
        )
        AgendaItem.objects.create(
            meeting=meeting, content="Item 2", order_index=1, is_priority=False
        )

        url = f"/api/projects/{self.project_a.slug}/meetings/{meeting.slug}/"

        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(AgendaItem.objects.filter(meeting=meeting).exists())

    def test_create_meeting_with_participant_user_ids(self):
        url = f"/api/projects/{self.project_a.slug}/meetings/"
        payload = {
            "title": "With participants",
            "meeting_type": "planning",
            "objective": "Discuss",
            "participant_user_ids": [self.user_a.id, self.user_a.id],
        }
        response = self.client.post(url, data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        meeting_id = response.data["id"]
        self.assertIn("participants", response.data)
        self.assertIn("tags", response.data)
        self.assertEqual(
            response.data["participants"],
            [{"user_id": self.user_a.id, "role": None, "username": self.user_a.username, "avatar": None}],
        )
        self.assertEqual(response.data["tags"], [])
        links = ParticipantLink.objects.filter(meeting_id=meeting_id, user=self.user_a)
        self.assertEqual(links.count(), 1)

    def test_create_meeting_participant_user_ids_rejects_non_member(self):
        url = f"/api/projects/{self.project_a.slug}/meetings/"
        payload = {
            "title": "Bad participant",
            "meeting_type": "planning",
            "objective": "X",
            "participant_user_ids": [self.user_b.id],
        }
        response = self.client.post(url, data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(
            Meeting.objects.filter(title="Bad participant").exists(),
        )

    def test_meeting_list_includes_hub_lane_counts(self):
        """Paginated list adds Incoming/Completed lane totals for the meetings hub badge (A OF B)."""
        today = timezone.now().date()
        mt = self._meeting_type(self.project_a, slug="planning")
        Meeting.objects.create(
            project=self.project_a,
            title="Past",
            type_definition=mt,
            objective="x",
            scheduled_date=today - timedelta(days=10),
        )
        Meeting.objects.create(
            project=self.project_a,
            title="Future",
            type_definition=mt,
            objective="x",
            scheduled_date=today + timedelta(days=10),
        )
        Meeting.objects.create(
            project=self.project_a,
            title="Undated",
            type_definition=mt,
            objective="x",
            scheduled_date=None,
        )

        url = f"/api/projects/{self.project_a.slug}/meetings/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["incoming_lane_total"], 2)
        self.assertEqual(response.data["completed_lane_total"], 1)
        self.assertEqual(response.data["incoming_result_count"], 2)
        self.assertEqual(response.data["completed_result_count"], 1)
        self.assertEqual(response.data["incoming_lane_filtered"], 2)
        self.assertEqual(response.data["completed_lane_filtered"], 1)

    def test_meeting_list_lane_filtered_differs_when_q_narrows(self):
        mt = self._meeting_type(self.project_a, slug="planning")
        m1 = Meeting.objects.create(
            project=self.project_a,
            title="Alpha unique",
            type_definition=mt,
            objective="x",
            scheduled_date=None,
        )
        m2 = Meeting.objects.create(
            project=self.project_a,
            title="Beta other",
            type_definition=mt,
            objective="x",
            scheduled_date=None,
        )
        update_meeting_search_vector(m1.pk)
        update_meeting_search_vector(m2.pk)

        self.assertEqual(
            Meeting.objects.filter(project=self.project_a, is_deleted=False).count(),
            2,
        )

        url = f"/api/projects/{self.project_a.slug}/meetings/"
        r0 = self.client.get(url)
        self.assertEqual(r0.status_code, status.HTTP_200_OK)
        self.assertEqual(r0.data["incoming_lane_total"], 2)
        self.assertEqual(r0.data["completed_lane_total"], 0)
        self.assertEqual(r0.data["incoming_result_count"], 2)

        r1 = self.client.get(url, {"q": "Alpha unique"})
        self.assertEqual(r1.status_code, status.HTTP_200_OK)
        self.assertEqual(r1.data["incoming_result_count"], 1)
        self.assertEqual(r1.data["incoming_lane_total"], r0.data["incoming_lane_total"])

    @override_settings(MEETINGS_REQUIRE_PARTICIPANTS_AT_CREATE=True)
    def test_create_meeting_defaults_creator_as_participant_when_strict_and_none_sent(self):
        url = f"/api/projects/{self.project_a.slug}/meetings/"
        payload = {
            "title": "No participants",
            "meeting_type": "planning",
            "objective": "Y",
        }
        response = self.client.post(url, data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        meeting_id = response.data["id"]
        self.assertTrue(
            ParticipantLink.objects.filter(
                meeting_id=meeting_id,
                user_id=self.user_a.id,
            ).exists(),
        )

    def test_patch_meeting_layout_config_object_shape(self):
        meeting = Meeting.objects.create(
            project=self.project_a,
            title="Meeting",
            type_definition=self._meeting_type(self.project_a, slug="planning"),
            objective="Objective",
        )
        url = f"/api/projects/{self.project_a.slug}/meetings/{meeting.slug}/"
        payload = {
            "layout_config": {
                "blocks": [
                    {"id": "header", "type": "header"},
                    {"id": "agenda", "type": "agenda"},
                ],
                "nestedSections": [
                    {
                        "id": "s1",
                        "title": "Section A",
                        "items": [
                            {
                                "id": "1",
                                "text": "Item",
                                "completed": False,
                                "duration": "5m",
                            }
                        ],
                    }
                ],
            }
        }
        response = self.client.patch(url, data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        meeting.refresh_from_db()
        self.assertIsInstance(meeting.layout_config, dict)
        self.assertEqual(len(meeting.layout_config["blocks"]), 2)
        self.assertEqual(len(meeting.layout_config["nestedSections"]), 1)

        get_response = self.client.get(url)
        self.assertEqual(get_response.status_code, status.HTTP_200_OK)
        lc = get_response.data["layout_config"]
        self.assertIsInstance(lc, dict)
        self.assertEqual(lc["nestedSections"][0]["title"], "Section A")

    def test_get_meeting_document_creates_default_document(self):
        meeting = Meeting.objects.create(
            project=self.project_a,
            title="Meeting Doc",
            type_definition=self._meeting_type(self.project_a, slug="planning"),
            objective="Doc",
        )
        url = f"/api/projects/{self.project_a.slug}/meetings/{meeting.slug}/document/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["meeting"], meeting.id)
        self.assertEqual(response.data["content"], "")
        self.assertTrue(MeetingDocument.objects.filter(meeting=meeting).exists())

    def test_patch_meeting_document_updates_content(self):
        meeting = Meeting.objects.create(
            project=self.project_a,
            title="Meeting Doc",
            type_definition=self._meeting_type(self.project_a, slug="planning"),
            objective="Doc",
        )
        url = f"/api/projects/{self.project_a.slug}/meetings/{meeting.slug}/document/"
        response = self.client.patch(
            url,
            data={"content": "Collaborative content"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["content"], "Collaborative content")
        doc = MeetingDocument.objects.get(meeting=meeting)
        self.assertEqual(doc.last_edited_by_id, self.user_a.id)

    def test_patch_meeting_document_notifies_other_participant_doc_asset_update(self):
        """Non-editor participant saves via REST PATCH → others receive DOC_ASSET_UPDATE."""
        ProjectMember.objects.create(
            user=self.user_b,
            project=self.project_a,
            is_active=True,
        )
        meeting = Meeting.objects.create(
            project=self.project_a,
            title="Shared Notes Meeting",
            type_definition=self._meeting_type(self.project_a, slug="planning"),
            objective="O",
        )
        ParticipantLink.objects.create(meeting=meeting, user=self.user_a, is_accepted=True)
        ParticipantLink.objects.create(meeting=meeting, user=self.user_b, is_accepted=True)

        url = f"/api/projects/{self.project_a.slug}/meetings/{meeting.slug}/document/"

        # Seed initial content directly through the service to avoid generating
        # an unwanted notification for user_b before the assertion window.
        from meetings.services import update_meeting_document_content
        update_meeting_document_content(
            meeting_id=meeting.id,
            content="First draft body text",
            user_id=self.user_a.id,
            notify_collaborators=False,
        )

        # user_b edits → should notify user_a, but NOT user_b (self-edit)
        self.client.force_authenticate(user=self.user_b)
        response = self.client.patch(
            url,
            data={"content": "Second draft body text from user B"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        qs = Notification.objects.filter(
            recipient=self.user_a,
            actor=self.user_b,
            event_type=NotificationEventType.DOC_ASSET_UPDATE,
        )
        self.assertEqual(qs.count(), 1)
        n = qs.first()
        self.assertEqual(n.related_object_type, "meeting")
        self.assertEqual(n.related_object_id, str(meeting.id))
        md = n.metadata
        self.assertEqual(md.get("project_id"), self.project_a.id)
        self.assertEqual(md.get("meeting_id"), meeting.id)
        self.assertEqual(md.get("old_title"), "Shared Notes Meeting")
        self.assertEqual(md.get("new_title"), "Shared Notes Meeting")
        self.assertIn("First draft", md.get("old_agenda", ""))
        self.assertIn("Second draft", md.get("new_agenda", ""))

        # The editor (user_b) must NOT receive a notification for their own edit.
        self.assertFalse(
            Notification.objects.filter(
                recipient=self.user_b,
                actor=self.user_b,
                event_type=NotificationEventType.DOC_ASSET_UPDATE,
            ).exists()
        )

    def test_patch_meeting_document_no_notification_when_content_unchanged(self):
        ProjectMember.objects.create(
            user=self.user_b,
            project=self.project_a,
            is_active=True,
        )
        meeting = Meeting.objects.create(
            project=self.project_a,
            title="M",
            type_definition=self._meeting_type(self.project_a, slug="planning"),
            objective="O",
        )
        ParticipantLink.objects.create(meeting=meeting, user=self.user_a)
        ParticipantLink.objects.create(meeting=meeting, user=self.user_b)
        url = f"/api/projects/{self.project_a.slug}/meetings/{meeting.slug}/document/"
        body = "Same content"
        self.client.force_authenticate(user=self.user_a)
        self.client.patch(url, data={"content": body}, format="json")

        before = Notification.objects.count()
        self.client.force_authenticate(user=self.user_b)
        r = self.client.patch(url, data={"content": body}, format="json")
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(
            Notification.objects.count(),
            before,
            "No new notification when content string is identical",
        )

    def test_meeting_document_get_allowed_for_participant_without_project_membership(self):
        meeting = Meeting.objects.create(
            project=self.project_a,
            title="Invited only",
            type_definition=self._meeting_type(self.project_a, slug="planning"),
            objective="X",
        )
        user_c = CustomUser.objects.create_user(
            email="user_c@example.com",
            password="password",
            username="user_c",
        )
        ParticipantLink.objects.create(meeting=meeting, user=user_c)
        url = f"/api/projects/{self.project_a.slug}/meetings/{meeting.slug}/document/"
        self.client.force_authenticate(user=user_c)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["meeting"], meeting.id)

    def test_meeting_document_forbidden_without_member_or_participant_link(self):
        meeting = Meeting.objects.create(
            project=self.project_a,
            title="Private doc",
            type_definition=self._meeting_type(self.project_a, slug="planning"),
            objective="Y",
        )
        user_c = CustomUser.objects.create_user(
            email="user_d@example.com",
            password="password",
            username="user_d",
        )
        url = f"/api/projects/{self.project_a.slug}/meetings/{meeting.slug}/document/"
        self.client.force_authenticate(user=user_c)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


