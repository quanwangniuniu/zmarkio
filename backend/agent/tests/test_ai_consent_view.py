"""GET/POST /api/agent/ai-consent/ — per-spreadsheet AI-analysis consent."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from core.models import AuditEvent, Organization, Project, ProjectMember
from spreadsheet.models import Sheet, Spreadsheet, SpreadsheetAiConsent

User = get_user_model()

URL = "/api/agent/ai-consent/"


class AiConsentViewTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Org")
        self.user = User.objects.create_user(
            email="u@t.com", username="u", password="pw"
        )
        self.user.organization = self.org
        self.user.save()
        self.project = Project.objects.create(
            name="P", organization=self.org, owner=self.user
        )
        ProjectMember.objects.create(
            user=self.user, project=self.project, role="owner", is_active=True
        )
        self.spreadsheet = Spreadsheet.objects.create(project=self.project, name="B")
        self.sheet = Sheet.objects.create(
            spreadsheet=self.spreadsheet, name="S", position=0
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_get_reports_not_consented_then_consented(self):
        resp = self.client.get(URL, {"spreadsheet_id": self.spreadsheet.id})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["consented"])
        self.assertIsNone(resp.json()["consented_at"])

        SpreadsheetAiConsent.objects.create(
            user=self.user, spreadsheet=self.spreadsheet
        )
        resp = self.client.get(URL, {"spreadsheet_id": self.spreadsheet.id})
        self.assertTrue(resp.json()["consented"])
        self.assertIsNotNone(resp.json()["consented_at"])

    def test_post_records_consent_idempotently(self):
        resp = self.client.post(URL, {"spreadsheet_id": self.spreadsheet.id}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["consented"])
        self.assertEqual(
            SpreadsheetAiConsent.objects.filter(
                user=self.user, spreadsheet=self.spreadsheet
            ).count(),
            1,
        )
        # second call is a no-op, still 200
        resp = self.client.post(URL, {"spreadsheet_id": self.spreadsheet.id}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            SpreadsheetAiConsent.objects.filter(
                user=self.user, spreadsheet=self.spreadsheet
            ).count(),
            1,
        )

    def test_post_emits_audit_event_once(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(URL, {"spreadsheet_id": self.spreadsheet.id}, format="json")
            self.client.post(URL, {"spreadsheet_id": self.spreadsheet.id}, format="json")
        events = AuditEvent.objects.filter(
            event_type="agent.spreadsheet.ai_consent_granted"
        )
        self.assertEqual(events.count(), 1)
        self.assertEqual(str(events.first().target_id), str(self.spreadsheet.id))

    def test_unknown_or_inaccessible_spreadsheet_404(self):
        self.assertEqual(self.client.get(URL, {"spreadsheet_id": 999999}).status_code, 404)
        self.assertEqual(self.client.post(URL, {}, format="json").status_code, 404)

        outsider = User.objects.create_user(email="x@t.com", username="x", password="pw")
        other_client = APIClient()
        other_client.force_authenticate(user=outsider)
        resp = other_client.post(
            URL, {"spreadsheet_id": self.spreadsheet.id}, format="json"
        )
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(
            SpreadsheetAiConsent.objects.filter(spreadsheet=self.spreadsheet).exists()
        )

    def test_target_by_sheet_id(self):
        resp = self.client.get(URL, {"sheet_id": self.sheet.id})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["consented"])

        resp = self.client.post(URL, {"sheet_id": self.sheet.id}, format="json")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(
            SpreadsheetAiConsent.objects.filter(
                user=self.user, spreadsheet=self.spreadsheet
            ).exists()
        )
        # now the spreadsheet-id form sees it as consented
        resp = self.client.get(URL, {"spreadsheet_id": self.spreadsheet.id})
        self.assertTrue(resp.json()["consented"])

    def test_requires_auth(self):
        anon = APIClient()
        self.assertIn(
            anon.get(URL, {"spreadsheet_id": self.spreadsheet.id}).status_code,
            (401, 403),
        )
