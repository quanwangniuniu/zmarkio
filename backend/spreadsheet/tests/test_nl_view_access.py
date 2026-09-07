"""Access control + feature-gate + audit on the NL->config spreadsheet views."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from core.models import AuditEvent, Organization, Project, ProjectMember
from core.test_utils import grant_ai_consent
from spreadsheet.models import Cell, Sheet, SheetColumn, SheetRow, Spreadsheet

User = get_user_model()

PATTERN_URL = "/api/spreadsheet/sheets/{}/generate-pattern-steps/"
PIVOT_URL = "/api/spreadsheet/sheets/{}/generate-pivot-config/"


def _user(email, org):
    u = User.objects.create_user(email=email, username=email.split("@")[0], password="pw")
    u.organization = org
    u.save()
    return u


class NlViewAccessTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Org")
        self.owner = _user("owner@t.com", self.org)
        self.project = Project.objects.create(
            name="P", organization=self.org, owner=self.owner
        )
        ProjectMember.objects.create(
            user=self.owner, project=self.project, role="owner", is_active=True
        )
        self.spreadsheet = Spreadsheet.objects.create(project=self.project, name="B")
        grant_ai_consent(self.owner, self.spreadsheet)
        self.sheet = Sheet.objects.create(
            spreadsheet=self.spreadsheet, name="S", position=0
        )
        col = SheetColumn.objects.create(sheet=self.sheet, name="A", position=0)
        row = SheetRow.objects.create(sheet=self.sheet, position=0)
        Cell.objects.create(
            sheet=self.sheet, row=row, column=col, raw_input="Region",
            value_type="string", string_value="Region",
        )

    def _client(self, user):
        c = APIClient()
        c.force_authenticate(user=user)
        return c

    def _post_pattern(self, user):
        return self._client(user).post(
            PATTERN_URL.format(self.sheet.id), {"instruction": "x"}, format="json"
        )

    @patch("spreadsheet.nl_pattern_service.generate_pattern_steps", return_value=[])
    def test_owner_ok(self, _m):
        self.assertEqual(self._post_pattern(self.owner).status_code, 200)

    @patch("spreadsheet.nl_pattern_service.generate_pattern_steps", return_value=[])
    def test_active_member_ok(self, _m):
        member = _user("m@t.com", self.org)
        ProjectMember.objects.create(
            user=member, project=self.project, role="member", is_active=True
        )
        grant_ai_consent(member, self.spreadsheet)
        self.assertEqual(self._post_pattern(member).status_code, 200)

    def test_non_member_404(self):
        self.assertEqual(self._post_pattern(_user("x@t.com", self.org)).status_code, 404)

    def test_inactive_member_404(self):
        ex = _user("ex@t.com", self.org)
        ProjectMember.objects.create(
            user=ex, project=self.project, role="member", is_active=False
        )
        self.assertEqual(self._post_pattern(ex).status_code, 404)

    def test_consent_required_403(self):
        fresh = _user("fresh@t.com", self.org)
        ProjectMember.objects.create(
            user=fresh, project=self.project, role="member", is_active=True
        )
        resp = self._post_pattern(fresh)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["code"], "AI_CONSENT_REQUIRED")
        self.assertEqual(resp.json()["spreadsheet_id"], self.spreadsheet.id)

    @patch("spreadsheet.nl_pattern_service.generate_pattern_steps", return_value=[])
    def test_consent_is_per_spreadsheet(self, _m):
        """Consent granted for one spreadsheet does not unlock another."""
        other = Spreadsheet.objects.create(project=self.project, name="B2")
        other_sheet = Sheet.objects.create(
            spreadsheet=other, name="S", position=0
        )
        # self.owner consented to self.spreadsheet in setUp, not to `other`.
        resp = self._client(self.owner).post(
            PATTERN_URL.format(other_sheet.id), {"instruction": "x"}, format="json"
        )
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["code"], "AI_CONSENT_REQUIRED")

    @override_settings(AGENT_SPREADSHEET_AI_ENABLED=False)
    def test_global_flag_off_403(self):
        resp = self._post_pattern(self.owner)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["code"], "AI_DISABLED_GLOBAL")

    def test_project_flag_off_403(self):
        self.project.ai_analysis_enabled = False
        self.project.save(update_fields=["ai_analysis_enabled"])
        resp = self._post_pattern(self.owner)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()["code"], "AI_DISABLED_PROJECT")

    @patch("spreadsheet.nl_pattern_service.generate_pattern_steps", return_value=[])
    def test_audit_row_written_without_content(self, _m):
        with self.captureOnCommitCallbacks(execute=True):
            self._post_pattern(self.owner)
        ev = AuditEvent.objects.filter(
            event_type="spreadsheet.nl_pattern.generated"
        ).latest("occurred_at")
        self.assertEqual(ev.actor_id, self.owner.id)
        self.assertEqual(str(ev.target_id), str(self.spreadsheet.id))
        self.assertIn("instruction_chars", ev.context)
        self.assertNotIn("instruction", ev.context)
        self.assertNotIn("rows", ev.context)

    @patch("spreadsheet.nl_pivot_service.generate_pivot_config", return_value={})
    def test_pivot_view_same_gate(self, _m):
        resp = self._client(_user("y@t.com", self.org)).post(
            PIVOT_URL.format(self.sheet.id), {"instruction": "x"}, format="json"
        )
        self.assertEqual(resp.status_code, 404)
