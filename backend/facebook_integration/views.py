"""HTTP views for the Facebook / Meta integration.

Flow (redirect OAuth, confirmed working via spike 2026-04-23):

  GET  /api/facebook_integration/connect/     -> { authorize_url, state }  (frontend redirects browser to it)
  GET  /api/facebook_integration/callback/    -> handles Facebook redirect, stores connection, 302 back to frontend
  GET  /api/facebook_integration/status/      -> current user connection summary + ad accounts
  POST /api/facebook_integration/disconnect/  -> soft disconnect
  POST /api/facebook_integration/sync/        -> trigger immediate refresh (re-fetch /me/businesses + ad accounts)
  POST /api/facebook_integration/ad_accounts/<pk>/link_project/ -> attach MediaJira project
"""

import logging
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import redirect
from rest_framework import status as drf_status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import Project, ProjectMember
from core.tenant_context import current_tenant_schema
from core.services.oauth_state import OAuthStateExpired, OAuthStateInvalid

from .access import (
    get_accessible_meta_ad_account_or_404,
    get_accessible_meta_ad_accounts,
    user_can_manage_meta_ad_account,
)
from .models import FacebookConnection, MetaAdAccount
from .serializers import (
    ConnectInitSerializer,
    FacebookConnectionStatusSerializer,
    LinkProjectSerializer,
    MetaAdAccountSerializer,
)
from . import services


logger = logging.getLogger(__name__)


def _parse_optional_int(raw) -> int | None:
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _parse_positive_int(raw, default: int, *, max_value: int | None = None) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    if value < 1:
        value = default
    if max_value is not None:
        value = min(value, max_value)
    return value


def _status_payload(request) -> dict:
    user = request.user
    project_id = _parse_optional_int(request.query_params.get("project_id"))
    try:
        connection = FacebookConnection.objects.get(user=user, is_active=True)
    except FacebookConnection.DoesNotExist:
        connection = None

    if project_id is None:
        ad_accounts = MetaAdAccount.objects.filter(
            connection__user=user,
            connection__is_active=True,
        ).select_related("connection", "connection__user")
    else:
        ad_accounts = get_accessible_meta_ad_accounts(
            user,
            project_id=project_id,
        )
    ad_accounts = ad_accounts.order_by("name", "id")

    if connection is None and not ad_accounts.exists():
        return {"connected": False, "ad_accounts": []}

    return {
        "connected": True,
        "fb_user_name": connection.fb_user_name if connection else "",
        "fb_email": connection.fb_email if connection else None,
        "business_id": connection.business_id if connection else "",
        "business_name": connection.business_name if connection else "",
        "token_expires_at": connection.token_expires_at if connection else None,
        "last_synced_at": connection.last_synced_at if connection else None,
        "ad_accounts": ad_accounts,
    }


def _serialized_status_payload(request) -> dict:
    serializer = FacebookConnectionStatusSerializer(
        _status_payload(request),
        context={"request": request},
    )
    return serializer.data


class FacebookConnectView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        state = services.build_oauth_state(
            request.user.id,
            project_id=request.query_params.get("project_id"),
        )
        url = services.build_authorize_url(state)
        serializer = ConnectInitSerializer({"authorize_url": url, "state": state})
        return Response(serializer.data)


class FacebookCallbackView(APIView):
    """Facebook redirects here with `code` + `state`.

    We verify the state (CSRF + session binding), exchange the code, persist
    the connection, and redirect the browser to the Integrations page.
    AllowAny because the Facebook server-to-server redirect doesn't carry our
    session cookie in every browser context; state signature is the guard.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        frontend = settings.FRONTEND_URL.rstrip("/")
        back = f"{frontend}/integrations"

        error = request.GET.get("error")
        if error:
            query = {"facebook_error": error, "facebook_error_description": request.GET.get("error_description", "")}
            return redirect(f"{back}?{urlencode(query)}")

        code = request.GET.get("code")
        raw_state = request.GET.get("state", "")
        if not code or not raw_state:
            return redirect(f"{back}?facebook_error=missing_code_or_state")

        try:
            state_payload = services.unpack_oauth_state(raw_state)
        except OAuthStateExpired:
            return redirect(f"{back}?facebook_error=state_expired")
        except OAuthStateInvalid:
            return redirect(f"{back}?facebook_error=invalid_state")

        from django.contrib.auth import get_user_model

        User = get_user_model()
        try:
            user = User.objects.get(pk=state_payload.get("user_id"))
        except User.DoesNotExist:
            return redirect(f"{back}?facebook_error=user_not_found")

        try:
            services.store_connection_from_code(user, code)
        except requests.HTTPError as err:
            logger.warning("Facebook token exchange failed: %s", err)
            return redirect(f"{back}?facebook_error=token_exchange_failed")
        except Exception as err:  # pragma: no cover - defensive
            logger.exception("Facebook callback failed")
            return redirect(f"{back}?facebook_error=server_error")

        return redirect(f"{back}?facebook_connected=1")


class FacebookStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_serialized_status_payload(request))


class MetaAdAccountListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        project_id = _parse_optional_int(request.query_params.get("project_id"))
        queryset = get_accessible_meta_ad_accounts(
            request.user,
            project_id=project_id,
        )

        search = (request.query_params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search)
                | Q(meta_account_id__icontains=search)
                | Q(business_id__icontains=search)
            )

        queryset = queryset.order_by("name", "id")

        page = _parse_positive_int(request.query_params.get("page"), 1)
        page_size = _parse_positive_int(
            request.query_params.get("page_size"),
            5,
            max_value=100,
        )
        paginator = Paginator(queryset, page_size)
        page_obj = paginator.get_page(page)
        serializer = MetaAdAccountSerializer(
            page_obj,
            many=True,
            context={"request": request},
        )

        return Response(
            {
                "count": paginator.count,
                "page": page,
                "page_size": page_size,
                "results": serializer.data,
            }
        )


class FacebookDisconnectView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            connection = FacebookConnection.objects.get(user=request.user, is_active=True)
        except FacebookConnection.DoesNotExist:
            return Response({"connected": False}, status=drf_status.HTTP_200_OK)
        services.disconnect(connection)
        return Response({"connected": False})


class FacebookSyncView(APIView):
    """Trigger immediate /me + business + ad account refresh for current user."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            connection = FacebookConnection.objects.get(user=request.user, is_active=True)
        except FacebookConnection.DoesNotExist:
            return Response(
                {"detail": "Not connected."}, status=drf_status.HTTP_400_BAD_REQUEST
            )
        access_token = connection.get_access_token()
        if not access_token:
            return Response(
                {"detail": "Access token missing. Please reconnect."},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )
        try:
            me = services.fetch_me(access_token)
            businesses = services.fetch_my_businesses(access_token)
        except requests.HTTPError as err:
            logger.warning("Facebook sync failed: %s", err)
            connection.last_sync_error = str(err)[:500]
            connection.save(update_fields=["last_sync_error", "updated_at"])
            return Response(
                {"detail": "Facebook API call failed. Try reconnecting."},
                status=drf_status.HTTP_502_BAD_GATEWAY,
            )

        # Update identity fields if they moved
        connection.fb_user_id = str(me.get("id", connection.fb_user_id))
        connection.fb_user_name = me.get("name", connection.fb_user_name) or connection.fb_user_name
        connection.fb_email = me.get("email") or connection.fb_email

        if businesses and not connection.business_id:
            connection.business_id = str(businesses[0].get("id", ""))
            connection.business_name = businesses[0].get("name", "") or ""

        connection.last_sync_error = ""
        connection.save(update_fields=[
            "fb_user_id", "fb_user_name", "fb_email",
            "business_id", "business_name", "last_sync_error", "updated_at",
        ])

        if connection.business_id:
            services._sync_ad_accounts_for_business(connection, access_token, connection.business_id)
        return Response(_serialized_status_payload(request))


class MetaAdAccountLinkProjectView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk: int):
        serializer = LinkProjectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        account = get_accessible_meta_ad_account_or_404(request.user, pk)

        target_project = None
        target_project_id = serializer.validated_data.get("project_id")
        if target_project_id:
            try:
                target_project = Project.objects.get(
                    pk=target_project_id,
                    is_deleted=False,
                )
            except Project.DoesNotExist:
                return Response(
                    {"detail": "Project not found."},
                    status=drf_status.HTTP_404_NOT_FOUND,
                )
            if not ProjectMember.objects.filter(
                user=request.user,
                project=target_project,
                is_active=True,
            ).exists():
                return Response(
                    {"detail": "You do not have access to this project."},
                    status=drf_status.HTTP_403_FORBIDDEN,
                )

        if not user_can_manage_meta_ad_account(
            request.user,
            account,
            target_project=target_project,
        ):
            return Response(
                {"detail": "You do not have permission to manage this ad account."},
                status=drf_status.HTTP_403_FORBIDDEN,
            )

        account.project = target_project
        account.project_schema = current_tenant_schema() if target_project else "public"
        account.save(update_fields=["project", "project_schema", "updated_at"])
        return Response(
            MetaAdAccountSerializer(
                account,
                context={"request": request},
            ).data
        )
