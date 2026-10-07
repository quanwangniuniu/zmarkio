"""Audit + billing + consent behaviour on the agent spreadsheet-analysis paths."""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import AuditEvent, Organization, Project, ProjectMember
from core.test_utils import grant_ai_consent
from spreadsheet.models import Cell, Sheet, SheetColumn, SheetRow, Spreadsheet
from stripe_meta.exceptions import QuotaError

User = get_user_model()


class SpreadsheetInsightsSecurityTests(TestCase):
    def setUp(self):
        from agent.models import AgentSession

        self.org = Organization.objects.create(name="SecOrg")
        self.user = User.objects.create_user(
            email="sec@t.com", username="sec", password="pw"
        )
        self.user.organization = self.org
        self.user.save()
        self.project = Project.objects.create(
            name="SecP", organization=self.org, owner=self.user
        )
        ProjectMember.objects.create(
            user=self.user, project=self.project, role="owner", is_active=True
        )
        self.session = AgentSession.objects.create(
            user=self.user, project=self.project
        )
        self.spreadsheet = Spreadsheet.objects.create(
            project=self.project, name="Bk"
        )
        sheet = Sheet.objects.create(
            spreadsheet=self.spreadsheet, name="S", position=0
        )
        col = SheetColumn.objects.create(sheet=sheet, name="A", position=0)
        r0 = SheetRow.objects.create(sheet=sheet, position=0)
        Cell.objects.create(
            sheet=sheet, row=r0, column=col, value_type="string",
            string_value="A", computed_type="string", computed_string="A",
        )
        self.sheet_id = sheet.id

    def _orch(self):
        from agent.services import AgentOrchestrator

        return AgentOrchestrator(self.user, self.project, self.session)

    def _run_insights(self):
        return list(
            self._orch().analyze_spreadsheet_insights(
                self.spreadsheet.id, sheet_id=self.sheet_id
            )
        )

    def test_consent_required_before_insights(self):
        chunks = self._run_insights()
        err = [c for c in chunks if c["type"] == "error"]
        self.assertTrue(err)
        self.assertEqual(err[0]["data"]["code"], "AI_CONSENT_REQUIRED")
        self.assertEqual(err[0]["data"]["spreadsheet_id"], self.spreadsheet.id)

    def test_insights_allowed_after_per_spreadsheet_consent(self):
        grant_ai_consent(self.user, self.spreadsheet)
        # Stub the key check so CI without GEMINI_API_KEY still reaches the mocked LLM.
        with patch("core.services.gemini_client._get_api_key", return_value="test-key"), patch(
            "agent.services._call_gemini_spreadsheet_insights"
        ) as mock_call:
            mock_call.return_value = {
                "summary": "ok", "recommendations": [], "anomalies": [],
                "recommended_tasks": [],
            }
            chunks = self._run_insights()
        self.assertFalse([c for c in chunks if c["type"] == "error"])

    @patch("core.services.gemini_client._get_api_key", return_value="test-key")
    @patch("agent.services._call_gemini_spreadsheet_insights")
    def test_insights_routes_through_call_llm_and_audits(self, mock_call, _mock_key):
        grant_ai_consent(self.user, self.spreadsheet)
        mock_call.return_value = {
            "summary": "ok", "recommendations": [], "anomalies": [],
            "recommended_tasks": [],
        }
        with self.captureOnCommitCallbacks(execute=True):
            chunks = self._run_insights()

        # agent_session threaded through to the billed path
        self.assertEqual(
            mock_call.call_args.kwargs.get("agent_session"), self.session
        )
        self.assertTrue(any(c["type"] == "spreadsheet_summary" for c in chunks))
        self.assertTrue(
            AuditEvent.objects.filter(
                event_type="agent.spreadsheet.insights_analyzed"
            ).exists()
        )

    @patch("agent.services._run_spreadsheet_insights")
    def test_quota_error_surfaces_not_masked(self, mock_run):
        grant_ai_consent(self.user, self.spreadsheet)
        mock_run.side_effect = QuotaError(
            code="TOKEN_QUOTA_EXCEEDED", message="Monthly quota exceeded."
        )
        with self.assertRaises(QuotaError):
            self._run_insights()

    @patch("agent.services._run_spreadsheet_insights")
    def test_audit_context_has_no_cell_values(self, mock_run):
        grant_ai_consent(self.user, self.spreadsheet)
        mock_run.return_value = {
            "summary": "ok", "recommendations": [], "anomalies": [],
            "recommended_tasks": [],
        }
        with self.captureOnCommitCallbacks(execute=True):
            self._run_insights()
        ev = AuditEvent.objects.filter(
            event_type="agent.spreadsheet.insights_analyzed"
        ).latest("occurred_at")
        self.assertEqual(ev.context["sheet_id"], self.sheet_id)
        self.assertIn("rows_sent", ev.context)
        self.assertNotIn("rows", ev.context)
        self.assertNotIn("cells", ev.context)
