"""Narrow, access-checked facade over spreadsheet data for external consumers.

The AI agent must not import ``spreadsheet.models`` or walk the spreadsheet ORM.
It depends only on this module. See ``backend/agent/README.md`` for the layering
contract.

Responsibilities kept here (not in the agent):
    * access control  -> ``spreadsheet.access.accessible_spreadsheets`` (owner OR
      active member OR org-admin)
    * feature gating  -> ``check_ai_analysis_enabled`` (global kill-switch +
      per-project toggle)
    * cell-value resolution -> ``resolve_cell_value`` (one precedence, mirrors
      ``xlsx_export._cell_export_value``)
    * windowed reads with hard row/column/cell caps
"""
from __future__ import annotations

import logging
from typing import Any

from django.conf import settings

from .access import accessible_spreadsheets
from .models import Cell, ComputedCellType

logger = logging.getLogger(__name__)

# Hard caps on what is handed to an LLM. Overridable via settings (Commit 4 adds
# the documented defaults); ``getattr`` keeps this import-safe until then.
_DEFAULT_MAX_ROWS = 500
_DEFAULT_MAX_COLS = 50
_DEFAULT_MAX_CELLS = 20000


def _limit(name: str, default: int) -> int:
    value = getattr(settings, name, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class SpreadsheetAccessError(Exception):
    """The user cannot access the requested spreadsheet/sheet (caller -> 404)."""


class AiAnalysisDisabled(Exception):
    """AI analysis is turned off globally or for this project (caller -> 403 / SSE error).

    ``spreadsheet_id`` is set for the ``AI_CONSENT_REQUIRED`` case so the caller
    can tell the client which spreadsheet the one-time consent prompt is for.
    """

    def __init__(self, code: str, message: str, *, spreadsheet_id=None):
        self.code = code
        self.message = message
        self.spreadsheet_id = spreadsheet_id
        super().__init__(message)


def resolve_cell_value(cell: Cell) -> Any:
    """The one effective scalar value for a cell.

    Precedence mirrors ``spreadsheet.xlsx_export._cell_export_value`` (the most
    complete of the previously-divergent copies). Fixes the historical bug where
    ``agent.services`` compared ``computed_type == 'NUMBER'`` (the enum value is
    lowercase ``'number'``), so numeric computed cells fell through to strings.
    """
    if cell.computed_type == ComputedCellType.NUMBER and cell.computed_number is not None:
        return float(cell.computed_number)
    if cell.number_value is not None:
        return float(cell.number_value)
    if cell.computed_type == ComputedCellType.STRING and cell.computed_string is not None:
        return cell.computed_string
    if cell.string_value is not None:
        return cell.string_value
    if cell.boolean_value is not None:
        return cell.boolean_value
    return None


def check_ai_analysis_enabled(project, user=None, *, spreadsheet=None) -> None:
    """Raise :class:`AiAnalysisDisabled` when AI analysis must not run.

    In order: global kill-switch (``settings.AGENT_SPREADSHEET_AI_ENABLED``),
    per-project toggle (``Project.ai_analysis_enabled``), then — when both *user*
    and *spreadsheet* are given — that user's one-time consent for that
    spreadsheet (:class:`~spreadsheet.models.SpreadsheetAiConsent`).

    Entry points that already have a spreadsheet pass it so the consent step is
    enforced. The upload path has no spreadsheet yet (the file becomes one in the
    same request); it omits *spreadsheet* here and records consent for the new
    spreadsheet itself (see :meth:`SpreadsheetDataProvider.create_from_parsed_upload`).
    """
    if not getattr(settings, "AGENT_SPREADSHEET_AI_ENABLED", True):
        raise AiAnalysisDisabled(
            "AI_DISABLED_GLOBAL", "AI analysis is currently disabled."
        )
    if project is not None and not getattr(project, "ai_analysis_enabled", True):
        raise AiAnalysisDisabled(
            "AI_DISABLED_PROJECT", "AI analysis is disabled for this project."
        )
    if user is not None and spreadsheet is not None:
        require_ai_consent(user, spreadsheet)


def _spreadsheet_pk(spreadsheet) -> int:
    """Accept either a ``Spreadsheet`` instance or a bare pk."""
    return getattr(spreadsheet, "pk", spreadsheet)


def has_ai_consent(user, spreadsheet) -> bool:
    from .models import SpreadsheetAiConsent

    return SpreadsheetAiConsent.objects.filter(
        user=user, spreadsheet_id=_spreadsheet_pk(spreadsheet)
    ).exists()


def require_ai_consent(user, spreadsheet) -> None:
    """Raise ``AiAnalysisDisabled('AI_CONSENT_REQUIRED', …)`` until this user has
    consented to sending *this spreadsheet's* data to an external AI service."""
    if not has_ai_consent(user, spreadsheet):
        raise AiAnalysisDisabled(
            "AI_CONSENT_REQUIRED",
            "Enable AI analysis for this spreadsheet to continue.",
            spreadsheet_id=_spreadsheet_pk(spreadsheet),
        )


def record_ai_consent(user, spreadsheet):
    """Idempotently record *user*'s one-time consent for *spreadsheet*."""
    from .models import SpreadsheetAiConsent

    obj, _ = SpreadsheetAiConsent.objects.get_or_create(
        user=user, spreadsheet_id=_spreadsheet_pk(spreadsheet)
    )
    return obj


class SpreadsheetDataProvider:
    """Everything the agent is allowed to do with spreadsheet data, for one user."""

    def __init__(self, user):
        self.user = user

    # -- internal ---------------------------------------------------------------
    def _get_or_raise(self, spreadsheet_id):
        obj = (
            accessible_spreadsheets(self.user)
            .filter(id=spreadsheet_id)
            .first()
        )
        if obj is None:
            raise SpreadsheetAccessError(str(spreadsheet_id))
        return obj

    def accessible_spreadsheet_id(self, spreadsheet_id):
        """Return *spreadsheet_id* if the user may access it, else ``None``.

        Cheap existence/access check (no cell read) for callers that only need
        to attach the FK, e.g. ``AgentWorkflowRun.spreadsheet``.
        """
        if not spreadsheet_id:
            return None
        exists = (
            accessible_spreadsheets(self.user)
            .filter(id=spreadsheet_id)
            .exists()
        )
        return spreadsheet_id if exists else None

    # -- read -----------------------------------------------------------------
    def get_analysis_payload(
        self,
        spreadsheet_id,
        *,
        sheet_id=None,
        max_rows: int | None = None,
        max_cols: int | None = None,
        max_cells: int | None = None,
    ) -> dict:
        """Access-checked, feature-gated, windowed read of a spreadsheet.

        Returns the dict contract shared with
        ``core.services.file_parser.parse_file_to_json``::

            {"name": str, "id": int, "truncated": bool,
             "sheets": [{"id": int, "name": str, "columns": [str],
                         "rows": [{col_name: scalar}], "window": {...}}]}
        """
        max_rows = max_rows or _limit("SPREADSHEET_AI_MAX_ROWS", _DEFAULT_MAX_ROWS)
        max_cols = max_cols or _limit("SPREADSHEET_AI_MAX_COLS", _DEFAULT_MAX_COLS)
        max_cells = max_cells or _limit("SPREADSHEET_AI_MAX_CELLS", _DEFAULT_MAX_CELLS)

        spreadsheet = self._get_or_raise(spreadsheet_id)
        check_ai_analysis_enabled(
            spreadsheet.project, user=self.user, spreadsheet=spreadsheet
        )

        sheets_qs = spreadsheet.sheets.filter(is_deleted=False).order_by("position")
        if sheet_id is not None:
            sheets_qs = sheets_qs.filter(id=sheet_id)

        out_sheets: list[dict] = []
        cell_budget = max_cells
        for sheet in sheets_qs:
            all_cols = list(
                sheet.columns.filter(is_deleted=False).order_by("position")
            )
            col_limited = len(all_cols) > max_cols
            cols = all_cols[:max_cols]
            col_name_by_id = {c.id: (c.name or f"col_{c.id}") for c in cols}

            rows = list(
                sheet.rows.filter(is_deleted=False).order_by("position")[: max_rows + 1]
            )
            row_limited = len(rows) > max_rows
            rows = rows[:max_rows]
            row_ids = [r.id for r in rows]

            # One bulk query per sheet (replaces the per-row Cell.objects.filter
            # loop in the old _extract_spreadsheet_data and the N+1 in
            # pivot_service).
            buckets: dict[int, dict] = {}
            returned = 0
            if row_ids and col_name_by_id:
                cells = (
                    Cell.objects.filter(
                        sheet=sheet,
                        row_id__in=row_ids,
                        column_id__in=list(col_name_by_id.keys()),
                        is_deleted=False,
                    )
                    .only(
                        "row_id",
                        "column_id",
                        "computed_type",
                        "computed_number",
                        "computed_string",
                        "string_value",
                        "number_value",
                        "boolean_value",
                    )
                )
                for cell in cells:
                    if cell_budget <= 0:
                        row_limited = True
                        break
                    value = resolve_cell_value(cell)
                    if value is None:
                        continue
                    buckets.setdefault(cell.row_id, {})[
                        col_name_by_id[cell.column_id]
                    ] = value
                    returned += 1
                    cell_budget -= 1

            row_dicts = [buckets[r.id] for r in rows if buckets.get(r.id)]
            out_sheets.append(
                {
                    "id": sheet.id,
                    "name": sheet.name,
                    "columns": [col_name_by_id[c.id] for c in cols],
                    "rows": row_dicts,
                    "window": {
                        "row_limited": row_limited,
                        "col_limited": col_limited,
                        "cells_returned": returned,
                        "max_rows": max_rows,
                    },
                }
            )

        return {
            "name": spreadsheet.name,
            "id": spreadsheet.id,
            "sheets": out_sheets,
            "truncated": any(
                s["window"]["row_limited"] or s["window"]["col_limited"]
                for s in out_sheets
            ),
        }

    # -- list ---------------------------------------------------------------
    def list_project_spreadsheets(self, project) -> list[dict]:
        rows = (
            accessible_spreadsheets(self.user)
            .filter(project=project)
            .values("id", "name", "created_at")
            .order_by("-created_at")
        )
        return [
            {**row, "project_id": project.id, "updated_at": row["created_at"]}
            for row in rows
        ]

    # -- write (upload import) --------------------------------------------------
    def create_from_parsed_upload(
        self, project, parsed: dict, original_filename: str
    ) -> dict:
        # No spreadsheet exists yet, so only the global + per-project gates run
        # here; consent is recorded for the spreadsheet this call creates.
        check_ai_analysis_enabled(project, user=self.user)
        from .import_service import create_spreadsheet_from_upload

        result = create_spreadsheet_from_upload(
            project=project,
            parsed=parsed,
            original_filename=original_filename,
        )
        # Uploading a file through the "analyze with AI" flow is itself the
        # user's one-time consent for the resulting spreadsheet.
        record_ai_consent(self.user, result["spreadsheet_id"])
        return result
