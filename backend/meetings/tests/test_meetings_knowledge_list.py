from datetime import date
from urllib.parse import urlencode

from meetings.tasks import update_meeting_search_vector

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from rest_framework.test import APIClient

from core.models import Organization, Project, ProjectMember, CustomUser
from decision.models import Decision
from meetings.models import (
    ArtifactLink,
    Meeting,
    MeetingDecisionOrigin,
    MeetingTagAssignment,
    MeetingTagDefinition,
    MeetingTaskOrigin,
    MeetingTypeDefinition,
    ParticipantLink,
)
from meetings.views import MeetingViewSet
from task.models import Task
from meetings.tests.decision_origin_helpers import create_meeting_decision_origin


class _TwoPerPage(PageNumberPagination):
    page_size = 2


class TestMeetingsKnowledgeListAPI(TestCase):
    """GET /api/projects/{id}/meetings/ — filters, q, pagination, strict slugs."""

    def setUp(self):
        self.client = APIClient()
        self.organization = Organization.objects.create(name="OrgK", slug="org-k")
        self.project = Project.objects.create(
            name="Project K",
            organization=self.organization,
        )
        self.user = CustomUser.objects.create_user(
            email="k_user@example.com",
            password="password",
            username="k_user",
        )
        ProjectMember.objects.create(
            user=self.user,
            project=self.project,
            is_active=True,
        )
        self.other_project = Project.objects.create(
            name="Other",
            organization=self.organization,
        )
        self.other_user = CustomUser.objects.create_user(
            email="other@example.com",
            password="password",
            username="other_u",
        )
        ProjectMember.objects.create(
            user=self.other_user,
            project=self.other_project,
            is_active=True,
        )

        self.planning = MeetingTypeDefinition.objects.create(
            project=self.project,
            slug="planning",
            label="Planning",
        )
        self.review = MeetingTypeDefinition.objects.create(
            project=self.project,
            slug="review",
            label="Review",
        )
        self.tag_strat = MeetingTagDefinition.objects.create(
            project=self.project,
            slug="strategy",
            label="Strategy",
        )

        self.client.force_authenticate(user=self.user)

    def _url(self, **query):
        base = f"/api/projects/{self.project.slug}/meetings/"
        if not query:
            return base
        q = "&".join(f"{k}={v}" for k, v in query.items())
        return f"{base}?{q}"

    def _results(self, response):
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)
        self.assertIn("count", response.data)
        return response.data["results"]

    def test_list_project_scoped_only(self):
        m1 = Meeting.objects.create(
            project=self.project,
            title="In",
            type_definition=self.planning,
            objective="o",
            summary="",
        )
        Meeting.objects.create(
            project=self.other_project,
            title="Out",
            type_definition=MeetingTypeDefinition.objects.create(
                project=self.other_project,
                slug="p",
                label="P",
            ),
            objective="o",
        )
        ids = {row["id"] for row in self._results(self.client.get(self._url()))}
        self.assertEqual(ids, {m1.id})

    def test_q_matches_title(self):
        m1 = Meeting.objects.create(
            project=self.project,
            title="Alpha roadmap",
            type_definition=self.planning,
            objective="o",
            summary="",
        )
        m2 = Meeting.objects.create(
            project=self.project,
            title="Beta sync",
            type_definition=self.planning,
            objective="o",
            summary="",
        )
        update_meeting_search_vector(m1.pk)
        update_meeting_search_vector(m2.pk)
        rows = self._results(self.client.get(self._url(q="beta")))
        self.assertEqual({r["id"] for r in rows}, {m2.id})

    def test_q_matches_summary(self):
        m = Meeting.objects.create(
            project=self.project,
            title="T",
            type_definition=self.planning,
            objective="o",
            summary="Quarterly outcomes digest",
        )
        update_meeting_search_vector(m.pk)
        rows = self._results(self.client.get(self._url(q="digest")))
        self.assertEqual({r["id"] for r in rows}, {m.id})

    def test_filter_meeting_type_slug(self):
        m1 = Meeting.objects.create(
            project=self.project,
            title="A",
            type_definition=self.planning,
            objective="o",
        )
        Meeting.objects.create(
            project=self.project,
            title="B",
            type_definition=self.review,
            objective="o",
        )
        rows = self._results(self.client.get(self._url(meeting_type="planning")))
        self.assertEqual({r["id"] for r in rows}, {m1.id})

    def test_filter_meeting_type_multiple_or(self):
        m_plan = Meeting.objects.create(
            project=self.project,
            title="Plan only",
            type_definition=self.planning,
            objective="o",
        )
        m_rev = Meeting.objects.create(
            project=self.project,
            title="Review only",
            type_definition=self.review,
            objective="o",
        )
        base = f"/api/projects/{self.project.slug}/meetings/"
        qs = urlencode(
            [
                ("meeting_type", "planning"),
                ("meeting_type", "review"),
            ]
        )
        rows = self._results(self.client.get(f"{base}?{qs}"))
        self.assertEqual({r["id"] for r in rows}, {m_plan.id, m_rev.id})

    def test_filter_participant(self):
        m = Meeting.objects.create(
            project=self.project,
            title="P",
            type_definition=self.planning,
            objective="o",
        )
        ParticipantLink.objects.create(meeting=m, user=self.user, role="host")
        ParticipantLink.objects.create(
            meeting=m,
            user=self.other_user,
            role=None,
        )
        m2 = Meeting.objects.create(
            project=self.project,
            title="Alone",
            type_definition=self.planning,
            objective="o",
        )
        ParticipantLink.objects.create(meeting=m2, user=self.other_user, role=None)

        rows = self._results(
            self.client.get(self._url(participant=str(self.user.id)))
        )
        self.assertEqual({r["id"] for r in rows}, {m.id})

    def test_filter_participant_multiple_or(self):
        """Repeated participant=id — meetings that include any listed user."""
        m_u = Meeting.objects.create(
            project=self.project,
            title="Only user",
            type_definition=self.planning,
            objective="o",
        )
        ParticipantLink.objects.create(meeting=m_u, user=self.user, role="host")
        m_o = Meeting.objects.create(
            project=self.project,
            title="Only other",
            type_definition=self.planning,
            objective="o",
        )
        ParticipantLink.objects.create(meeting=m_o, user=self.other_user, role=None)
        m_neither = Meeting.objects.create(
            project=self.project,
            title="Neither",
            type_definition=self.planning,
            objective="o",
        )
        base = f"/api/projects/{self.project.slug}/meetings/"
        qs = urlencode(
            [
                ("participant", str(self.user.id)),
                ("participant", str(self.other_user.id)),
            ]
        )
        rows = self._results(self.client.get(f"{base}?{qs}"))
        self.assertEqual({r["id"] for r in rows}, {m_u.id, m_o.id})
        self.assertNotIn(m_neither.id, {r["id"] for r in rows})

    def test_participant_exclude_overlap_400(self):
        r = self.client.get(
            self._url(
                participant=str(self.user.id),
                exclude_participant=str(self.user.id),
            )
        )
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("exclude_participant", r.data)

    def test_filter_exclude_participant(self):
        m_with = Meeting.objects.create(
            project=self.project,
            title="With user",
            type_definition=self.planning,
            objective="o",
        )
        ParticipantLink.objects.create(meeting=m_with, user=self.user, role="host")
        m_without = Meeting.objects.create(
            project=self.project,
            title="Without user",
            type_definition=self.planning,
            objective="o",
        )
        ParticipantLink.objects.create(meeting=m_without, user=self.other_user, role=None)

        rows = self._results(
            self.client.get(self._url(exclude_participant=str(self.user.id)))
        )
        self.assertEqual({r["id"] for r in rows}, {m_without.id})

    def test_filter_tag_slug(self):
        m = Meeting.objects.create(
            project=self.project,
            title="Tagged",
            type_definition=self.planning,
            objective="o",
        )
        MeetingTagAssignment.objects.create(
            meeting=m,
            tag_definition=self.tag_strat,
        )
        m2 = Meeting.objects.create(
            project=self.project,
            title="No tag",
            type_definition=self.planning,
            objective="o",
        )
        rows = self._results(self.client.get(self._url(tag="strategy")))
        self.assertEqual({r["id"] for r in rows}, {m.id})
        self.assertNotIn(m2.id, {r["id"] for r in rows})

    def test_filter_date_range_excludes_null_scheduled_date(self):
        m_in = Meeting.objects.create(
            project=self.project,
            title="In range",
            type_definition=self.planning,
            objective="o",
            scheduled_date=date(2026, 2, 15),
        )
        Meeting.objects.create(
            project=self.project,
            title="Null date",
            type_definition=self.planning,
            objective="o",
            scheduled_date=None,
        )
        rows = self._results(
            self.client.get(
                self._url(date_from="2026-02-01", date_to="2026-02-28")
            )
        )
        self.assertEqual({r["id"] for r in rows}, {m_in.id})

    def test_filter_is_archived(self):
        ma = Meeting.objects.create(
            project=self.project,
            title="Arch",
            type_definition=self.planning,
            objective="o",
            is_archived=True,
        )
        mna = Meeting.objects.create(
            project=self.project,
            title="Live",
            type_definition=self.planning,
            objective="o",
            is_archived=False,
        )
        rows_a = self._results(self.client.get(self._url(is_archived="true")))
        self.assertEqual({r["id"] for r in rows_a}, {ma.id})
        rows_na = self._results(self.client.get(self._url(is_archived="false")))
        self.assertEqual({r["id"] for r in rows_na}, {mna.id})

    def test_ordering_title(self):
        mb = Meeting.objects.create(
            project=self.project,
            title="Bbb",
            type_definition=self.planning,
            objective="o",
        )
        ma = Meeting.objects.create(
            project=self.project,
            title="Aaa",
            type_definition=self.planning,
            objective="o",
        )
        rows = self._results(self.client.get(self._url(ordering="title")))
        titles = [r["title"] for r in rows]
        self.assertEqual(titles, ["Aaa", "Bbb"])

    def test_decision_and_task_counts(self):
        m = Meeting.objects.create(
            project=self.project,
            title="Counts",
            type_definition=self.planning,
            objective="o",
        )
        d1 = Decision.objects.create(
            project=self.project, author=self.user, title="Pause spend"
        )
        d2 = Decision.objects.create(
            project=self.project, author=self.user, title="Raise budget"
        )
        create_meeting_decision_origin(meeting=m, decision=d1)
        create_meeting_decision_origin(meeting=m, decision=d2)
        t = Task.objects.create(
            summary="Do thing",
            project=self.project,
            type="report",
            owner=self.user,
        )
        MeetingTaskOrigin.objects.create(meeting=m, task=t)

        rows = self._results(self.client.get(self._url()))
        row = next(r for r in rows if r["id"] == m.id)
        self.assertEqual(row["decision_count"], 2)
        self.assertEqual(row["task_count"], 1)
        self.assertEqual(len(row["generated_decisions"]), 2)
        self.assertEqual(len(row["generated_tasks"]), 1)
        ld = sorted(row["generated_decisions"], key=lambda x: x["id"])
        self.assertEqual(ld[0]["id"], d1.id)
        self.assertEqual(ld[0]["title"], "Pause spend")
        self.assertEqual(
            ld[0]["url"],
            f"/decisions/{d1.slug}?project_id={self.project.id}",
        )
        lt = row["generated_tasks"][0]
        self.assertEqual(lt["id"], t.id)
        self.assertEqual(lt["title"], "Do thing")
        self.assertEqual(
            lt["url"],
            f"/tasks/{t.slug}",
        )

    def test_filter_has_generated_decisions_uses_origin_only(self):
        m_with = Meeting.objects.create(
            project=self.project,
            title="With D",
            type_definition=self.planning,
            objective="o",
        )
        d = Decision.objects.create(
            project=self.project,
            author=self.user,
            title="D",
        )
        create_meeting_decision_origin(meeting=m_with, decision=d)

        m_without = Meeting.objects.create(
            project=self.project,
            title="No D",
            type_definition=self.planning,
            objective="o",
        )

        rows = self._results(
            self.client.get(self._url(has_generated_decisions="true"))
        )
        ids = {r["id"] for r in rows}
        self.assertIn(m_with.id, ids)
        self.assertNotIn(m_without.id, ids)

        rows_lo = self._results(
            self.client.get(self._url(has_generated_decisions="false"))
        )
        ids_lo = {r["id"] for r in rows_lo}
        self.assertIn(m_without.id, ids_lo)
        self.assertNotIn(m_with.id, ids_lo)

    def test_filter_has_generated_tasks_uses_origin_only(self):
        m_with = Meeting.objects.create(
            project=self.project,
            title="With T",
            type_definition=self.planning,
            objective="o",
        )
        t = Task.objects.create(
            summary="T",
            project=self.project,
            type="report",
            owner=self.user,
        )
        MeetingTaskOrigin.objects.create(meeting=m_with, task=t)

        m_without = Meeting.objects.create(
            project=self.project,
            title="No T",
            type_definition=self.planning,
            objective="o",
        )

        rows = self._results(
            self.client.get(self._url(has_generated_tasks="true"))
        )
        ids = {r["id"] for r in rows}
        self.assertIn(m_with.id, ids)
        self.assertNotIn(m_without.id, ids)

    def test_has_generated_decisions_ignores_artifact_links_without_origin(self):
        m = Meeting.objects.create(
            project=self.project,
            title="Artifact only",
            type_definition=self.planning,
            objective="o",
        )
        d = Decision.objects.create(
            project=self.project,
            author=self.user,
            title="Linked via artifact",
        )
        ArtifactLink.objects.create(
            meeting=m,
            artifact_type="decision",
            artifact_id=d.id,
        )

        rows = self._results(
            self.client.get(self._url(has_generated_decisions="true"))
        )
        self.assertNotIn(m.id, {r["id"] for r in rows})

    def test_combined_filters_participant_date_range_has_generated_ordering(self):
        """Participant + date + has_generated_decisions + ordering apply together."""
        d_match = Decision.objects.create(
            project=self.project,
            author=self.user,
            title="Combo D",
        )
        m_match = Meeting.objects.create(
            project=self.project,
            title="Combo match",
            type_definition=self.planning,
            objective="o",
            scheduled_date=date(2026, 2, 15),
        )
        ParticipantLink.objects.create(meeting=m_match, user=self.user, role="host")
        create_meeting_decision_origin(meeting=m_match, decision=d_match)

        d_wrong_date = Decision.objects.create(
            project=self.project,
            author=self.user,
            title="Wrong date D",
        )
        m_wrong_date = Meeting.objects.create(
            project=self.project,
            title="Wrong month",
            type_definition=self.planning,
            objective="o",
            scheduled_date=date(2026, 6, 1),
        )
        ParticipantLink.objects.create(meeting=m_wrong_date, user=self.user, role="host")
        create_meeting_decision_origin(meeting=m_wrong_date, decision=d_wrong_date)

        d_wrong_participant = Decision.objects.create(
            project=self.project,
            author=self.user,
            title="Wrong participant D",
        )
        m_wrong_participant = Meeting.objects.create(
            project=self.project,
            title="Wrong participant",
            type_definition=self.planning,
            objective="o",
            scheduled_date=date(2026, 2, 20),
        )
        ParticipantLink.objects.create(
            meeting=m_wrong_participant,
            user=self.other_user,
            role=None,
        )
        create_meeting_decision_origin(
            meeting=m_wrong_participant,
            decision=d_wrong_participant,
            )

        m_no_origin = Meeting.objects.create(
            project=self.project,
            title="No generated decision",
            type_definition=self.planning,
            objective="o",
            scheduled_date=date(2026, 2, 10),
        )
        ParticipantLink.objects.create(meeting=m_no_origin, user=self.user, role="host")

        r = self.client.get(
            self._url(
                participant=str(self.user.id),
                date_from="2026-02-01",
                date_to="2026-02-28",
                has_generated_decisions="true",
                ordering="-created_at",
            )
        )
        rows = self._results(r)
        ids = [row["id"] for row in rows]
        self.assertEqual(ids, [m_match.id])

    def test_pagination_shape(self):
        prev = MeetingViewSet.pagination_class
        MeetingViewSet.pagination_class = _TwoPerPage
        try:
            for i in range(3):
                Meeting.objects.create(
                    project=self.project,
                    title=f"M{i}",
                    type_definition=self.planning,
                    objective="o",
                )
            r1 = self.client.get(self._url(page="1"))
            self.assertEqual(r1.status_code, status.HTTP_200_OK)
            self.assertEqual(r1.data["count"], 3)
            self.assertEqual(len(r1.data["results"]), 2)
            self.assertIsNotNone(r1.data.get("next"))
        finally:
            MeetingViewSet.pagination_class = prev

    def test_no_duplicate_rows_multi_participant(self):
        m = Meeting.objects.create(
            project=self.project,
            title="Dup",
            type_definition=self.planning,
            objective="o",
        )
        ParticipantLink.objects.create(meeting=m, user=self.user, role="a")
        ParticipantLink.objects.create(meeting=m, user=self.other_user, role="b")

        rows = self._results(self.client.get(self._url()))
        self.assertEqual(sum(1 for r in rows if r["id"] == m.id), 1)

    def test_unknown_meeting_type_slug_returns_empty_list(self):
        Meeting.objects.create(
            project=self.project,
            title="Has planning",
            type_definition=self.planning,
            objective="o",
        )
        r = self.client.get(self._url(meeting_type="nope"))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data["count"], 0)
        self.assertEqual(r.data["results"], [])

    def test_unknown_tag_slug_returns_empty_list(self):
        Meeting.objects.create(
            project=self.project,
            title="Has planning",
            type_definition=self.planning,
            objective="o",
        )
        r = self.client.get(self._url(tag="nope"))
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertEqual(r.data["count"], 0)
        self.assertEqual(r.data["results"], [])

    def test_invalid_ordering_400(self):
        r = self.client.get(self._url(ordering="hacked"))
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("ordering", r.data)

    def test_participant_invalid_400(self):
        r = self.client.get(self._url(participant="x"))
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("participant", r.data)

    def test_page_below_one_400(self):
        r = self.client.get(self._url(page="0"))
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("page", r.data)

    def test_date_from_invalid_400(self):
        r = self.client.get(self._url(date_from="not-a-date"))
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("date_from", r.data)

    def test_date_from_after_date_to_400(self):
        r = self.client.get(
            self._url(date_from="2026-03-01", date_to="2026-01-01")
        )
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("date_to", r.data)

    def test_forbidden_other_project(self):
        r = self.client.get(f"/api/projects/{self.other_project.slug}/meetings/")
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_not_found_unknown_project(self):
        r = self.client.get("/api/projects/999999991/meetings/")
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)

    def test_list_sql_query_count_bounded_no_n_plus_one(self):
        """
        Lightweight performance guard: list serialization must not issue O(n) queries per meeting
        for participants/tags (``for_knowledge_discovery`` prefetch + select_related).
        """
        for i in range(10):
            m = Meeting.objects.create(
                project=self.project,
                title=f"Q{i}",
                type_definition=self.planning,
                objective="o",
            )
            ParticipantLink.objects.create(meeting=m, user=self.user, role="a")
            ParticipantLink.objects.create(meeting=m, user=self.other_user, role="b")
            MeetingTagAssignment.objects.create(
                meeting=m,
                tag_definition=self.tag_strat,
            )

        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(self._url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        n = len(ctx.captured_queries)
        # Without prefetch this would be ~1 + 10*(participants+tags) >> 20. Keep a generous ceiling
        # for auth/session/pagination variance while still catching gross N+1.
        self.assertLessEqual(
            n,
            25,
            f"Too many SQL queries ({n}); possible N+1 on meeting list. "
            f"Ensure list uses meetings_base_queryset_for_project + for_knowledge_discovery.",
        )

    def test_list_row_shape(self):
        m = Meeting.objects.create(
            project=self.project,
            title="Shape",
            type_definition=self.planning,
            objective="obj",
            summary="Sum",
            scheduled_date=date(2026, 1, 1),
            is_archived=False,
        )
        ParticipantLink.objects.create(meeting=m, user=self.user, role="host")
        MeetingTagAssignment.objects.create(meeting=m, tag_definition=self.tag_strat)

        rows = self._results(self.client.get(self._url()))
        row = next(r for r in rows if r["id"] == m.id)
        self.assertEqual(
            set(row.keys()),
            {
                "id",
                "slug",
                "title",
                "summary",
                "scheduled_date",
                "status",
                "meeting_type",
                "meeting_type_slug",
                "participants",
                "tags",
                "decision_count",
                "task_count",
                "generated_decisions_count",
                "generated_tasks_count",
                "generated_decisions",
                "generated_tasks",
                "related_decisions",
                "related_tasks",
                "is_archived",
                "snippet_source",
                "search_snippet",
            },
        )
        self.assertEqual(row["meeting_type"], "Planning")
        self.assertEqual(row["meeting_type_slug"], "planning")
        self.assertEqual(row["participants"], [{"user_id": self.user.id, "role": "host", "username": self.user.username, "avatar": None}])
        self.assertEqual(
            row["tags"],
            [{"slug": "strategy", "label": "Strategy"}],
        )
