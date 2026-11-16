import uuid
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from django.core.files.base import ContentFile
from django.test import override_settings
import tempfile
import io

import pytest
import requests
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from google_docs_integration.google_sheets_patch_export import (
    GoogleBlankTabCleanupError,
    GooglePatchExportError,
    export_copied_google_sheet,
)
from google_docs_integration.google_sheets_patch_export import _format_body, stored_google_formats
from google_docs_integration.models import GoogleDocsConnection
from google_docs_integration.services import GOOGLE_SHEETS_URL, run_google_api_with_token_retry
from spreadsheet.import_service import build_import_baseline
from spreadsheet.models import (
    Cell,
    SheetColumn,
    SheetImportSource,
    SheetRow,
    SheetStructureOperation,
    SpreadsheetCellFormat,
    SpreadsheetHighlight,
)
from spreadsheet.services import SheetService
from spreadsheet.tests.test_services import (
    create_test_organization,
    create_test_project,
    create_test_sheet,
    create_test_spreadsheet,
    create_test_user,
)

SHEETS_EXPORT_URL = "/api/google-docs/sheets/export/"
SOURCE_SPREADSHEET_ID = "source-sheet"
DESTINATION_ID = "dest-sheet"
BLANK_SHEET_ID = 11
COPIED_SHEET_ID = 22


def _safe_diff(**overrides):
    diff = {
        "requires_rebuild": False,
        "has_changes": False,
        "cells": {},
        "formats": {},
        "highlights": {"cells": {}},
    }
    diff.update(overrides)
    return diff


def _source(sheet_id=0, worksheet_name="Budget"):
    return SimpleNamespace(
        google_spreadsheet_id=SOURCE_SPREADSHEET_ID,
        google_sheet_id=sheet_id,
        worksheet_name=worksheet_name,
    )


def _ok(payload):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def _http_error(status_code):
    response = MagicMock()
    response.status_code = status_code
    error = requests.HTTPError(str(status_code))
    error.response = response
    response.raise_for_status.side_effect = error
    return response


def _created_payload():
    return {
        "spreadsheetId": DESTINATION_ID,
        "sheets": [{"properties": {"sheetId": BLANK_SHEET_ID}}],
    }


def _copy_payload(title="Budget"):
    return {"sheetId": COPIED_SHEET_ID, "title": title}


@pytest.fixture
def sheet_setup(db):
    suffix = uuid.uuid4().hex[:8]
    user = create_test_user(username=f"p5_{suffix}", email=f"p5_{suffix}@test.com")
    organization = create_test_organization(name=f"Phase 5 {suffix}")
    project = create_test_project(organization, owner=user)
    spreadsheet = create_test_spreadsheet(project, name=f"Budget {suffix}")
    sheet = create_test_sheet(spreadsheet, name="Sheet 1")
    connection = GoogleDocsConnection(user=user, google_email="user@gmail.com", is_active=True)
    connection.set_access_token("token-abc")
    connection.set_refresh_token("refresh-abc")
    connection.save()
    client = APIClient()
    client.force_authenticate(user=user)
    return SimpleNamespace(
        user=user,
        project=project,
        spreadsheet=spreadsheet,
        sheet=sheet,
        client=client,
        connection=connection,
    )


def _google_source(sheet, **overrides):
    fields = {
        "sheet": sheet,
        "origin": SheetImportSource.Origin.GOOGLE,
        "worksheet_name": "Budget",
        "google_spreadsheet_id": SOURCE_SPREADSHEET_ID,
        "google_sheet_id": 0,
    }
    fields.update(overrides)
    return SheetImportSource.objects.create(**fields)


def _post_export(setup, sheet_id=None, spreadsheet_id=None):
    return setup.client.post(
        SHEETS_EXPORT_URL,
        {
            "title": "Export",
            "matrix": [["a"]],
            "formats": [],
            "spreadsheet_id": setup.spreadsheet.slug if spreadsheet_id is None else spreadsheet_id,
            "sheet_id": setup.sheet.id if sheet_id is None else sheet_id,
        },
        format="json",
    )


@contextmanager
def _routed_export(diff=None, copy_error=None):
    def invoke(connection, fn):
        return fn("token")

    rebuild_result = {
        "spreadsheet_id": "rebuilt",
        "url": "https://docs.google.com/spreadsheets/d/rebuilt/edit",
    }
    copy_result = {
        "spreadsheet_id": "copied",
        "url": "https://docs.google.com/spreadsheets/d/copied/edit",
    }
    with patch(
        "google_docs_integration.views.run_google_api_with_token_retry",
        side_effect=invoke,
    ), patch(
        "google_docs_integration.views.export_to_google_sheet",
        return_value=rebuild_result,
    ) as rebuild, patch(
        "google_docs_integration.views.export_copied_google_sheet",
        return_value=copy_result,
    ) as copy, patch(
        "google_docs_integration.views.diff_sheet",
        return_value=diff or _safe_diff(),
    ):
        if copy_error is not None:
            copy.side_effect = copy_error
        yield SimpleNamespace(rebuild=rebuild, copy=copy)


@pytest.mark.django_db
class TestGoogleSheetExportRouting:
    @pytest.mark.parametrize(
        "source_fields, diff",
        [
            (None, None),
            ({"origin": SheetImportSource.Origin.EXCEL, "google_spreadsheet_id": "", "google_sheet_id": None}, None),
            ({"google_spreadsheet_id": "  "}, None),
            ({"google_sheet_id": None}, None),
            ({}, _safe_diff(requires_rebuild=True, has_changes=True)),
        ],
        ids=["no_source", "excel_without_file", "blank_spreadsheet_id", "null_sheet_id", "requires_rebuild"],
    )
    def test_unusable_source_uses_rebuild(self, sheet_setup, source_fields, diff):
        if source_fields is not None:
            _google_source(sheet_setup.sheet, **source_fields)
        with _routed_export(diff) as routed:
            response = _post_export(sheet_setup)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["spreadsheet_id"] == "rebuilt"
        routed.rebuild.assert_called_once()
        routed.copy.assert_not_called()

    def test_safe_unchanged_google_source_returns_copy_url(self, sheet_setup):
        _google_source(sheet_setup.sheet)
        with _routed_export() as routed:
            response = _post_export(sheet_setup)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["url"] == "https://docs.google.com/spreadsheets/d/copied/edit"
        routed.copy.assert_called_once()
        routed.rebuild.assert_not_called()

    def test_safe_changed_google_source_passes_the_diff(self, sheet_setup):
        _google_source(sheet_setup.sheet)
        diff = _safe_diff(has_changes=True, cells={"8,0": {"raw_input": "CMP-11000"}})
        with _routed_export(diff) as routed:
            response = _post_export(sheet_setup)
        assert response.status_code == status.HTTP_200_OK
        assert routed.copy.call_args.args[1] == diff
        assert routed.copy.call_args.args[2] == "Export"
        assert routed.copy.call_args.args[3] == "token"
        routed.rebuild.assert_not_called()

    def test_patch_error_falls_back_to_rebuild(self, sheet_setup):
        _google_source(sheet_setup.sheet)
        with _routed_export(copy_error=GooglePatchExportError("patch failed")) as routed:
            response = _post_export(sheet_setup)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["spreadsheet_id"] == "rebuilt"
        routed.copy.assert_called_once()
        routed.rebuild.assert_called_once()

    def test_blank_tab_cleanup_returns_503_without_rebuild(self, sheet_setup):
        _google_source(sheet_setup.sheet)
        error = GoogleBlankTabCleanupError("copied tab rename failed")
        with _routed_export(copy_error=error) as routed:
            response = _post_export(sheet_setup)
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert "error" in response.data
        assert "url" not in response.data
        routed.rebuild.assert_not_called()

    def test_sheet_from_another_spreadsheet_returns_404(self, sheet_setup):
        other = create_test_spreadsheet(sheet_setup.project, name=f"Other {uuid.uuid4().hex[:6]}")
        other_sheet = create_test_sheet(other, name="Other sheet")
        with _routed_export() as routed:
            response = _post_export(sheet_setup, sheet_id=other_sheet.id)
        assert response.status_code == status.HTTP_404_NOT_FOUND
        routed.rebuild.assert_not_called()
        routed.copy.assert_not_called()

    def _grid(self, sheet):
        row = SheetRow.objects.create(sheet=sheet, position=0)
        columns = [
            SheetColumn.objects.create(sheet=sheet, position=index, name=chr(ord("A") + index))
            for index in range(2)
        ]
        Cell.objects.create(sheet=sheet, row=row, column=columns[1], raw_input="right")

    def _export_with_real_diff(self, setup):
        rebuild_result = {
            "spreadsheet_id": "rebuilt",
            "url": "https://docs.google.com/spreadsheets/d/rebuilt/edit",
        }
        copy_result = {
            "spreadsheet_id": "copied",
            "url": "https://docs.google.com/spreadsheets/d/copied/edit",
        }
        with patch(
            "google_docs_integration.views.run_google_api_with_token_retry",
            side_effect=lambda connection, fn: fn("token"),
        ), patch(
            "google_docs_integration.views.export_to_google_sheet",
            return_value=rebuild_result,
        ) as rebuild, patch(
            "google_docs_integration.views.export_copied_google_sheet",
            return_value=copy_result,
        ) as copy, patch(
            "google_docs_integration.views.export_converted_excel_sheet",
            return_value=copy_result,
        ) as converted:
            response = _post_export(setup)
        return response, SimpleNamespace(rebuild=rebuild, copy=copy, converted=converted)

    def test_rebuild_uses_the_stored_formats(self, sheet_setup):
        sheet = sheet_setup.sheet
        SpreadsheetCellFormat.objects.create(sheet=sheet, row_index=0, column_index=0, bold=True)
        SpreadsheetHighlight.objects.create(
            spreadsheet=sheet.spreadsheet, sheet=sheet, scope="CELL",
            row_index=5, col_index=0, color="#FEF08A",
        )

        response, routed = self._export_with_real_diff(sheet_setup)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["spreadsheet_id"] == "rebuilt"
        routed.rebuild.assert_called_once()
        assert routed.rebuild.call_args.args[3] == [
            {"row": 0, "column": 0, "userEnteredFormat": {"textFormat": {"bold": True}}},
        ]

    def test_google_structural_edit_since_import_uses_the_copier(self, sheet_setup):
        sheet = sheet_setup.sheet
        self._grid(sheet)
        _google_source(sheet, baseline=build_import_baseline(sheet))
        SheetService.insert_columns(sheet, position=1, count=1, created_by=sheet_setup.user)

        response, routed = self._export_with_real_diff(sheet_setup)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["spreadsheet_id"] == "copied"
        routed.copy.assert_called_once()
        routed.rebuild.assert_not_called()
        diff = routed.copy.call_args.args[1]
        assert diff["structural_operations"] == [
            {"op_type": "COL_INSERT", "anchor_position": 1, "count": 1},
        ]

    def test_google_undone_structural_edit_uses_the_copier(self, sheet_setup):
        sheet = sheet_setup.sheet
        self._grid(sheet)
        _google_source(sheet, baseline=build_import_baseline(sheet))
        inserted = SheetService.insert_columns(sheet, position=1, count=1, created_by=sheet_setup.user)
        operation = SheetStructureOperation.objects.get(id=inserted["operation_id"])
        SheetService.revert_structure_operation(sheet, operation)

        response, routed = self._export_with_real_diff(sheet_setup)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["spreadsheet_id"] == "copied"
        routed.copy.assert_called_once()
        routed.rebuild.assert_not_called()

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_excel_structural_edit_since_import_uses_conversion(self, sheet_setup):
        sheet = sheet_setup.sheet
        self._grid(sheet)
        source = _google_source(
            sheet,
            origin=SheetImportSource.Origin.EXCEL,
            google_spreadsheet_id="",
            google_sheet_id=None,
            baseline=build_import_baseline(sheet),
        )
        source.file.save("book.xlsx", ContentFile(b"PK-fake"), save=True)
        SheetService.delete_rows(sheet, position=0, count=1, created_by=sheet_setup.user)

        response, routed = self._export_with_real_diff(sheet_setup)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["spreadsheet_id"] == "copied"
        routed.converted.assert_called_once()
        routed.rebuild.assert_not_called()
        diff = routed.converted.call_args.args[1]
        assert diff["structural_operations"] == [
            {"op_type": "ROW_DELETE", "anchor_position": 0, "count": 1},
        ]

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_excel_file_uses_conversion(self, sheet_setup):
        source = _google_source(
            sheet_setup.sheet,
            origin=SheetImportSource.Origin.EXCEL,
            google_spreadsheet_id="",
            google_sheet_id=None,
        )
        source.file.save("book.xlsx", ContentFile(b"PK-fake"), save=True)
        converted_result = {
            "spreadsheet_id": "converted",
            "url": "https://docs.google.com/spreadsheets/d/converted/edit",
        }
        with _routed_export() as routed, patch(
            "google_docs_integration.views.export_converted_excel_sheet",
            return_value=converted_result,
        ) as converted:
            response = _post_export(sheet_setup)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["spreadsheet_id"] == "converted"
        converted.assert_called_once()
        routed.rebuild.assert_not_called()
        routed.copy.assert_not_called()

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    @pytest.mark.parametrize(
        "diff, convert_error",
        [
            (_safe_diff(requires_rebuild=True, has_changes=True), None),
            (None, GooglePatchExportError("convert failed")),
        ],
        ids=["requires_rebuild", "conversion_error"],
    )
    def test_excel_source_falls_back_to_rebuild(self, sheet_setup, diff, convert_error):
        source = _google_source(
            sheet_setup.sheet,
            origin=SheetImportSource.Origin.EXCEL,
            google_spreadsheet_id="",
            google_sheet_id=None,
        )
        source.file.save("book.xlsx", ContentFile(b"PK-fake"), save=True)
        with _routed_export(diff) as routed, patch(
            "google_docs_integration.views.export_converted_excel_sheet",
            side_effect=convert_error,
        ) as converted:
            response = _post_export(sheet_setup)
        assert response.data["spreadsheet_id"] == "rebuilt"
        routed.rebuild.assert_called_once()
        routed.copy.assert_not_called()
        assert converted.call_count == (0 if convert_error is None else 1)

    
    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_excel_cleanup_failure_does_not_rebuild(self, sheet_setup):
        source = _google_source(
            sheet_setup.sheet,
            origin=SheetImportSource.Origin.EXCEL,
            google_spreadsheet_id="",
            google_sheet_id=None,
        )
        source.file.save("book.xlsx", ContentFile(b"PK-fake"), save=True)
        with _routed_export() as routed, patch(
            "google_docs_integration.views.export_converted_excel_sheet",
            side_effect=GoogleBlankTabCleanupError("delete failed"),
        ):
            response = _post_export(sheet_setup)
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        routed.rebuild.assert_not_called()

    def test_excel_conversion_uploads_xlsx_and_patches_the_named_worksheet(self):
        from google_docs_integration.google_sheets_patch_export import export_converted_excel_sheet

        class StoredFile:
            def __init__(self, payload):
                self.payload = payload

            def open(self, mode):
                assert mode == "rb"
                return io.BytesIO(self.payload)

        payload = b"PK-original"
        source = SimpleNamespace(
            worksheet_name="Budget",
            file=StoredFile(payload),
            baseline={"cells": {}},
        )
        diff = _safe_diff(has_changes=True, cells={"0,0": {"raw_input": "new"}})

        def fake_post(url, params=None, headers=None, data=None, timeout=None):
            assert url.endswith("/upload/drive/v3/files")
            assert params["uploadType"] == "multipart"
            assert b"application/vnd.google-apps.spreadsheet" in data
            assert payload in data
            response = MagicMock()
            response.status_code = 200
            response.json.return_value = {"id": "converted-id"}
            return response

        def fake_request(method, url, access_token, timeout, **kwargs):
            response = MagicMock()
            response.status_code = 200
            if method == "get":
                response.json.return_value = {
                    "sheets": [
                        {"properties": {"sheetId": 1, "title": "Other"}},
                        {"properties": {"sheetId": 0, "title": "Budget"}},
                    ]
                }
                return response
            assert "copyTo" not in url
            response.json.return_value = {}
            return response

        with patch(
            "google_docs_integration.google_sheets_patch_export.requests.post",
            side_effect=fake_post,
        ), patch(
            "google_docs_integration.google_sheets_patch_export._request",
            side_effect=fake_request,
        ) as requested:
            result = export_converted_excel_sheet(source, diff, "Export", "token")
        assert result["spreadsheet_id"] == "converted-id"
        assert source.file.payload == b"PK-original"
        assert any(call.args[0] == "get" for call in requested.call_args_list)


class TestExportCopiedGoogleSheet:
    def _run(self, diff, source=None, copy_title="Budget", fail=None, recorded=None):
        if recorded is None:
            recorded = []

        def request(method, url, **kwargs):
            recorded.append((url, kwargs.get("json")))
            if fail and fail(url, kwargs.get("json"), len(recorded)):
                return _http_error(fail.status_code)
            if url == GOOGLE_SHEETS_URL:
                return _ok(_created_payload())
            if url.endswith(":copyTo"):
                return _ok(_copy_payload(copy_title))
            if method == "get" and url == f"{GOOGLE_SHEETS_URL}/{DESTINATION_ID}":
                return _ok({
                    "sheets": [{
                        "properties": {
                            "sheetId": COPIED_SHEET_ID,
                            "gridProperties": {"rowCount": 1000, "columnCount": 26},
                        }
                    }]
                })
            return _ok({})

        with patch(
            "google_docs_integration.google_sheets_patch_export.requests.request",
            side_effect=request,
        ):
            result = export_copied_google_sheet(source or _source(), diff, "Phase 5", "token")
        return result, recorded

    def test_no_edit_copies_deletes_blank_tab_and_restores_name(self):
        result, recorded = self._run(_safe_diff(), copy_title="Copy of Budget")
        urls = [url for url, _body in recorded]
        copy_calls = [(url, body) for url, body in recorded if url.endswith(":copyTo")]
        assert copy_calls == [(
            f"{GOOGLE_SHEETS_URL}/{SOURCE_SPREADSHEET_ID}/sheets/0:copyTo",
            {"destinationSpreadsheetId": DESTINATION_ID},
        )]
        assert result == {
            "spreadsheet_id": DESTINATION_ID,
            "url": f"https://docs.google.com/spreadsheets/d/{DESTINATION_ID}/edit",
        }
        assert not any(url.endswith("/values:batchUpdate") for url in urls)
        assert not any(
            body and "repeatCell" in str(body)
            for _url, body in recorded
        )
        assert recorded[2][1]["requests"] == [{"deleteSheet": {"sheetId": BLANK_SHEET_ID}}]
        rename = recorded[3][1]["requests"][0]["updateSheetProperties"]
        assert rename["properties"] == {"sheetId": COPIED_SHEET_ID, "title": "Budget"}
        assert rename["fields"] == "title"

    def test_value_edit_uses_user_entered_ranges(self):
        diff = _safe_diff(
            has_changes=True,
            cells={
                "8,0": {"raw_input": "CMP-11000"},
                "0,1": {"raw_input": "=B2*2"},
                "1,0": {"raw_input": None},
            },
        )
        _result, recorded = self._run(diff)
        values = next(body for url, body in recorded if url.endswith("/values:batchUpdate"))
        assert values["valueInputOption"] == "USER_ENTERED"
        assert values["data"] == [
            {"range": "'Budget'!A9", "values": [["CMP-11000"]]},
            {"range": "'Budget'!B1", "values": [["=B2*2"]]},
            {"range": "'Budget'!A2", "values": [[""]]},
        ]

    def _repeat_cells(self, diff):
        _result, recorded = self._run(diff)
        repeats = []
        for _url, body in recorded:
            for request in (body or {}).get("requests", []):
                if "repeatCell" in request:
                    repeats.append(request["repeatCell"])
        return repeats

    def test_multiple_format_fields_use_one_repeat_cell(self):
        repeats = self._repeat_cells(_safe_diff(
            has_changes=True,
            formats={"0,0": {"bold": True, "italic": True, "text_color": "#7C3AED"}},
        ))
        assert len(repeats) == 1
        assert repeats[0]["fields"].split(",") == [
            "userEnteredFormat.textFormat.bold",
            "userEnteredFormat.textFormat.italic",
            "userEnteredFormat.textFormat.foregroundColor",
            "userEnteredFormat.textFormat.foregroundColorStyle",
        ]
        text = repeats[0]["cell"]["userEnteredFormat"]["textFormat"]
        assert text["bold"] is True
        assert text["italic"] is True
        assert text["foregroundColor"] == {"red": 124 / 255, "green": 58 / 255, "blue": 237 / 255}

    def test_highlight_uses_a_background_only_mask(self):
        repeats = self._repeat_cells(_safe_diff(
            has_changes=True,
            highlights={"cells": {"2,0": "#BFDBFE"}},
        ))
        assert len(repeats) == 1
        assert repeats[0]["fields"] == (
            "userEnteredFormat.backgroundColor,userEnteredFormat.backgroundColorStyle"
        )
        assert repeats[0]["cell"]["userEnteredFormat"]["backgroundColor"] == {
            "red": 191 / 255,
            "green": 219 / 255,
            "blue": 254 / 255,
        }

    def test_supported_currency_format_is_patched(self):
        repeats = self._repeat_cells(_safe_diff(
            has_changes=True,
            formats={"0,0": {"number_format": {
                "type": "CURRENCY",
                "currency_code": "USD",
                "decimal_places": 2,
            }}},
        ))
        assert repeats[0]["fields"] == "userEnteredFormat.numberFormat"
        assert repeats[0]["cell"]["userEnteredFormat"]["numberFormat"] == {
            "type": "CURRENCY",
            "pattern": "[$$-409]#,##0.00",
        }

    def test_requires_rebuild_makes_no_request(self):
        with patch(
            "google_docs_integration.google_sheets_patch_export.requests.request",
        ) as request:
            with pytest.raises(GooglePatchExportError):
                export_copied_google_sheet(_source(), _safe_diff(requires_rebuild=True), "Phase 5", "token")
        request.assert_not_called()

    def test_row_highlight_makes_no_request(self):
        diff = _safe_diff(highlights={"cells": {}, "rows": {"0": "#FF0000"}, "columns": {}})
        with patch(
            "google_docs_integration.google_sheets_patch_export.requests.request",
        ) as request:
            with pytest.raises(GooglePatchExportError):
                export_copied_google_sheet(_source(), diff, "Phase 5", "token")
        request.assert_not_called()

    def test_copy_failure_raises_patch_error(self):
        def fail(url, _body, _count):
            return url.endswith(":copyTo")

        fail.status_code = 403
        with pytest.raises(GooglePatchExportError):
            self._run(_safe_diff(), fail=fail)

    def test_401_is_reraised(self):
        def fail(url, _body, _count):
            return url == GOOGLE_SHEETS_URL

        fail.status_code = 401
        with pytest.raises(requests.HTTPError) as caught:
            self._run(_safe_diff(), fail=fail)
        assert caught.value.response.status_code == 401

    def test_patch_failure_deletes_only_the_copied_tab(self):
        diff = _safe_diff(has_changes=True, cells={"0,0": {"raw_input": "x"}})
        recorded = []

        def fail(url, _body, _count):
            return url.endswith("/values:batchUpdate")

        fail.status_code = 500
        with pytest.raises(GooglePatchExportError):
            self._run(diff, fail=fail, recorded=recorded)
        assert recorded[-1][0] == f"https://www.googleapis.com/drive/v3/files/{DESTINATION_ID}"
        assert not any(
            "deleteSheet" in str(body)
            for _url, body in recorded
        )

    def test_blank_tab_delete_failure_raises_cleanup_error(self):
        def fail(url, body, _count):
            return url.endswith(":batchUpdate") and "deleteSheet" in str(body)

        fail.status_code = 500
        with pytest.raises(GoogleBlankTabCleanupError) as caught:
            self._run(_safe_diff(), fail=fail)

    def test_rename_failure_raises_cleanup_error(self):
        seen_delete = {"done": False}

        def fail(url, body, _count):
            if not url.endswith(":batchUpdate"):
                return False
            if "deleteSheet" in str(body):
                seen_delete["done"] = True
                return False
            return seen_delete["done"]

        fail.status_code = 500
        with pytest.raises(GoogleBlankTabCleanupError):
            self._run(_safe_diff(), copy_title="Copy of Budget", fail=fail)

    def test_source_spreadsheet_id_is_only_on_copy_to(self):
        diff = _safe_diff(
            has_changes=True,
            cells={"0,0": {"raw_input": "x"}},
            formats={"0,0": {"bold": True}},
        )
        _result, recorded = self._run(diff)
        for url, _body in recorded:
            if SOURCE_SPREADSHEET_ID in url:
                assert url.endswith(":copyTo")
            if url.endswith("/values:batchUpdate") or url.endswith(":batchUpdate"):
                assert DESTINATION_ID in url

    def test_delete_then_value_patch_uses_the_final_coordinate(self):
        diff = _safe_diff(
            has_changes=True,
            structural_operations=[{"op_type": "COL_DELETE", "anchor_position": 1, "count": 1}],
            cells={"0,1": {"raw_input": "moved"}},
        )
        _result, recorded = self._run(diff)
        urls = [url for url, _body in recorded]
        assert urls.index(f"{GOOGLE_SHEETS_URL}/{DESTINATION_ID}:batchUpdate") < urls.index(
            f"{GOOGLE_SHEETS_URL}/{DESTINATION_ID}/values:batchUpdate"
        )
        values = next(body for url, body in recorded if url.endswith("/values:batchUpdate"))
        assert values["data"] == [{"range": "'Budget'!B1", "values": [["moved"]]}]

    def test_structural_export_does_not_rewrite_a_cross_sheet_formula(self):
        from spreadsheet.formula_rewrite import rewrite_formula_text

        rewritten = rewrite_formula_text("=Sheet2!A1", "COL_INSERT", 0, 1)
        source = _source()
        source.baseline = {"cells": {"0,0": {"raw_input": "=Sheet2!A1"}}}
        diff = _safe_diff(
            has_changes=True,
            structural_operations=[{"op_type": "COL_INSERT", "anchor_position": 0, "count": 1}],
            cells={"0,1": {"raw_input": rewritten}},
        )
        _result, recorded = self._run(diff, source=source)
        assert not any(url.endswith("/values:batchUpdate") for url, _body in recorded)

    def test_structural_export_still_writes_a_formula_the_user_edited(self):
        source = _source()
        source.baseline = {"cells": {"0,0": {"raw_input": "=B1"}}}
        diff = _safe_diff(
            has_changes=True,
            structural_operations=[{"op_type": "COL_INSERT", "anchor_position": 0, "count": 1}],
            cells={"0,1": {"raw_input": "=C1+5"}},
        )
        _result, recorded = self._run(diff, source=source)
        values = next(body for url, body in recorded if url.endswith("/values:batchUpdate"))
        assert values["data"] == [{"range": "'Budget'!B1", "values": [["=C1+5"]]}]



@pytest.mark.django_db
class TestGoogleCopyTokenRetry:
    def test_rejected_token_refreshes_and_retries_the_copy(self):
        suffix = uuid.uuid4().hex[:8]
        user = create_test_user(
            username=f"p5tok_{suffix}",
            email=f"p5tok_{suffix}@test.com",
        )
        connection = GoogleDocsConnection(
            user=user,
            google_email="user@gmail.com",
            is_active=True,
        )
        connection.set_access_token("token-abc")
        connection.set_refresh_token("refresh-abc")
        connection.token_expiry = timezone.now() + timedelta(hours=1)
        connection.save()

        creates = []

        def request(method, url, **kwargs):
            if url == GOOGLE_SHEETS_URL:
                creates.append(kwargs["headers"]["Authorization"])
                if len(creates) == 1:
                    return _http_error(401)
                return _ok(_created_payload())

            if url.endswith(":copyTo"):
                return _ok(_copy_payload())

            return _ok({})

        with patch(
            "google_docs_integration.google_sheets_patch_export.requests.request",
            side_effect=request,
        ), patch(
            "google_docs_integration.services.refresh_google_tokens",
            return_value={
                "access_token": "refreshed-token",
                "token_expiry": timezone.now() + timedelta(hours=1),
            },
        ) as refresh:
            result = run_google_api_with_token_retry(
                connection,
                lambda token: export_copied_google_sheet(
                    _source(),
                    _safe_diff(),
                    "Phase 5",
                    token,
                ),
            )

        assert creates == [
            "Bearer token-abc",
            "Bearer refreshed-token",
        ]
        refresh.assert_called_once()
        assert result["spreadsheet_id"] == DESTINATION_ID


class TestGoogleFormatBody:
    def test_mask_order_matches_the_table_not_the_diff(self):
        body, fields = _format_body({
            "number_format": {"type": "PERCENT", "decimal_places": 0},
            "text_color": None,
            "bold": True,
        })
        assert fields == [
            "userEnteredFormat.textFormat.bold",
            "userEnteredFormat.textFormat.foregroundColor",
            "userEnteredFormat.textFormat.foregroundColorStyle",
            "userEnteredFormat.numberFormat",
        ]
        assert body == {
            "textFormat": {"bold": True},
            "numberFormat": {"type": "PERCENT", "pattern": "0%"},
        }

    def test_cleared_size_only_sends_the_mask(self):
        assert _format_body({"font_size": None}) == (
            {},
            ["userEnteredFormat.textFormat.fontSize"],
        )

    def test_unsupported_family_raises(self):
        with pytest.raises(GooglePatchExportError):
            _format_body({"font_family": "Comic Sans"})

    def test_no_format_fields_is_malformed(self):
        with pytest.raises(GooglePatchExportError):
            _format_body({"underline": True})

@pytest.mark.django_db
class TestStoredGoogleFormats:
    def _fill(self, sheet, scope, color, row=None, col=None):
        SpreadsheetHighlight.objects.create(
            spreadsheet=sheet.spreadsheet, sheet=sheet, scope=scope,
            row_index=row, col_index=col, color=color,
        )

    def test_maps_text_style_font_size_and_red(self, sheet_setup):
        SpreadsheetCellFormat.objects.create(
            sheet=sheet_setup.sheet, row_index=0, column_index=0,
            bold=True, italic=True, strikethrough=True,
            font_family="Arial, sans-serif", font_size=14, text_color="#DC2626",
        )
        assert stored_google_formats(sheet_setup.sheet, 1, 1) == [{
            "row": 0,
            "column": 0,
            "userEnteredFormat": {"textFormat": {
                "bold": True,
                "italic": True,
                "strikethrough": True,
                "fontFamily": "Arial",
                "fontSize": 14,
                "foregroundColor": {"red": 220 / 255, "green": 38 / 255, "blue": 38 / 255},
            }},
        }]

    def test_maps_number_formats_and_an_unknown_currency_as_a_number(self, sheet_setup):
        stored = [
            {"type": "NUMBER", "decimal_places": 0},
            {"type": "NUMBER", "decimal_places": 2},
            {"type": "PERCENT", "decimal_places": 0},
            {"type": "PERCENT", "decimal_places": 2},
            {"type": "CURRENCY", "currency_code": "USD", "decimal_places": 2},
            {"type": "CURRENCY", "currency_code": "GBP", "decimal_places": 2},
            {"type": "CURRENCY", "currency_code": "EUR", "decimal_places": 2},
            {"type": "CURRENCY", "currency_code": "JPY", "decimal_places": 0},
            {"type": "CURRENCY", "currency_code": "AUD", "decimal_places": 2},
            {"type": "GENERAL"},
        ]
        for column, number_format in enumerate(stored):
            SpreadsheetCellFormat.objects.create(
                sheet=sheet_setup.sheet, row_index=0, column_index=column, number_format=number_format,
            )
        patterns = {
            item["column"]: item["userEnteredFormat"]["numberFormat"]
            for item in stored_google_formats(sheet_setup.sheet, 1, len(stored))
        }
        assert patterns == {
            0: {"type": "NUMBER", "pattern": "#,##0"},
            1: {"type": "NUMBER", "pattern": "#,##0.00"},
            2: {"type": "PERCENT", "pattern": "0%"},
            3: {"type": "PERCENT", "pattern": "0.00%"},
            4: {"type": "CURRENCY", "pattern": "[$$-409]#,##0.00"},
            5: {"type": "CURRENCY", "pattern": "[$£-809]#,##0.00"},
            6: {"type": "CURRENCY", "pattern": "[$€-407]#,##0.00"},
            7: {"type": "CURRENCY", "pattern": "[$¥-411]#,##0"},
            8: {"type": "NUMBER", "pattern": "#,##0.00"},
        }

    def test_fill_precedence_blank_cells_and_matrix_bounds(self, sheet_setup):
        sheet = sheet_setup.sheet
        self._fill(sheet, "COLUMN", "#0000FF", col=1)
        self._fill(sheet, "ROW", "#00FF00", row=1)
        self._fill(sheet, "CELL", "#FEF08A", row=1, col=1)
        self._fill(sheet, "CELL", "#FEF08A", row=9, col=0)
        SpreadsheetCellFormat.objects.create(sheet=sheet, row_index=20, column_index=0, bold=True)

        found = {
            (item["row"], item["column"]): item["userEnteredFormat"]["backgroundColor"]
            for item in stored_google_formats(sheet, 10, 2)
        }
        yellow = {"red": 254 / 255, "green": 240 / 255, "blue": 138 / 255}
        green = {"red": 0.0, "green": 1.0, "blue": 0.0}
        blue = {"red": 0.0, "green": 0.0, "blue": 1.0}
        assert found[(1, 1)] == yellow
        assert found[(1, 0)] == green
        assert found[(0, 1)] == blue
        assert found[(9, 0)] == yellow
        assert (0, 0) not in found
        assert all(row < 10 for row, _column in found)

    def test_unsupported_values_are_skipped_not_raised(self, sheet_setup):
        SpreadsheetCellFormat.objects.create(
            sheet=sheet_setup.sheet, row_index=0, column_index=0,
            bold=True, font_family="Comic Sans", font_size=0, text_color="red",
            number_format={"type": "DATE"},
        )
        assert stored_google_formats(sheet_setup.sheet, 1, 1) == [{
            "row": 0, "column": 0, "userEnteredFormat": {"textFormat": {"bold": True}},
        }]