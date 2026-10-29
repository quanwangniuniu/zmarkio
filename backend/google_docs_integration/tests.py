"""
Tests for google_docs_integration views.
Covers: google_docs_integration/views.py, models.py, serializers.py
"""
import uuid
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APIClient

from core.services.oauth_state import create_oauth_state
from google_docs_integration.models import GoogleDocsConnection

User = get_user_model()

GOOGLE_DOCS_STATE_SALT = "google-docs-oauth-state"
STATUS_URL = "/api/google-docs/status/"
CONNECT_URL = "/api/google-docs/connect/"
CALLBACK_URL = "/api/google-docs/callback/"
DISCONNECT_URL = "/api/google-docs/disconnect/"
DOCUMENTS_URL = "/api/google-docs/documents/"
IMPORT_URL = "/api/google-docs/import/"
EXPORT_URL = "/api/google-docs/export/"
EXPORT_RAW_URL = "/api/google-docs/export/raw/"
SHEETS_IMPORT_URL = "/api/google-docs/sheets/import/"
SHEETS_EXPORT_URL = "/api/google-docs/sheets/export/"


@pytest.fixture
def user(db):
    return User.objects.create_user(
        username=f"gd_{uuid.uuid4().hex[:8]}",
        email=f"gd_{uuid.uuid4().hex[:8]}@test.com",
        password="pass123",
    )


@pytest.fixture
def user2(db):
    return User.objects.create_user(
        username=f"gd2_{uuid.uuid4().hex[:8]}",
        email=f"gd2_{uuid.uuid4().hex[:8]}@test.com",
        password="pass123",
    )


@pytest.fixture
def connection(user, db):
    """Active GoogleDocsConnection for user."""
    conn = GoogleDocsConnection(user=user, google_email="user@gmail.com", is_active=True)
    conn.set_access_token("fake_access_token_abc")
    conn.set_refresh_token("fake_refresh_token_xyz")
    conn.save()
    return conn


@pytest.fixture
def api_client(user):
    c = APIClient()
    c.force_authenticate(user=user)
    return c


@pytest.fixture
def unauth_client():
    return APIClient()


def _build_state(user_id: int) -> str:
    return create_oauth_state(
        flow=GOOGLE_DOCS_STATE_SALT,
        payload={"user_id": user_id},
        ttl_seconds=600,
    )


# ---------------------------------------------------------------------------
# GoogleDocsStatusView
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestGoogleDocsStatusView:
    def test_not_connected_returns_false(self, api_client):
        response = api_client.get(STATUS_URL)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["connected"] is False
        assert response.data["google_email"] is None

    def test_connected_returns_true(self, api_client, connection):
        response = api_client.get(STATUS_URL)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["connected"] is True
        assert response.data["google_email"] == "user@gmail.com"

    def test_inactive_connection_returns_false(self, api_client, connection, db):
        connection.is_active = False
        connection.save()
        response = api_client.get(STATUS_URL)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["connected"] is False

    def test_unauthenticated_returns_401(self, unauth_client):
        response = unauth_client.get(STATUS_URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ---------------------------------------------------------------------------
# GoogleDocsConnectView
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestGoogleDocsConnectView:
    @override_settings(
        GOOGLE_OAUTH_CLIENT_ID="",
        GOOGLE_OAUTH_CLIENT_SECRET="",
        GOOGLE_OAUTH_REDIRECT_URI="",
    )
    def test_missing_oauth_settings_returns_400(self, api_client):
        """When GOOGLE_OAUTH env vars are empty, returns 400."""
        response = api_client.get(CONNECT_URL)
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "error" in response.data
        assert "missing_settings" in response.data.get("details", {})

    @override_settings(
        GOOGLE_OAUTH_CLIENT_ID="test_client",
        GOOGLE_OAUTH_CLIENT_SECRET="test_secret",
        GOOGLE_OAUTH_REDIRECT_URI="http://localhost/api/google-docs/callback/",
        GOOGLE_DOCS_OAUTH_REDIRECT_URI="http://localhost/api/google-docs/callback/",    
    )
    def test_with_oauth_settings_returns_auth_url(self, api_client):
        with patch(
            "google_docs_integration.views.build_google_auth_url",
            return_value="https://accounts.google.com/o/oauth2/v2/auth?client_id=test",
        ):
            response = api_client.get(CONNECT_URL)
        assert response.status_code == status.HTTP_200_OK
        assert "auth_url" in response.data
        assert "state" in response.data

    def test_unauthenticated_returns_401(self, unauth_client):
        response = unauth_client.get(CONNECT_URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ---------------------------------------------------------------------------
# GoogleDocsCallbackView (AllowAny)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestGoogleDocsCallbackView:
    def test_missing_code_and_state_redirects_error(self, unauth_client):
        response = unauth_client.get(CALLBACK_URL)
        assert response.status_code == 302
        assert "google_docs_error=missing_code" in response["Location"]

    def test_missing_code_redirects_error(self, unauth_client):
        state = _build_state(999)
        response = unauth_client.get(CALLBACK_URL, {"state": state})
        assert response.status_code == 302
        assert "google_docs_error=missing_code" in response["Location"]

    def test_missing_state_redirects_error(self, unauth_client):
        response = unauth_client.get(CALLBACK_URL, {"code": "mycode"})
        assert response.status_code == 302
        assert "google_docs_error=missing_code" in response["Location"]

    def test_invalid_state_redirects_error(self, unauth_client):
        response = unauth_client.get(CALLBACK_URL, {"code": "code123", "state": "tampered_state"})
        assert response.status_code == 302
        assert "google_docs_error=invalid_state" in response["Location"]

    def test_expired_state_redirects_error(self, unauth_client, user):
        state = _build_state(user.id)
        with patch("core.services.oauth_state.signing.loads") as mock_loads:
            from django.core import signing

            mock_loads.side_effect = signing.SignatureExpired("expired")
            response = unauth_client.get(CALLBACK_URL, {"code": "code123", "state": state})
        assert response.status_code == 302
        assert "google_docs_error=state_expired" in response["Location"]

    def test_token_exchange_failure_redirects_error(self, unauth_client, user):
        state = _build_state(user.id)
        with patch("google_docs_integration.views.exchange_code_for_token") as mock_exchange:
            mock_exchange.side_effect = Exception("Network error")
            response = unauth_client.get(CALLBACK_URL, {"code": "code123", "state": state})
        assert response.status_code == 302
        assert "google_docs_error=token_exchange_failed" in response["Location"]

    def test_successful_callback_creates_connection(self, unauth_client, user):
        state = _build_state(user.id)
        with patch("google_docs_integration.views.exchange_code_for_token") as mock_exchange, \
             patch("google_docs_integration.views.fetch_google_email") as mock_email:
            mock_exchange.return_value = {
                "access_token": "new_access_token",
                "refresh_token": "new_refresh_token",
                "token_expiry": None,
            }
            mock_email.return_value = "user@gmail.com"
            response = unauth_client.get(CALLBACK_URL, {"code": "code123", "state": state})
        assert response.status_code == 302
        assert "open_google_docs=1" in response["Location"]
        assert GoogleDocsConnection.objects.filter(user=user, is_active=True).exists()

    def test_no_access_token_in_response_redirects_error(self, unauth_client, user):
        state = _build_state(user.id)
        with patch("google_docs_integration.views.exchange_code_for_token") as mock_exchange:
            mock_exchange.return_value = {"access_token": None}
            response = unauth_client.get(CALLBACK_URL, {"code": "code123", "state": state})
        assert response.status_code == 302
        assert "token_exchange_failed" in response["Location"]


# ---------------------------------------------------------------------------
# GoogleDocsDisconnectView
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestGoogleDocsDisconnectView:
    def test_no_connection_returns_success(self, api_client):
        response = api_client.post(DISCONNECT_URL)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["success"] is True

    def test_active_connection_is_deactivated(self, api_client, connection):
        response = api_client.post(DISCONNECT_URL)
        assert response.status_code == status.HTTP_200_OK
        connection.refresh_from_db()
        assert connection.is_active is False

    def test_unauthenticated_returns_401(self, unauth_client):
        response = unauth_client.post(DISCONNECT_URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ---------------------------------------------------------------------------
# GoogleDocsImportView
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestGoogleDocsImportView:
    def test_not_connected_returns_400(self, api_client):
        response = api_client.post(IMPORT_URL, {"document_id": "doc123"}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "Google Docs is not connected" in response.data["error"]

    def test_missing_document_id_returns_400(self, api_client, connection):
        response = api_client.post(IMPORT_URL, {}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_successful_import_returns_content(self, api_client, connection):
        with patch("google_docs_integration.views.run_google_api_with_token_retry") as mock_api:
            mock_api.return_value = ("My Doc Title", "Plain text content", "<p>HTML content</p>")
            response = api_client.post(IMPORT_URL, {"document_id": "doc123"}, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert response.data["title"] == "My Doc Title"
        assert response.data["content"] == "Plain text content"
        assert response.data["content_html"] == "<p>HTML content</p>"

    def test_google_401_returns_400(self, api_client, connection):
        http_error = requests.HTTPError()
        http_error.response = MagicMock(status_code=401)
        with patch("google_docs_integration.views.run_google_api_with_token_retry") as mock_api:
            mock_api.side_effect = http_error
            response = api_client.post(IMPORT_URL, {"document_id": "doc123"}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "expired" in response.data["error"].lower()

    def test_google_403_returns_403(self, api_client, connection):
        http_error = requests.HTTPError()
        http_error.response = MagicMock(status_code=403)
        with patch("google_docs_integration.views.run_google_api_with_token_retry") as mock_api:
            mock_api.side_effect = http_error
            response = api_client.post(IMPORT_URL, {"document_id": "doc123"}, format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_google_404_returns_404(self, api_client, connection):
        http_error = requests.HTTPError()
        http_error.response = MagicMock(status_code=404)
        with patch("google_docs_integration.views.run_google_api_with_token_retry") as mock_api:
            mock_api.side_effect = http_error
            response = api_client.post(IMPORT_URL, {"document_id": "doc123"}, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_request_exception_returns_503(self, api_client, connection):
        with patch("google_docs_integration.views.run_google_api_with_token_retry") as mock_api:
            mock_api.side_effect = requests.RequestException("timeout")
            response = api_client.post(IMPORT_URL, {"document_id": "doc123"}, format="json")
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    def test_unauthenticated_returns_401(self, unauth_client):
        response = unauth_client.post(IMPORT_URL, {"document_id": "doc123"}, format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ---------------------------------------------------------------------------
# GoogleDocsDocumentListView
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestGoogleDocsDocumentListView:
    def test_not_connected_returns_400(self, api_client):
        response = api_client.get(DOCUMENTS_URL)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_returns_document_list(self, api_client, connection):
        docs = [{"id": "doc1", "name": "Report"}, {"id": "doc2", "name": "Notes"}]
        with patch("google_docs_integration.views.run_google_api_with_token_retry", return_value=docs):
            response = api_client.get(DOCUMENTS_URL)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["items"] == docs

    def test_invalid_page_size_uses_default(self, api_client, connection):
        docs = []
        with patch("google_docs_integration.views.run_google_api_with_token_retry", return_value=docs):
            response = api_client.get(DOCUMENTS_URL, {"pageSize": "notanumber"})
        assert response.status_code == status.HTTP_200_OK

    def test_google_api_error_returns_error(self, api_client, connection):
        http_error = requests.HTTPError()
        http_error.response = MagicMock(status_code=401)
        with patch("google_docs_integration.views.run_google_api_with_token_retry") as mock_api:
            mock_api.side_effect = http_error
            response = api_client.get(DOCUMENTS_URL)
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_unauthenticated_returns_401(self, unauth_client):
        response = unauth_client.get(DOCUMENTS_URL)
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ---------------------------------------------------------------------------
# GoogleDocsExportView
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestGoogleDocsExportView:
    def test_not_connected_returns_400(self, api_client):
        response = api_client.post(EXPORT_URL, {"decision_id": 1}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_decision_not_found_returns_404(self, api_client, connection):
        with patch("google_docs_integration.views.run_google_api_with_token_retry") as mock_api:
            mock_api.return_value = {"documentId": "newdoc123"}
            response = api_client.post(EXPORT_URL, {"decision_id": 99999}, format="json")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_missing_decision_id_returns_400(self, api_client, connection):
        response = api_client.post(EXPORT_URL, {}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_unauthenticated_returns_401(self, unauth_client):
        response = unauth_client.post(EXPORT_URL, {"decision_id": 1}, format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ---------------------------------------------------------------------------
# GoogleDocsRawExportView
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestGoogleDocsRawExportView:
    def test_not_connected_returns_400(self, api_client):
        response = api_client.post(
            EXPORT_RAW_URL,
            {"title": "Test Doc", "content": "Hello"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_successful_export_returns_doc_payload(self, api_client, connection):
        doc_payload = {"documentId": "abc123", "title": "Test Doc"}
        with patch("google_docs_integration.views.run_google_api_with_token_retry", return_value=doc_payload):
            response = api_client.post(
                EXPORT_RAW_URL,
                {"title": "Test Doc", "content": "Hello world"},
                format="json",
            )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["documentId"] == "abc123"

    def test_request_exception_returns_503(self, api_client, connection):
        with patch("google_docs_integration.views.run_google_api_with_token_retry") as mock_api:
            mock_api.side_effect = requests.RequestException("timeout")
            response = api_client.post(
                EXPORT_RAW_URL,
                {"title": "Test", "content": "Hi"},
                format="json",
            )
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    def test_google_502_error(self, api_client, connection):
        http_error = requests.HTTPError()
        http_error.response = MagicMock(status_code=500)
        with patch("google_docs_integration.views.run_google_api_with_token_retry") as mock_api:
            mock_api.side_effect = http_error
            response = api_client.post(
                EXPORT_RAW_URL,
                {"title": "Test", "content": "Hi"},
                format="json",
            )
        assert response.status_code == status.HTTP_502_BAD_GATEWAY

    def test_unauthenticated_returns_401(self, unauth_client):
        response = unauth_client.post(EXPORT_RAW_URL, {"title": "T", "content": "C"}, format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ---------------------------------------------------------------------------
# GoogleSheetsImportView
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestGoogleSheetsImportView:
    def test_not_connected_returns_400(self, api_client):
        response = api_client.post(
            SHEETS_IMPORT_URL,
            {"sheet_url": "https://docs.google.com/spreadsheets/d/abc123/edit"},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_successful_import_returns_matrix(self, api_client, connection):
        matrix = [["A", "B"], ["1", "2"]]
        with patch(
            "google_docs_integration.views.run_google_api_with_token_retry",
            return_value={
                "title": "My Sheet",
                "matrix": matrix,
                "cells": [],
                "default_font_size": None,
                "worksheet_name": "Sheet1",
                "google_sheet_id": 0,
                "google_spreadsheet_id": "sheet123",
            },
        ):
            response = api_client.post(
                SHEETS_IMPORT_URL,
                {"sheet_url": "https://docs.google.com/spreadsheets/d/sheet123/edit"},
                format="json",
            )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["title"] == "My Sheet"
        assert response.data["matrix"] == matrix
        assert response.data["cells"] == []
        assert response.data["google_spreadsheet_id"] == "sheet123"
        assert response.data["google_sheet_id"] == 0
        assert response.data["worksheet_name"] == "Sheet1"

    def test_sheet_url_extracts_id(self, api_client, connection):
        """Ensure URL regex extracts spreadsheet ID correctly."""
        with patch("google_docs_integration.views.run_google_api_with_token_retry") as mock_api:
            mock_api.return_value = {
                "title": "Sheet",
                "matrix": [],
                "cells": [],
                "default_font_size": None,
                "worksheet_name": "Sheet1",
                "google_sheet_id": 0,
                "google_spreadsheet_id": "mySheetId123",
}
            response = api_client.post(
                SHEETS_IMPORT_URL,
                {"sheet_url": "https://docs.google.com/spreadsheets/d/mySheetId123/edit#gid=0"},
                format="json",
            )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["google_spreadsheet_id"] == "mySheetId123"
        assert response.data["google_sheet_id"] == 0
        assert response.data["worksheet_name"] == "Sheet1"

    def test_fetch_google_sheet_reads_first_tab_id(self):
        from google_docs_integration.services import fetch_google_sheet

        meta = {
            "properties": {"title": "Workbook", "defaultFormat": {"textFormat": {"fontSize": 10}}},
            "sheets": [
                {"properties": {"title": "Budget", "sheetId": 42}},
                {"properties": {"title": "Other", "sheetId": 99}},
            ],
        }
        grid = {"sheets": [{"data": [{"rowData": []}]}]}

        def fake_get(url, headers=None, params=None, timeout=None):
            response = MagicMock()
            response.raise_for_status.return_value = None
            response.json.return_value = grid if params and params.get("includeGridData") else meta
            return response

        with patch("google_docs_integration.services.requests.get", side_effect=fake_get) as mocked:
            result = fetch_google_sheet("sheet123", "token")

        assert result["worksheet_name"] == "Budget"
        assert result["google_sheet_id"] == 42
        assert result["google_spreadsheet_id"] == "sheet123"
        range_call = mocked.call_args_list[1]
        assert range_call.kwargs["params"]["ranges"] == "Budget"

    def test_grid_parser_keeps_raw_numbers_and_user_formats(self):
        from google_docs_integration.services import _grid_to_matrix_and_cells

        grid = {
            "sheets": [{
                "data": [{
                    "rowData": [{
                        "values": [
                            {"effectiveValue": {"stringValue": "Ada"}},
                            {
                                "formattedValue": "50%",
                                "effectiveValue": {"numberValue": 0.5},
                                "userEnteredFormat": {
                                    "numberFormat": {"type": "PERCENT", "pattern": "0%"},
                                    "textFormat": {"bold": True},
                                },
                            },
                        ]
                    }]
                }]
            }]
        }
        matrix, cells = _grid_to_matrix_and_cells(grid)
        assert matrix == [["Ada", "0.5"]]
        assert cells == [
            {"row": 0, "col": 0, "userEnteredFormat": None},
            {
                "row": 0,
                "col": 1,
                "userEnteredFormat": {
                    "numberFormat": {"type": "PERCENT", "pattern": "0%"},
                    "textFormat": {"bold": True},
                },
            },
        ]


    def test_grid_parser_keeps_merge_anchor_only(self):
        from google_docs_integration.services import _grid_to_matrix_and_cells

        grid = {
            "sheets": [{
                "merges": [{
                    "sheetId": 0,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 2,
                }],
                "data": [{
                    "rowData": [{
                        "values": [
                            {
                                "effectiveValue": {"stringValue": "Hello"},
                                "userEnteredFormat": {"textFormat": {"bold": True}},
                            },
                            {
                                "effectiveValue": {"stringValue": "slave"},
                                "userEnteredFormat": {"textFormat": {"italic": True}},
                            },
                        ]
                    }]
                }],
            }]
        }
        matrix, cells = _grid_to_matrix_and_cells(grid)
        assert matrix == [["Hello", ""]]
        assert len(cells) == 1
        assert cells[0]["row"] == 0
        assert cells[0]["col"] == 0
        assert cells[0]["userEnteredFormat"]["textFormat"]["bold"] is True
        assert all(cell["col"] != 1 for cell in cells)

    def test_grid_parser_keeps_scientific_raw_number(self):
        from google_docs_integration.services import _grid_to_matrix_and_cells

        grid = {
            "sheets": [{
                "data": [{
                    "rowData": [{
                        "values": [{
                            "formattedValue": "1.50E+03",
                            "effectiveValue": {"numberValue": 1500},
                            "userEnteredFormat": {
                                "numberFormat": {"type": "SCIENTIFIC", "pattern": "0.00E+00"},
                            },
                        }]
                    }]
                }]
            }]
        }
        matrix, cells = _grid_to_matrix_and_cells(grid)
        assert matrix == [["1500"]]
        assert cells[0]["userEnteredFormat"]["numberFormat"]["type"] == "SCIENTIFIC"

    def test_grid_parser_keeps_formula_text_and_plain_numbers(self):
        from google_docs_integration.services import _grid_to_matrix_and_cells

        grid = {
            "sheets": [{
                "data": [{
                    "rowData": [
                        {"values": [{"effectiveValue": {"numberValue": 10}}]},
                        {"values": [{"effectiveValue": {"numberValue": 20}}]},
                        {"values": [{
                            "userEnteredValue": {"formulaValue": "=A1+A2"},
                            "effectiveValue": {"numberValue": 30},
                            "formattedValue": "30",
                        }]},
                    ]
                }]
            }]
        }
        matrix, cells = _grid_to_matrix_and_cells(grid)
        assert matrix == [["10"], ["20"], ["=A1+A2"]]
        assert cells == [
            {"row": 0, "col": 0, "userEnteredFormat": None},
            {"row": 1, "col": 0, "userEnteredFormat": None},
            {"row": 2, "col": 0, "userEnteredFormat": None},
        ]

    def test_google_api_error(self, api_client, connection):
        http_error = requests.HTTPError()
        http_error.response = MagicMock(status_code=403)
        with patch("google_docs_integration.views.run_google_api_with_token_retry") as mock_api:
            mock_api.side_effect = http_error
            response = api_client.post(
                SHEETS_IMPORT_URL,
                {"sheet_url": "https://docs.google.com/spreadsheets/d/abc/edit"},
                format="json",
            )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_missing_sheet_url_returns_400(self, api_client, connection):
        response = api_client.post(SHEETS_IMPORT_URL, {}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_unauthenticated_returns_401(self, unauth_client):
        response = unauth_client.post(
            SHEETS_IMPORT_URL,
            {"sheet_url": "https://docs.google.com/spreadsheets/d/x"},
            format="json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ---------------------------------------------------------------------------
# GoogleSheetsExportView
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestGoogleSheetsExportView:
    def test_not_connected_returns_400(self, api_client):
        response = api_client.post(
            SHEETS_EXPORT_URL,
            {"title": "My Sheet", "matrix": [["A", "B"]]},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_successful_export_returns_result(self, api_client, connection):
        result = {"spreadsheetId": "sheet123", "spreadsheetUrl": "https://docs.google.com/..."}
        with patch("google_docs_integration.views.run_google_api_with_token_retry", return_value=result):
            response = api_client.post(
                SHEETS_EXPORT_URL,
                {"title": "My Sheet", "matrix": [["A", "B"], ["1", "2"]]},
                format="json",
            )
        assert response.status_code == status.HTTP_200_OK
        assert response.data["spreadsheetId"] == "sheet123"

    def test_request_exception_returns_503(self, api_client, connection):
        with patch("google_docs_integration.views.run_google_api_with_token_retry") as mock_api:
            mock_api.side_effect = requests.RequestException("timeout")
            response = api_client.post(
                SHEETS_EXPORT_URL,
                {"title": "Sheet", "matrix": []},
                format="json",
            )
        assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE

    def test_unauthenticated_returns_401(self, unauth_client):
        response = unauth_client.post(
            SHEETS_EXPORT_URL,
            {"title": "S", "matrix": []},
            format="json",
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_service_writes_formula_matrix_and_repeat_cell(self):
        from google_docs_integration.services import export_to_google_sheet

        created = {"spreadsheetId": "sheet123", "sheets": [{"properties": {"sheetId": 0}}]}
        create_response = MagicMock(status_code=200)
        create_response.json.return_value = created
        create_response.raise_for_status.return_value = None
        batch_response = MagicMock(status_code=200)
        batch_response.raise_for_status.return_value = None
        values_response = MagicMock(status_code=200)
        values_response.raise_for_status.return_value = None

        matrix = [["Name", "Score", ""], ["Ada", "0.5", "=B2*2"]]
        formats = [{
            "row": 0,
            "column": 0,
            "userEnteredFormat": {"textFormat": {"bold": True}},
        }]

        with patch("google_docs_integration.services.requests.post", side_effect=[create_response, batch_response]) as post:
            with patch("google_docs_integration.services.requests.put", return_value=values_response) as put:
                result = export_to_google_sheet("Budget", matrix, "token", formats)

        assert result["spreadsheet_id"] == "sheet123"
        assert post.call_args_list[0].args[0] == "https://sheets.googleapis.com/v4/spreadsheets"
        assert put.call_args.kwargs["json"]["values"] == matrix
        assert put.call_args.kwargs["params"]["valueInputOption"] == "USER_ENTERED"
        repeat = post.call_args_list[1].kwargs["json"]["requests"][0]["repeatCell"]
        requests_body = post.call_args_list[1].kwargs["json"]["requests"]
        columns = requests_body[-2]["updateDimensionProperties"]
        rows = requests_body[-1]["updateDimensionProperties"]
        assert columns["range"]["dimension"] == "COLUMNS"
        assert columns["range"]["endIndex"] == 3
        assert columns["properties"]["pixelSize"] == 117
        assert rows["range"]["dimension"] == "ROWS"
        assert rows["range"]["endIndex"] == 2
        assert rows["properties"]["pixelSize"] == 24
        assert repeat["range"]["sheetId"] == 0
        assert repeat["range"]["startRowIndex"] == 0
        assert repeat["range"]["endRowIndex"] == 1
        assert repeat["cell"]["userEnteredFormat"]["textFormat"]["bold"] is True
        assert repeat["fields"] == "userEnteredFormat.textFormat.bold"



# ---------------------------------------------------------------------------
# Helper function coverage: _google_api_error_response
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestGoogleApiErrorResponse:
    """Verify all branches of the error helper via the import view."""

    def test_none_response_returns_502(self, api_client, connection):
        http_error = requests.HTTPError()
        http_error.response = None  # no response object
        with patch("google_docs_integration.views.run_google_api_with_token_retry") as mock_api:
            mock_api.side_effect = http_error
            response = api_client.post(IMPORT_URL, {"document_id": "doc1"}, format="json")
        assert response.status_code == status.HTTP_502_BAD_GATEWAY


def test_google_xlsx_export_reads_the_sheet_id_and_does_not_write():
    from unittest.mock import MagicMock, patch
    from google_docs_integration.services import export_google_spreadsheet_xlsx

    source = type("Source", (), {"google_spreadsheet_id": "book-id", "google_sheet_id": 0})()
    calls = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append((url, params))
        response = MagicMock()
        response.raise_for_status.return_value = None
        if url.endswith("/book-id"):
            response.json.return_value = {
                "sheets": [
                    {"properties": {"sheetId": 4, "title": "Other"}},
                    {"properties": {"sheetId": 0, "title": "Budget"}},
                ]
            }
            response.content = b""
            return response
        response.content = b"PK-exported"
        response.json.return_value = {}
        return response

    with patch("google_docs_integration.services.requests.get", side_effect=fake_get), patch(
        "google_docs_integration.services.requests.post",
    ) as posted:
        content, title = export_google_spreadsheet_xlsx(source, "token")
    assert content == b"PK-exported"
    assert title == "Budget"
    assert calls[1][0].endswith("/book-id/export")
    assert calls[1][1]["mimeType"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    posted.assert_not_called()