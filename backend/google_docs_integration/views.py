import logging
import re
import requests
from django.conf import settings
from django.shortcuts import get_object_or_404, redirect
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from decision.models import Decision
from core.services.oauth_state import OAuthStateExpired, OAuthStateInvalid, create_oauth_state, validate_oauth_state
from core.slug_mixins import resolve_lookup_kwargs
from spreadsheet.access import get_accessible_spreadsheet_or_404
from spreadsheet.import_diff import diff_sheet
from spreadsheet.models import Sheet, SheetImportSource
from .google_sheets_patch_export import (
    GoogleBlankTabCleanupError,
    GooglePatchExportError,
    export_copied_google_sheet,
    export_converted_excel_sheet
)
from .models import GoogleDocsConnection
from .serializers import (
    GoogleDocsConnectSerializer,
    GoogleDocsExportSerializer,
    GoogleDocsRawExportSerializer,
    GoogleDocsImportSerializer,
    GoogleDocsStatusSerializer,
    GoogleSheetsImportSerializer,
    GoogleSheetsExportSerializer,
)
from .services import (
    build_google_auth_url,
    create_google_doc,
    exchange_code_for_token,
    export_to_google_sheet,
    fetch_document_text,
    fetch_google_email,
    fetch_google_sheet,
    list_google_docs,
    run_google_api_with_token_retry,
)

logger = logging.getLogger(__name__)

GOOGLE_SHEET_URL_ID_REGEX = re.compile(r'/spreadsheets/(?:u/\d+/)?d/([a-zA-Z0-9_-]+)', re.IGNORECASE)
GOOGLE_DOCS_STATE_SALT = "google-docs-oauth-state"
GOOGLE_DOCS_STATE_MAX_AGE_SECONDS = 600

def _build_oauth_state(user) -> str:
    return create_oauth_state(
        flow=GOOGLE_DOCS_STATE_SALT,
        payload={"user_id": user.id},
        ttl_seconds=GOOGLE_DOCS_STATE_MAX_AGE_SECONDS,
    )


def _google_api_error_response(exc: requests.HTTPError) -> Response:
    status_code = exc.response.status_code if exc.response is not None else None
    if status_code == 401:
        return Response(
            {"error": "Google session expired. Please reconnect Google Docs in your account Integrations tab."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if status_code == 403:
        return Response(
            {"error": "Google denied access. If importing an external doc, please reconnect Google Docs in your account Integrations tab to grant updated permissions."},
            status=status.HTTP_403_FORBIDDEN,
        )
    if status_code == 404:
        return Response(
            {"error": "Google Doc not found. Please verify the link or document ID."},
            status=status.HTTP_404_NOT_FOUND,
        )
    return Response(
        {"error": "Google Docs request failed. Please try again in a moment."},
        status=status.HTTP_502_BAD_GATEWAY,
    )


class GoogleDocsStatusView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        connection = GoogleDocsConnection.objects.filter(
            user=request.user,
            is_active=True,
        ).first()
        payload = {
            "connected": bool(connection and connection.get_access_token()),
            "google_email": connection.google_email if connection else None,
        }
        serializer = GoogleDocsStatusSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.data)


class GoogleDocsConnectView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        missing_settings = []
        if not settings.GOOGLE_OAUTH_CLIENT_ID:
            missing_settings.append("GOOGLE_CLIENT_ID (or GOOGLE_OAUTH_CLIENT_ID)")
        if not settings.GOOGLE_OAUTH_CLIENT_SECRET:
            missing_settings.append("GOOGLE_CLIENT_SECRET (or GOOGLE_OAUTH_CLIENT_SECRET)")
        if not settings.GOOGLE_OAUTH_REDIRECT_URI:
            missing_settings.append("GOOGLE_OAUTH_REDIRECT_URI (or GOOGLE_REDIRECT_URI)")

        if missing_settings:
            return Response(
                {
                    "error": "Google OAuth is not configured.",
                    "details": {
                        "missing_settings": missing_settings,
                        "expected_redirect_uri_path": "/api/google-docs/callback/",
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        state = _build_oauth_state(request.user)
        payload = {
            "auth_url": build_google_auth_url(state),
            "state": state,
        }
        serializer = GoogleDocsConnectSerializer(data=payload)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.data)


class GoogleDocsCallbackView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        if not code or not state:
            return redirect(f"{settings.FRONTEND_URL}/integrations?google_docs_error=missing_code")

        try:
            payload = validate_oauth_state(
                state,
                expected_flow=GOOGLE_DOCS_STATE_SALT,
                ttl_seconds=GOOGLE_DOCS_STATE_MAX_AGE_SECONDS,
            )
        except OAuthStateExpired:
            return redirect(f"{settings.FRONTEND_URL}/integrations?google_docs_error=state_expired")
        except OAuthStateInvalid:
            return redirect(f"{settings.FRONTEND_URL}/integrations?google_docs_error=invalid_state")

        user_id = payload.get("user_id")
        if not user_id:
            return redirect(f"{settings.FRONTEND_URL}/integrations?google_docs_error=invalid_state")

        try:
            token_data = exchange_code_for_token(code)
            access_token = token_data.get("access_token")
            if not access_token:
                raise ValueError("No access token returned by Google.")
            email = fetch_google_email(access_token)
            connection, _ = GoogleDocsConnection.objects.get_or_create(user_id=user_id)
            connection.google_email = email
            connection.set_access_token(access_token)
            connection.set_refresh_token(token_data.get("refresh_token") or connection.get_refresh_token())
            connection.token_expiry = token_data.get("token_expiry")
            connection.is_active = True
            connection.save()
        except Exception:
            return redirect(f"{settings.FRONTEND_URL}/integrations?google_docs_error=token_exchange_failed")

        return redirect(f"{settings.FRONTEND_URL}/integrations?open_google_docs=1")


class GoogleDocsDisconnectView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        connection = GoogleDocsConnection.objects.filter(user=request.user, is_active=True).first()
        if not connection:
            return Response({"success": True}, status=status.HTTP_200_OK)
        connection.is_active = False
        connection.set_access_token(None)
        connection.set_refresh_token(None)
        connection.save(update_fields=["is_active", "encrypted_access_token", "encrypted_refresh_token", "updated_at"])
        return Response({"success": True}, status=status.HTTP_200_OK)


class GoogleDocsImportView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = GoogleDocsImportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        connection = GoogleDocsConnection.objects.filter(user=request.user, is_active=True).first()
        if not connection or not connection.get_access_token():
            return Response({"error": "Google Docs is not connected."}, status=status.HTTP_400_BAD_REQUEST)

        document_id = serializer.validated_data["document_id"]
        try:
            title, plain_text, rich_html = run_google_api_with_token_retry(
                connection, lambda token: fetch_document_text(document_id, token)
            )
        except requests.HTTPError as exc:
            return _google_api_error_response(exc)
        except requests.RequestException:
            return Response(
                {"error": "Google Docs service is temporarily unavailable. Please try again."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        decision_id = serializer.validated_data.get("decision_id")
        if decision_id:
            decision = Decision.objects.filter(id=decision_id, is_deleted=False).first()
            if decision:
                decision.title = title or decision.title
                decision.context_summary = plain_text[:10000]
                decision.last_edited_by = request.user
                decision.save(update_fields=["title", "context_summary", "last_edited_by", "updated_at"])

        return Response(
            {
                "title": title,
                "content": plain_text,
                "content_html": rich_html,
                "decision_id": decision_id,
            },
            status=status.HTTP_200_OK,
        )


class GoogleDocsDocumentListView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        connection = GoogleDocsConnection.objects.filter(user=request.user, is_active=True).first()
        if not connection or not connection.get_access_token():
            return Response({"error": "Google Docs is not connected."}, status=status.HTTP_400_BAD_REQUEST)

        page_size_param = request.query_params.get("pageSize")
        try:
            page_size = int(page_size_param) if page_size_param else 20
        except (TypeError, ValueError):
            page_size = 20
        try:
            documents = run_google_api_with_token_retry(
                connection, lambda token: list_google_docs(token, page_size=page_size)
            )
        except requests.HTTPError as exc:
            return _google_api_error_response(exc)
        except requests.RequestException:
            return Response(
                {"error": "Google Docs service is temporarily unavailable. Please try again."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response({"items": documents}, status=status.HTTP_200_OK)


class GoogleDocsExportView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = GoogleDocsExportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        connection = GoogleDocsConnection.objects.filter(user=request.user, is_active=True).first()
        if not connection or not connection.get_access_token():
            return Response({"error": "Google Docs is not connected."}, status=status.HTTP_400_BAD_REQUEST)

        decision = Decision.objects.filter(
            id=serializer.validated_data["decision_id"],
            is_deleted=False,
        ).first()
        if not decision:
            return Response({"error": "Decision not found."}, status=status.HTTP_404_NOT_FOUND)

        title = serializer.validated_data.get("title") or decision.title or f"Decision #{decision.id}"
        content = (
            f"Title: {decision.title or 'Untitled'}\n\n"
            f"Context Summary:\n{decision.context_summary or ''}\n\n"
            f"Reasoning:\n{decision.reasoning or ''}\n\n"
            f"Risk Level: {decision.risk_level or ''}\n"
            f"Confidence: {decision.confidence or ''}\n"
        )
        try:
            doc_payload = run_google_api_with_token_retry(
                connection,
                lambda token: create_google_doc(title=title, content=content, access_token=token),
            )
        except requests.HTTPError as exc:
            return _google_api_error_response(exc)
        except requests.RequestException:
            return Response(
                {"error": "Google Docs service is temporarily unavailable. Please try again."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(doc_payload, status=status.HTTP_200_OK)


class GoogleDocsRawExportView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = GoogleDocsRawExportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        connection = GoogleDocsConnection.objects.filter(user=request.user, is_active=True).first()
        if not connection or not connection.get_access_token():
            return Response({"error": "Google Docs is not connected."}, status=status.HTTP_400_BAD_REQUEST)

        title = serializer.validated_data.get("title") or "Exported Document"
        content = serializer.validated_data.get("content") or ""
        try:
            doc_payload = run_google_api_with_token_retry(
                connection,
                lambda token: create_google_doc(title=title, content=content, access_token=token),
            )
        except requests.HTTPError as exc:
            return _google_api_error_response(exc)
        except requests.RequestException:
            return Response(
                {"error": "Google Docs service is temporarily unavailable. Please try again."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(doc_payload, status=status.HTTP_200_OK)


class GoogleSheetsImportView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = GoogleSheetsImportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        connection = GoogleDocsConnection.objects.filter(user=request.user, is_active=True).first()
        if not connection or not connection.get_access_token():
            return Response({"error": "Google Docs is not connected."}, status=status.HTTP_400_BAD_REQUEST)

        sheet_url = serializer.validated_data["sheet_url"]
        match = GOOGLE_SHEET_URL_ID_REGEX.search(sheet_url)
        spreadsheet_id = match.group(1) if match else sheet_url.strip()

        try:
            payload = run_google_api_with_token_retry(
                connection, lambda token: fetch_google_sheet(spreadsheet_id, token)
            )
        except requests.HTTPError as exc:
            return _google_api_error_response(exc)
        except requests.RequestException:
            return Response(
                {"error": "Google Sheets service is temporarily unavailable. Please try again."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(
            {
                "title": payload["title"],
                "matrix": payload["matrix"],
                "cells": payload["cells"],
                "default_font_size": payload["default_font_size"],
                # NEW
                "google_spreadsheet_id": payload["google_spreadsheet_id"],
                "google_sheet_id": payload["google_sheet_id"],
                "worksheet_name": payload["worksheet_name"],
            },
            status=status.HTTP_200_OK,
        )


class GoogleSheetsExportView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = GoogleSheetsExportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        connection = GoogleDocsConnection.objects.filter(user=request.user, is_active=True).first()
        if not connection or not connection.get_access_token():
            return Response({"error": "Google Docs is not connected."}, status=status.HTTP_400_BAD_REQUEST)

        title = serializer.validated_data.get("title") or "Exported Sheet"
        matrix = serializer.validated_data.get("matrix") or []
        formats = serializer.validated_data.get("formats") or []    
        spreadsheet_lookup = (serializer.validated_data.get("spreadsheet_id") or "").strip()
        sheet_id = serializer.validated_data.get("sheet_id")

        def rebuild():
            return run_google_api_with_token_retry(
                connection,
                lambda token: export_to_google_sheet(title, matrix, token, formats),
            )
        try:
            result = None
            if spreadsheet_lookup and sheet_id is not None:
                spreadsheet = get_accessible_spreadsheet_or_404(
                    request.user,
                    **resolve_lookup_kwargs(spreadsheet_lookup),
                )
                sheet = get_object_or_404(
                    Sheet,
                    id=sheet_id,
                    spreadsheet=spreadsheet,
                    is_deleted=False,
                )
                source = (
                    SheetImportSource.objects.filter(
                        sheet=sheet,
                        is_deleted=False,
                        origin=SheetImportSource.Origin.GOOGLE,
                    ).first()
                )
                source_spreadsheet_id = (source.google_spreadsheet_id or "").strip() if source else ""
                if source is not None and source_spreadsheet_id and source.google_sheet_id is not None:
                    diff = diff_sheet(source)
                    if not diff["requires_rebuild"]:
                        try:
                            result = run_google_api_with_token_retry(
                                connection,
                                lambda token: export_copied_google_sheet(
                                    source,
                                    diff,
                                    title,
                                    token,
                                ),
                            )
                        except GoogleBlankTabCleanupError:
                            logger.exception(
                                "Google copied-sheet finalization failed for sheet_id=%s source_id=%s",
                                sheet.id,
                                source.id,
                            )
                            return Response(
                                    {"error": "Google Sheets export could not be finished. Please try again."},
                                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                                )
                        except GooglePatchExportError:
                            logger.exception(
                                "Google copy failed for sheet_id=%s source_id=%s",
                                sheet.id,
                                source.id,
                            )
                            result = rebuild()
                if result is None:
                    excel_source = (
                        SheetImportSource.objects.filter(
                            sheet=sheet,
                            is_deleted=False,
                            origin=SheetImportSource.Origin.EXCEL,
                        ).first()
                    )
                    if excel_source is not None and excel_source.file:
                        diff = diff_sheet(excel_source)
                        if not diff["requires_rebuild"]:
                            try:
                                result = run_google_api_with_token_retry(
                                    connection,
                                    lambda token: export_converted_excel_sheet(
                                        excel_source,
                                        diff,
                                        title,
                                        token,
                                    ),
                                )
                            except GoogleBlankTabCleanupError:
                                logger.exception(
                                    "Excel conversion cleanup failed for sheet_id=%s source_id=%s",
                                    sheet.id,
                                    excel_source.id,
                                )
                                return Response(
                                    {"error": "Google Sheets export could not be finished. Please try again."},
                                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                                )
                            except GooglePatchExportError:
                                logger.exception(
                                    "Excel conversion failed for sheet_id=%s source_id=%s",
                                    sheet.id,
                                    excel_source.id,
                                )
                                result = rebuild()
                            
            if result is None:
                result = rebuild()
        except requests.HTTPError as exc:
            return _google_api_error_response(exc)
        except requests.RequestException:
            return Response(
                {"error": "Google Sheets service is temporarily unavailable. Please try again."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return Response(result, status=status.HTTP_200_OK)
