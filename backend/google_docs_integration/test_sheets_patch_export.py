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
from google_docs_integration.models import GoogleDocsConnection
from google_docs_integration.services import GOOGLE_SHEETS_URL, run_google_api_with_token_retry
from spreadsheet.models import SheetImportSource
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
    def test_no_source_uses_rebuild(self, sheet_setup):
        with _routed_export() as routed:
            response = _post_export(sheet_setup)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["spreadsheet_id"] == "rebuilt"
        routed.rebuild.assert_called_once()
        routed.copy.assert_not_called()

    def test_excel_source_uses_rebuild(self, sheet_setup):
        _google_source(
            sheet_setup.sheet,
            origin=SheetImportSource.Origin.EXCEL,
            google_spreadsheet_id="",
            google_sheet_id=None,
        )
        with _routed_export() as routed:
            response = _post_export(sheet_setup)
        assert response.status_code == status.HTTP_200_OK
        routed.rebuild.assert_called_once()
        routed.copy.assert_not_called()

    def test_blank_google_spreadsheet_id_uses_rebuild(self, sheet_setup):
        _google_source(sheet_setup.sheet, google_spreadsheet_id="  ")
        with _routed_export() as routed:
            response = _post_export(sheet_setup)
        assert response.status_code == status.HTTP_200_OK
        routed.rebuild.assert_called_once()
        routed.copy.assert_not_called()

    def test_null_google_sheet_id_uses_rebuild(self, sheet_setup):
        _google_source(sheet_setup.sheet, google_sheet_id=None)
        with _routed_export() as routed:
            response = _post_export(sheet_setup)
        assert response.status_code == status.HTTP_200_OK
        routed.rebuild.assert_called_once()
        routed.copy.assert_not_called()

    def test_requires_rebuild_uses_rebuild(self, sheet_setup):
        _google_source(sheet_setup.sheet)
        with _routed_export(_safe_diff(requires_rebuild=True, has_changes=True)) as routed:
            response = _post_export(sheet_setup)
        assert response.status_code == status.HTTP_200_OK
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
        error = GoogleBlankTabCleanupError(
            "copied tab rename failed",
            spreadsheet_id=DESTINATION_ID,
            url=f"https://docs.google.com/spreadsheets/d/{DESTINATION_ID}/edit",
        )
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

    def test_structural_only_diff_uses_the_copier(self, sheet_setup):
        _google_source(sheet_setup.sheet)
        diff = _safe_diff(structural_operations=[
            {"op_type": "COL_DELETE", "anchor_position": 1, "count": 1},
        ])
        with _routed_export(diff) as routed:
            response = _post_export(sheet_setup)
        assert response.status_code == status.HTTP_200_OK
        assert routed.copy.call_args.args[1] == diff
        routed.rebuild.assert_not_called()

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
    def test_excel_requires_rebuild_skips_conversion(self, sheet_setup):
        source = _google_source(
            sheet_setup.sheet,
            origin=SheetImportSource.Origin.EXCEL,
            google_spreadsheet_id="",
            google_sheet_id=None,
        )
        source.file.save("book.xlsx", ContentFile(b"PK-fake"), save=True)
        with _routed_export(_safe_diff(requires_rebuild=True, has_changes=True)) as routed, patch(
            "google_docs_integration.views.export_converted_excel_sheet",
        ) as converted:
            response = _post_export(sheet_setup)
        assert response.data["spreadsheet_id"] == "rebuilt"
        converted.assert_not_called()
        routed.copy.assert_not_called()
        routed.rebuild.assert_called_once()

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_excel_conversion_failure_uses_rebuild(self, sheet_setup):
        source = _google_source(
            sheet_setup.sheet,
            origin=SheetImportSource.Origin.EXCEL,
            google_spreadsheet_id="",
            google_sheet_id=None,
        )
        source.file.save("book.xlsx", ContentFile(b"PK-fake"), save=True)
        with _routed_export() as routed, patch(
            "google_docs_integration.views.export_converted_excel_sheet",
            side_effect=GooglePatchExportError("convert failed"),
        ):
            response = _post_export(sheet_setup)
        assert response.data["spreadsheet_id"] == "rebuilt"
        routed.rebuild.assert_called_once()

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
            side_effect=GoogleBlankTabCleanupError(
                "delete failed",
                spreadsheet_id="converted",
                url="https://docs.google.com/spreadsheets/d/converted/edit",
            ),
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
            assert b"PK-original" == payload
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

    def test_bold_only_uses_a_narrow_mask(self):
        repeats = self._repeat_cells(_safe_diff(has_changes=True, formats={"0,0": {"bold": True}}))
        assert len(repeats) == 1
        assert repeats[0]["fields"] == "userEnteredFormat.textFormat.bold"
        assert repeats[0]["cell"]["userEnteredFormat"]["textFormat"]["bold"] is True
        assert "numberFormat" not in repeats[0]["fields"]

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
        assert "borders" not in repeats[0]["fields"]
        assert "textFormat" not in repeats[0]["fields"]
        assert "horizontalAlignment" not in repeats[0]["fields"]

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

    def test_google_sheet_id_zero_is_copied(self):
        _result, recorded = self._run(_safe_diff(), source=_source(sheet_id=0))
        assert recorded[1][0].endswith("/sheets/0:copyTo")

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
        deletes = [
            request["deleteSheet"]["sheetId"]
            for _url, body in recorded
            for request in (body or {}).get("requests", [])
            if "deleteSheet" in request
        ]
        assert deletes == [COPIED_SHEET_ID]

    def test_blank_tab_delete_failure_raises_cleanup_error(self):
        def fail(url, body, _count):
            return url.endswith(":batchUpdate") and "deleteSheet" in str(body)

        fail.status_code = 500
        with pytest.raises(GoogleBlankTabCleanupError) as caught:
            self._run(_safe_diff(), fail=fail)
        assert caught.value.spreadsheet_id == DESTINATION_ID
        assert caught.value.url == f"https://docs.google.com/spreadsheets/d/{DESTINATION_ID}/edit"

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
        with pytest.raises(GoogleBlankTabCleanupError) as caught:
            self._run(_safe_diff(), copy_title="Copy of Budget", fail=fail)
        assert caught.value.spreadsheet_id == DESTINATION_ID
        assert caught.value.url == f"https://docs.google.com/spreadsheets/d/{DESTINATION_ID}/edit"

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

    def _dimension_requests(self, diff, **kwargs):
        _result, recorded = self._run(diff, **kwargs)
        found = []
        for url, body in recorded:
            if url == f"{GOOGLE_SHEETS_URL}/{DESTINATION_ID}:batchUpdate":
                for request in (body or {}).get("requests", []):
                    if "insertDimension" in request or "deleteDimension" in request or "updateSheetProperties" in request:
                        found.append(request)
        source_writes = [
            url for url, _body in recorded
            if SOURCE_SPREADSHEET_ID in url and not url.endswith(":copyTo")
        ]
        return found, source_writes

    def test_column_delete_only_deletes_the_copied_column(self):
        diff = _safe_diff(structural_operations=[
            {"op_type": "COL_DELETE", "anchor_position": 1, "count": 1},
        ])
        found, source_writes = self._dimension_requests(diff)
        assert source_writes == []
        assert found == [{
            "deleteDimension": {
                "range": {
                    "sheetId": COPIED_SHEET_ID,
                    "dimension": "COLUMNS",
                    "startIndex": 1,
                    "endIndex": 2,
                }
            }
        }]

    def test_row_delete_with_an_empty_padding_row_deletes_only_the_original_row(self):
        diff = _safe_diff(structural_operations=[
            {"op_type": "ROW_DELETE", "anchor_position": 1, "count": 1},
        ])
        found, source_writes = self._dimension_requests(diff)
        assert source_writes == []
        assert found == [{
            "deleteDimension": {
                "range": {
                    "sheetId": COPIED_SHEET_ID,
                    "dimension": "ROWS",
                    "startIndex": 1,
                    "endIndex": 2,
                }
            }
        }]

    def test_row_delete_does_not_clear_formatting(self):
        requests = self._recorded_requests(_safe_diff(structural_operations=[
            {"op_type": "ROW_DELETE", "anchor_position": 1, "count": 1},
        ]))
        assert not any(item.get("repeatCell", {}).get("fields") == "userEnteredFormat" for item in requests)


    def test_row_insert_at_the_top_does_not_inherit_from_before(self):
        diff = _safe_diff(structural_operations=[
            {"op_type": "ROW_INSERT", "anchor_position": 0, "count": 1},
        ])
        found, _source_writes = self._dimension_requests(diff)
        assert found[0]["insertDimension"]["inheritFromBefore"] is False
        assert found[0]["insertDimension"]["range"]["startIndex"] == 0
        assert found[0]["insertDimension"]["range"]["endIndex"] == 1

    def test_column_insert_inherits_from_the_previous_column(self):
        diff = _safe_diff(structural_operations=[
            {"op_type": "COL_INSERT", "anchor_position": 2, "count": 1},
        ])
        found, _source_writes = self._dimension_requests(diff)
        insert = found[0]["insertDimension"]
        assert insert["inheritFromBefore"] is True
        assert insert["range"]["dimension"] == "COLUMNS"
        assert insert["range"]["sheetId"] == COPIED_SHEET_ID

    def test_operations_stay_in_order(self):
        diff = _safe_diff(structural_operations=[
            {"op_type": "ROW_INSERT", "anchor_position": 1, "count": 2},
            {"op_type": "COL_DELETE", "anchor_position": 0, "count": 1},
        ])
        found, _source_writes = self._dimension_requests(diff)
        assert list(found[0]) == ["insertDimension"]
        assert found[0]["insertDimension"]["range"]["endIndex"] == 3
        assert list(found[1]) == ["deleteDimension"]

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

    def test_delete_then_bold_patch_uses_the_copied_sheet_id(self):
        diff = _safe_diff(
            has_changes=True,
            structural_operations=[{"op_type": "ROW_DELETE", "anchor_position": 0, "count": 1}],
            formats={"0,0": {"bold": True}},
        )
        repeats = self._repeat_cells(diff)
        assert repeats[0]["range"]["sheetId"] == COPIED_SHEET_ID
        assert repeats[0]["fields"] == "userEnteredFormat.textFormat.bold"

    def test_insert_past_the_grid_grows_before_inserting(self):
        diff = _safe_diff(structural_operations=[
            {"op_type": "COL_INSERT", "anchor_position": 30, "count": 1},
        ])
        found, _source_writes = self._dimension_requests(diff)
        assert found[0]["updateSheetProperties"]["properties"]["gridProperties"]["columnCount"] == 30
        assert found[0]["updateSheetProperties"]["properties"]["sheetId"] == COPIED_SHEET_ID
        assert found[1]["insertDimension"]["range"]["startIndex"] == 30

    def test_delete_outside_the_grid_raises_before_a_dimension_write(self):
        diff = _safe_diff(structural_operations=[
            {"op_type": "ROW_DELETE", "anchor_position": 1000, "count": 1},
        ])
        recorded = []
        with pytest.raises(GooglePatchExportError):
            self._run(diff, recorded=recorded)
        assert not any(
            "deleteDimension" in str(body)
            for _url, body in recorded
        )

    def test_dimension_api_error_deletes_only_the_copied_tab(self):
        diff = _safe_diff(structural_operations=[
            {"op_type": "COL_DELETE", "anchor_position": 1, "count": 1},
        ])
        recorded = []

        def fail(url, body, _count):
            return (
                url == f"{GOOGLE_SHEETS_URL}/{DESTINATION_ID}:batchUpdate"
                and "deleteDimension" in str(body)
            )

        fail.status_code = 400
        with pytest.raises(GooglePatchExportError, match="google sheets request failed"):
            self._run(diff, fail=fail, recorded=recorded)
        deletes = [
            request["deleteSheet"]["sheetId"]
            for _url, body in recorded
            for request in (body or {}).get("requests", [])
            if "deleteSheet" in request
        ]
        assert deletes == [COPIED_SHEET_ID]
    
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

    def _recorded_requests(self, diff, **kwargs):
        _result, recorded = self._run(diff, **kwargs)
        requests = []
        for _url, body in recorded:
            requests.extend((body or {}).get("requests", []))
        return requests

    def test_row_insert_clears_only_the_new_row(self):
        requests = self._recorded_requests(_safe_diff(structural_operations=[
            {"op_type": "ROW_INSERT", "anchor_position": 1, "count": 2},
        ]))
        clear = next(item["repeatCell"] for item in requests if "repeatCell" in item)
        assert clear["fields"] == "userEnteredFormat"
        assert clear["cell"] == {}
        assert clear["range"] == {
            "sheetId": COPIED_SHEET_ID,
            "startRowIndex": 1,
            "endRowIndex": 3,
            "startColumnIndex": 0,
            "endColumnIndex": 26,
        }

    def test_column_insert_at_the_end_clears_only_the_new_column(self):
        requests = self._recorded_requests(_safe_diff(structural_operations=[
            {"op_type": "COL_INSERT", "anchor_position": 26, "count": 1},
        ]))
        clear = next(item["repeatCell"] for item in requests if item.get("repeatCell", {}).get("fields") == "userEnteredFormat")
        assert clear["range"]["startColumnIndex"] == 26
        assert clear["range"]["endColumnIndex"] == 27
        assert clear["range"]["startRowIndex"] == 0
        assert clear["range"]["endRowIndex"] == 1000

    def test_insert_at_the_top_clears_the_new_row_before_a_user_format(self):
        diff = _safe_diff(
            has_changes=True,
            structural_operations=[{"op_type": "ROW_INSERT", "anchor_position": 0, "count": 1}],
            formats={"0,0": {"bold": True}},
        )
        _result, recorded = self._run(diff)
        repeats = []
        for url, body in recorded:
            for item in (body or {}).get("requests", []):
                if "repeatCell" in item:
                    repeats.append((url, item["repeatCell"]))
        assert repeats[0][1]["fields"] == "userEnteredFormat"
        assert repeats[0][1]["range"]["startRowIndex"] == 0
        assert repeats[0][1]["range"]["endRowIndex"] == 1
        assert repeats[1][1]["fields"] == "userEnteredFormat.textFormat.bold"
        assert repeats[1][1]["range"]["endColumnIndex"] == 1




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
    