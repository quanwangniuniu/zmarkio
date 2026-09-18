"""Contract + access + decoupling tests for spreadsheet.providers.

See backend/agent/README.md for the layering rules these enforce.
"""
import ast
import inspect
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from core.models import Organization, Project, ProjectMember
from core.test_utils import grant_ai_consent
from spreadsheet.models import Cell, Sheet, SheetColumn, SheetRow, Spreadsheet
from spreadsheet.providers import (
    AiAnalysisDisabled,
    SpreadsheetAccessError,
    SpreadsheetDataProvider,
    resolve_cell_value,
)

User = get_user_model()


def _make_user(email):
    return User.objects.create_user(email=email, username=email.split("@")[0], password="pw")


class _SheetBuilder:
    """Create a sheet with (row, col) -> Cell-kwargs, the way real cells look
    after CellService (computed_* / *_value populated, not just raw_input)."""

    def __init__(self, sheet):
        self.sheet = sheet
        self._rows = {}
        self._cols = {}

    def _row(self, pos):
        if pos not in self._rows:
            self._rows[pos], _ = SheetRow.objects.get_or_create(
                sheet=self.sheet, position=pos
            )
        return self._rows[pos]

    def _col(self, pos, name=None):
        if pos not in self._cols:
            self._cols[pos], _ = SheetColumn.objects.get_or_create(
                sheet=self.sheet, position=pos,
                defaults={"name": name or f"C{pos}"},
            )
        return self._cols[pos]

    def cols(self, count):
        for i in range(count):
            self._col(i)
        return self

    def header(self, names):
        for i, name in enumerate(names):
            self._col(i, name)
            Cell.objects.create(
                sheet=self.sheet, row=self._row(0), column=self._cols[i],
                value_type="string", string_value=name, raw_input=name,
                computed_type="string", computed_string=name,
            )
        return self

    def cell(self, row, col, **kwargs):
        Cell.objects.create(
            sheet=self.sheet, row=self._row(row), column=self._col(col), **kwargs
        )
        return self


class SpreadsheetProviderContractTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Org")
        self.owner = _make_user("owner@t.com")
        self.owner.organization = self.org
        self.owner.save()
        self.project = Project.objects.create(
            name="P", organization=self.org, owner=self.owner
        )
        ProjectMember.objects.create(
            user=self.owner, project=self.project, role="owner", is_active=True
        )
        self.spreadsheet = Spreadsheet.objects.create(project=self.project, name="Book")
        grant_ai_consent(self.owner, self.spreadsheet)
        self.sheet = Sheet.objects.create(
            spreadsheet=self.spreadsheet, name="Sheet1", position=0
        )
        (
            _SheetBuilder(self.sheet)
            .header(["Region", "Revenue"])
            .cell(1, 0, value_type="string", string_value="West",
                  computed_type="string", computed_string="West")
            # a computed-number cell: the old agent code compared
            # computed_type == 'NUMBER' (enum value is lowercase 'number') so it
            # was dropped. resolve_cell_value must return 100.0.
            .cell(1, 1, value_type="number",
                  computed_type="number", computed_number=Decimal("100"))
        )
        self.provider = SpreadsheetDataProvider(self.owner)

    def test_payload_shape_matches_contract(self):
        payload = self.provider.get_analysis_payload(self.spreadsheet.id)
        self.assertEqual(set(payload), {"name", "id", "sheets", "truncated"})
        self.assertEqual(payload["id"], self.spreadsheet.id)
        self.assertFalse(payload["truncated"])
        sheet = payload["sheets"][0]
        self.assertEqual(set(sheet), {"id", "name", "columns", "rows", "window"})
        self.assertEqual(sheet["columns"], ["Region", "Revenue"])
        # Behaviour preserved from the old _extract_spreadsheet_data: columns come
        # from SheetColumn.name and the header row (position 0) is included as a
        # data row. The computed-number cell must come back as a float (100.0),
        # not be dropped as it was by the old 'NUMBER' vs 'number' comparison.
        self.assertEqual(
            sheet["rows"],
            [
                {"Region": "Region", "Revenue": "Revenue"},
                {"Region": "West", "Revenue": 100.0},
            ],
        )
        self.assertIsInstance(sheet["rows"][1]["Revenue"], float)

    def test_computed_number_regression(self):
        cell = Cell.objects.get(
            sheet=self.sheet, row__position=1, column__position=1
        )
        self.assertEqual(resolve_cell_value(cell), 100.0)

    def test_sheet_id_filter(self):
        other = Sheet.objects.create(
            spreadsheet=self.spreadsheet, name="Sheet2", position=1
        )
        payload = self.provider.get_analysis_payload(
            self.spreadsheet.id, sheet_id=self.sheet.id
        )
        self.assertEqual([s["id"] for s in payload["sheets"]], [self.sheet.id])
        empty = self.provider.get_analysis_payload(
            self.spreadsheet.id, sheet_id=other.id
        )
        self.assertEqual(empty["sheets"][0]["rows"], [])

    def test_cell_read_is_constant_query_count_not_o_rows(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def count_queries():
            with CaptureQueriesContext(connection) as cq:
                self.provider.get_analysis_payload(self.spreadsheet.id, max_rows=10000)
            return len(cq)

        small = count_queries()
        builder = _SheetBuilder(self.sheet)
        for r in range(2, 60):
            builder.cell(r, 0, value_type="string", string_value=f"r{r}",
                         computed_type="string", computed_string=f"r{r}")
        large = count_queries()
        # One bulk Cell query per sheet: adding 58 rows must not add queries.
        self.assertEqual(small, large)

    def test_row_cap_sets_truncated(self):
        builder = _SheetBuilder(self.sheet)
        for r in range(2, 20):
            builder.cell(r, 0, value_type="string", string_value=f"r{r}",
                         computed_type="string", computed_string=f"r{r}")
        payload = self.provider.get_analysis_payload(self.spreadsheet.id, max_rows=5)
        self.assertTrue(payload["truncated"])
        self.assertTrue(payload["sheets"][0]["window"]["row_limited"])
        self.assertLessEqual(len(payload["sheets"][0]["rows"]), 5)


class SpreadsheetProviderAccessTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Org")
        self.owner = _make_user("owner2@t.com")
        self.owner.organization = self.org
        self.owner.save()
        self.project = Project.objects.create(
            name="P2", organization=self.org, owner=self.owner
        )
        ProjectMember.objects.create(
            user=self.owner, project=self.project, role="owner", is_active=True
        )
        self.spreadsheet = Spreadsheet.objects.create(project=self.project, name="B")
        grant_ai_consent(self.owner, self.spreadsheet)
        Sheet.objects.create(spreadsheet=self.spreadsheet, name="S", position=0)

    def test_owner_ok(self):
        payload = SpreadsheetDataProvider(self.owner).get_analysis_payload(
            self.spreadsheet.id
        )
        self.assertEqual(payload["id"], self.spreadsheet.id)

    def test_active_member_ok(self):
        member = _make_user("member@t.com")
        member.organization = self.org
        member.save()
        ProjectMember.objects.create(
            user=member, project=self.project, role="member", is_active=True
        )
        grant_ai_consent(member, self.spreadsheet)
        payload = SpreadsheetDataProvider(member).get_analysis_payload(
            self.spreadsheet.id
        )
        self.assertEqual(payload["id"], self.spreadsheet.id)

    def test_consent_required(self):
        fresh = _make_user("fresh@t.com")
        fresh.organization = self.org
        fresh.save()
        ProjectMember.objects.create(
            user=fresh, project=self.project, role="member", is_active=True
        )
        with self.assertRaises(AiAnalysisDisabled) as ctx:
            SpreadsheetDataProvider(fresh).get_analysis_payload(self.spreadsheet.id)
        self.assertEqual(ctx.exception.code, "AI_CONSENT_REQUIRED")
        self.assertEqual(ctx.exception.spreadsheet_id, self.spreadsheet.id)

    def test_consent_is_per_spreadsheet_not_per_project(self):
        """Consenting to one spreadsheet does not consent to another in the
        same project."""
        other = Spreadsheet.objects.create(project=self.project, name="B2")
        Sheet.objects.create(spreadsheet=other, name="S", position=0)
        # self.owner already consented to self.spreadsheet in setUp.
        SpreadsheetDataProvider(self.owner).get_analysis_payload(self.spreadsheet.id)
        with self.assertRaises(AiAnalysisDisabled) as ctx:
            SpreadsheetDataProvider(self.owner).get_analysis_payload(other.id)
        self.assertEqual(ctx.exception.code, "AI_CONSENT_REQUIRED")

    def test_non_member_raises(self):
        outsider = _make_user("outsider@t.com")
        with self.assertRaises(SpreadsheetAccessError):
            SpreadsheetDataProvider(outsider).get_analysis_payload(self.spreadsheet.id)

    def test_inactive_member_raises(self):
        ex = _make_user("ex@t.com")
        ex.organization = self.org
        ex.save()
        ProjectMember.objects.create(
            user=ex, project=self.project, role="member", is_active=False
        )
        with self.assertRaises(SpreadsheetAccessError):
            SpreadsheetDataProvider(ex).get_analysis_payload(self.spreadsheet.id)

    @override_settings(AGENT_SPREADSHEET_AI_ENABLED=False)
    def test_global_flag_off(self):
        with self.assertRaises(AiAnalysisDisabled) as ctx:
            SpreadsheetDataProvider(self.owner).get_analysis_payload(self.spreadsheet.id)
        self.assertEqual(ctx.exception.code, "AI_DISABLED_GLOBAL")


class OrchestratorThroughProviderTests(TestCase):
    """The agent orchestrator reaches spreadsheet data only via the provider."""

    def setUp(self):
        from agent.models import AgentSession

        self.org = Organization.objects.create(name="Org")
        self.user = _make_user("orch@t.com")
        self.user.organization = self.org
        self.user.save()
        self.project = Project.objects.create(
            name="OP", organization=self.org, owner=self.user
        )
        ProjectMember.objects.create(
            user=self.user, project=self.project, role="owner", is_active=True
        )
        self.spreadsheet = Spreadsheet.objects.create(project=self.project, name="Bk")
        grant_ai_consent(self.user, self.spreadsheet)
        sheet = Sheet.objects.create(
            spreadsheet=self.spreadsheet, name="S1", position=0
        )
        _SheetBuilder(sheet).header(["A"]).cell(
            1, 0, value_type="string", string_value="x",
            computed_type="string", computed_string="x",
        )
        self.session = AgentSession.objects.create(user=self.user, project=self.project)

    def _run(self):
        from agent.services import AgentOrchestrator

        orch = AgentOrchestrator(self.user, self.project, self.session)
        return list(orch.analyze_spreadsheet(self.spreadsheet.id))

    @patch("agent.services._run_analysis")
    def test_analyze_spreadsheet_end_to_end(self, mock_run):
        from agent.models import AgentWorkflowRun

        mock_run.return_value = {"anomalies": [], "recommended_tasks": []}
        chunks = self._run()

        self.assertTrue(any(c["type"] == "analysis" for c in chunks))
        payload = mock_run.call_args.args[0]
        self.assertEqual(payload["id"], self.spreadsheet.id)  # came from the provider
        run = AgentWorkflowRun.objects.get(session=self.session)
        self.assertEqual(run.spreadsheet_id, self.spreadsheet.id)

    @patch("agent.services._run_analysis")
    def test_analyze_spreadsheet_denied_for_non_member(self, mock_run):
        outsider = _make_user("nope@t.com")
        from agent.models import AgentSession
        from agent.services import AgentOrchestrator

        session = AgentSession.objects.create(user=outsider, project=self.project)
        orch = AgentOrchestrator(outsider, self.project, session)
        chunks = list(orch.analyze_spreadsheet(self.spreadsheet.id))
        self.assertTrue(any(c["type"] == "error" for c in chunks))
        mock_run.assert_not_called()

    @override_settings(AGENT_SPREADSHEET_AI_ENABLED=False)
    @patch("agent.services._run_analysis")
    def test_analyze_spreadsheet_blocked_when_flag_off(self, mock_run):
        chunks = self._run()
        err = [c for c in chunks if c["type"] == "error"]
        self.assertTrue(err)
        self.assertEqual(err[0]["data"]["code"], "AI_DISABLED_GLOBAL")
        mock_run.assert_not_called()


def _imported_top_modules(path):
    """Top-level package names this module imports, from its AST."""
    tree = ast.parse(Path(path).read_text())
    mods = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.add(node.module.split(".")[0])
    return mods


class DecouplingGuardTests(TestCase):
    def test_agent_services_has_no_spreadsheet_orm_import(self):
        import agent.services

        src = Path(inspect.getfile(agent.services)).read_text()
        self.assertNotIn("from spreadsheet.models", src)
        self.assertNotIn("import spreadsheet.models", src)
        self.assertNotIn("_extract_spreadsheet_data", src)

    def test_spreadsheet_package_does_not_import_agent(self):
        import spreadsheet.import_service
        import spreadsheet.nl_pattern_service
        import spreadsheet.nl_pivot_service
        import spreadsheet.providers

        for mod in (
            spreadsheet.providers,
            spreadsheet.import_service,
            spreadsheet.nl_pattern_service,
            spreadsheet.nl_pivot_service,
        ):
            self.assertNotIn(
                "agent",
                _imported_top_modules(inspect.getfile(mod)),
                f"{mod.__name__} imports agent",
            )
