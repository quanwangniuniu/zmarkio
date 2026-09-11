import hashlib
import hmac
import json
import time
import requests
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.utils import timezone
from datetime import timedelta
from rest_framework.test import APIClient
from rest_framework import status

from core.models import Organization, Project, ProjectMember
from meetings.models import Meeting, MeetingTypeDefinition

from .models import ZoomCredential, ZoomMeetingData
from .services import (
    EVENT_MEETING_ENDED,
    EVENT_RECORDING_COMPLETED,
    EVENT_SUMMARY_COMPLETED,
    get_authorization_url,
    exchange_code_for_token,
    refresh_access_token,
    get_valid_credential,
    save_token_for_user,
    create_zoom_meeting,
    find_zoom_meeting_data_for_webhook,
    sync_zoom_meeting_for_event,
)
from .post_meeting_payload import build_zoom_post_meeting_payload, compute_user_feedback_code
from .tasks import process_zoom_webhook_event
from .webhook import encrypt_zoom_url_validation_token
from .views import _build_zoom_oauth_state

User = get_user_model()


# ─────────────────────────────────────────────
# Helper function: quickly create a test ZoomCredential
# ─────────────────────────────────────────────
def make_credential(user, expired=False):
    """
    Helper function: create a ZoomCredential for the test user
    expired=True  → token expired (for testing refresh logic)
    expired=False → token valid (default)
    """
    expires_at = timezone.now() + timedelta(hours=1)   # default 1 hour later
    if expired:
        expires_at = timezone.now() - timedelta(hours=1)  # set to 1 hour ago, expired

    credential = ZoomCredential(
        user=user,
        token_expires_at=expires_at,
    )
    credential.set_tokens("test_access_token", "test_refresh_token")
    credential.save()
    return credential


# ─────────────────────────────────────────────
# 1. Model tests
# ─────────────────────────────────────────────
class ZoomCredentialModelTest(TestCase):

    def setUp(self):
        # create a clean test user for each test method
        self.user, created = User.objects.get_or_create(
            username="testuser",
            defaults={"email": "test@example.com"},
        )
        if created:
            self.user.set_password("testpass123")
            self.user.save()

    def test_create_credential(self):
        """test if ZoomCredential can be created normally"""
        credential = make_credential(self.user)
        self.assertEqual(credential.user, self.user)
        self.assertEqual(credential.get_access_token(), "test_access_token")
        self.assertEqual(credential.get_refresh_token(), "test_refresh_token")
        self.assertNotIn(
            "test_access_token",
            credential.encrypted_access_token,
        )

    def test_one_to_one_constraint(self):
        """
        test OneToOne constraint: a user cannot have two ZoomCredential
        second creation should raise an exception
        """
        make_credential(self.user)
        with self.assertRaises(Exception):
            make_credential(self.user)  # duplicate creation, should raise an exception

    def test_str_representation(self):
        """test if __str__ method returns the correct string"""
        credential = make_credential(self.user)
        self.assertIn("test@example.com", str(credential))


# ─────────────────────────────────────────────
# 2. Services tests
# ─────────────────────────────────────────────
class ZoomServiceTest(TestCase):

    def setUp(self):
        self.user, created = User.objects.get_or_create(
            username="testuser",
            defaults={"email": "test@example.com"},
        )
        if created:
            self.user.set_password("testpass123")
            self.user.save()

    def test_get_authorization_url_contains_client_id(self):
        """
        test if the generated authorization URL contains the correct parameters
        patch to temporarily replace settings.ZOOM_CLIENT_ID, do not rely on real environment variables
        """
        with patch("zoom_integration.services.settings") as mock_settings:
            mock_settings.ZOOM_CLIENT_ID = "test_client_id"
            mock_settings.ZOOM_REDIRECT_URI = "http://localhost/api/v1/zoom/callback/"

            url = get_authorization_url("random_state_123")

            self.assertIn("test_client_id", url)
            self.assertIn("random_state_123", url)
            self.assertIn("zoom.us/oauth/authorize", url)

    @patch("zoom_integration.services.requests.post")
    def test_exchange_code_for_token_success(self, mock_post):
        """
        test if the token can be successfully exchanged for the authorization code
        mock requests.post, avoid sending real HTTP requests
        """
        # mock the token data returned by Zoom
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "new_access_token",
            "refresh_token": "new_refresh_token",
            "expires_in": 3600,
        }
        mock_post.return_value = mock_response

        with patch("zoom_integration.services.settings") as mock_settings:
            mock_settings.ZOOM_CLIENT_ID = "test_client_id"
            mock_settings.ZOOM_CLIENT_SECRET = "test_secret"
            mock_settings.ZOOM_REDIRECT_URI = "http://localhost/callback/"

            result = exchange_code_for_token("auth_code_123")

        self.assertEqual(result["access_token"], "new_access_token")
        self.assertEqual(result["refresh_token"], "new_refresh_token")

    @patch("zoom_integration.services.requests.post")
    def test_refresh_access_token(self, mock_post):
        """
        test if the refresh_token can successfully refresh the access_token
        """
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "access_token": "refreshed_access_token",
            "refresh_token": "refreshed_refresh_token",
            "expires_in": 3600,
        }
        mock_post.return_value = mock_response

        credential = make_credential(self.user, expired=True)

        with patch("zoom_integration.services.settings") as mock_settings:
            mock_settings.ZOOM_CLIENT_ID = "id"
            mock_settings.ZOOM_CLIENT_SECRET = "secret"

            updated = refresh_access_token(credential)

        # verify if the token in the database has been updated
        self.assertEqual(updated.get_access_token(), "refreshed_access_token")
        self.assertEqual(updated.get_refresh_token(), "refreshed_refresh_token")

    def test_get_valid_credential_raises_if_not_connected(self):
        """
        test if get_valid_credential should raise ValueError when user is not connected to Zoom
        """
        with self.assertRaises(ValueError) as ctx:
            get_valid_credential(self.user)

        self.assertIn("not connected to Zoom", str(ctx.exception))

    @patch("zoom_integration.services.refresh_access_token")
    def test_get_valid_credential_refreshes_when_expired(self, mock_refresh):
        """
        test if get_valid_credential should automatically call refresh_access_token when token is expired
        """
        credential = make_credential(self.user, expired=True)
        mock_refresh.return_value = credential  # return the same object after refresh

        get_valid_credential(self.user)

        # verify if refresh function is called once
        mock_refresh.assert_called_once_with(credential)

    def test_get_valid_credential_no_refresh_when_valid(self):
        """
        test if get_valid_credential should not trigger refresh when token is valid
        """
        make_credential(self.user, expired=False)

        with patch("zoom_integration.services.refresh_access_token") as mock_refresh:
            get_valid_credential(self.user)
            mock_refresh.assert_not_called()  # verify if refresh function is not called

    @patch("zoom_integration.services.requests.post")
    def test_get_valid_credential_refresh_revoked_deletes_credential(self, mock_post):
        """Refresh failure with 400/401 from Zoom token endpoint removes stored credential."""
        for status_code in (400, 401):
            with self.subTest(status_code=status_code):
                make_credential(self.user, expired=True)

                mock_resp = MagicMock()
                mock_resp.status_code = status_code

                def _raise():
                    err = requests.HTTPError()
                    err.response = mock_resp
                    raise err

                mock_resp.raise_for_status = _raise
                mock_post.return_value = mock_resp

                with patch("zoom_integration.services.settings") as mock_settings:
                    mock_settings.ZOOM_CLIENT_ID = "id"
                    mock_settings.ZOOM_CLIENT_SECRET = "secret"

                    with self.assertRaises(PermissionError) as ctx:
                        get_valid_credential(self.user)

                self.assertIn("reconnect", str(ctx.exception).lower())
                self.assertFalse(ZoomCredential.objects.filter(user=self.user).exists())

    @patch("zoom_integration.services.requests.post")
    def test_get_valid_credential_refresh_other_http_error_keeps_credential(self, mock_post):
        """Non-auth refresh failures propagate; credential is not deleted."""
        make_credential(self.user, expired=True)

        mock_resp = MagicMock()
        mock_resp.status_code = 502

        def _raise():
            err = requests.HTTPError()
            err.response = mock_resp
            raise err

        mock_resp.raise_for_status = _raise
        mock_post.return_value = mock_resp

        with patch("zoom_integration.services.settings") as mock_settings:
            mock_settings.ZOOM_CLIENT_ID = "id"
            mock_settings.ZOOM_CLIENT_SECRET = "secret"

            with self.assertRaises(requests.HTTPError):
                get_valid_credential(self.user)

        self.assertTrue(ZoomCredential.objects.filter(user=self.user).exists())

    def test_save_token_for_user_creates_new(self):
        """test if save_token_for_user should create a new credential when user does not have one"""
        token_data = {
            "access_token": "acc_token",
            "refresh_token": "ref_token",
            "expires_in": 3600,
        }
        credential = save_token_for_user(self.user, token_data)

        self.assertEqual(credential.get_access_token(), "acc_token")
        self.assertEqual(ZoomCredential.objects.filter(user=self.user).count(), 1)

    def test_save_token_for_user_updates_existing(self):
        """test if save_token_for_user should update the existing credential when user already has one"""
        make_credential(self.user)  # create one first

        token_data = {
            "access_token": "updated_token",
            "refresh_token": "updated_refresh",
            "expires_in": 3600,
        }
        save_token_for_user(self.user, token_data)

        # verify if there is only one credential in the database, and the content has been updated
        self.assertEqual(ZoomCredential.objects.filter(user=self.user).count(), 1)
        self.assertEqual(
            ZoomCredential.objects.get(user=self.user).get_access_token(),
            "updated_token",
        )

    @patch("zoom_integration.services.requests.post")
    @patch("zoom_integration.services.get_valid_credential")
    def test_create_zoom_meeting_success(self, mock_get_cred, mock_post):
        """
        test if create_zoom_meeting should return the correct data
        """
        # mock a valid credential
        mock_credential = MagicMock()
        mock_credential.get_access_token.return_value = "valid_token"
        mock_get_cred.return_value = mock_credential

        # mock the meeting data returned by Zoom
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "id": "123456789",
            "topic": "Test Meeting",
            "join_url": "https://zoom.us/j/123456789",
            "start_url": "https://zoom.us/s/123456789",
            "start_time": "2026-04-10T10:00:00Z",
            "duration": 60,
        }
        mock_post.return_value = mock_response

        result = create_zoom_meeting(
            user=self.user,
            topic="Test Meeting",
            start_time="2026-04-10T10:00:00Z",
            duration=60,
        )

        self.assertEqual(result["join_url"], "https://zoom.us/j/123456789")
        self.assertEqual(result["topic"], "Test Meeting")

    @patch("zoom_integration.services.requests.post")
    @patch("zoom_integration.services.get_valid_credential")
    def test_create_zoom_meeting_401_raises_permission_error(self, mock_get_cred, mock_post):
        """
        test if create_zoom_meeting should raise PermissionError when Zoom API returns 401
        """
        mock_credential = MagicMock()
        mock_credential.get_access_token.return_value = "expired_token"
        mock_get_cred.return_value = mock_credential

        mock_response = MagicMock()
        mock_response.status_code = 401  # mock token expired
        mock_post.return_value = mock_response

        with self.assertRaises(PermissionError):
            create_zoom_meeting(
                user=self.user,
                topic="Test",
                start_time="2026-04-10T10:00:00Z",
            )


# ─────────────────────────────────────────────
# 3. API Views tests
# ─────────────────────────────────────────────
class ZoomAPITest(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.user, created = User.objects.get_or_create(
            username="testuser",
            defaults={"email": "test@example.com"},
        )
        if created:
            self.user.set_password("testpass123")
            self.user.save()
        # force authentication, skip token validation, focus on testing business logic
        self.client.force_authenticate(user=self.user)

    # ── Status API ──
    def test_status_not_connected(self):
        """test if status should return connected=false when user is not connected to Zoom"""
        response = self.client.get("/api/v1/zoom/status/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["connected"])

    def test_status_connected(self):
        """test if status should return connected=true when user is connected to Zoom"""
        make_credential(self.user)
        response = self.client.get("/api/v1/zoom/status/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["connected"])

    def test_status_requires_authentication(self):
        """test if status should return 401 when user is not authenticated"""
        self.client.force_authenticate(user=None)  # un-authenticate
        response = self.client.get("/api/v1/zoom/status/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    # ── Connect API ──
    def test_connect_returns_auth_url(self):
        """test if connect should return the correct auth_url"""
        with patch("zoom_integration.services.settings") as mock_settings:
            mock_settings.ZOOM_CLIENT_ID = "test_id"
            mock_settings.ZOOM_REDIRECT_URI = "http://localhost/callback/"

            response = self.client.get("/api/v1/zoom/connect/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("auth_url", response.data)
        self.assertIn("zoom.us", response.data["auth_url"])

    # ── Disconnect API ──
    def test_disconnect_removes_credential(self):
        """test if disconnect should remove the user's ZoomCredential"""
        make_credential(self.user)
        response = self.client.delete("/api/v1/zoom/disconnect/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(ZoomCredential.objects.filter(user=self.user).exists())

    def test_disconnect_when_not_connected(self):
        """test if disconnect should return 200 when user is not connected to Zoom"""
        response = self.client.delete("/api/v1/zoom/disconnect/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    # ── Create Meeting API ──
    @patch("zoom_integration.views.create_zoom_meeting")
    def test_create_meeting_success(self, mock_create):
        """test if create_meeting should return the correct data"""
        mock_create.return_value = {
            "id": "123456789",
            "uuid": "abc/uuid+instance",
            "topic": "Sprint Review",
            "join_url": "https://zoom.us/j/123456789",
            "start_url": "https://zoom.us/s/123456789",
            "start_time": "2026-04-10T10:00:00Z",
            "duration": 60,
        }

        response = self.client.post("/api/v1/zoom/meetings/", {
            "topic": "Sprint Review",
            "start_time": "2026-04-10T10:00:00Z",
            "duration": 60,
        }, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIn("join_url", response.data)
        self.assertEqual(response.data["topic"], "Sprint Review")
        self.assertEqual(response.data["meeting_id"], "123456789")
        self.assertEqual(response.data["uuid"], "abc/uuid+instance")

    @patch("zoom_integration.views.create_zoom_meeting")
    def test_create_meeting_invalid_zoom_payload_returns_502(self, mock_create):
        """Malformed Zoom API body must not leak raw errors; same shape as other create failures."""
        mock_create.return_value = {"id": "123"}  # missing required meeting fields

        response = self.client.post(
            "/api/v1/zoom/meetings/",
            {
                "topic": "Sprint Review",
                "start_time": "2026-04-10T10:00:00Z",
                "duration": 60,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(response.data.get("code"), "zoom_meeting_create_failed")

    def test_create_meeting_missing_topic(self):
        """test if create_meeting should return 400 when missing required field topic"""
        response = self.client.post("/api/v1/zoom/meetings/", {
            "start_time": "2026-04-10T10:00:00Z",
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_meeting_invalid_time_format(self):
        """test if create_meeting should return 400 when time format is invalid"""
        response = self.client.post("/api/v1/zoom/meetings/", {
            "topic": "Test",
            "start_time": "not-a-date",
        }, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("zoom_integration.views.create_zoom_meeting")
    def test_create_meeting_user_not_connected(self, mock_create):
        """test if create_meeting should return 403 when user is not connected to Zoom"""
        mock_create.side_effect = ValueError("user is not connected to Zoom")

        response = self.client.post("/api/v1/zoom/meetings/", {
            "topic": "Test",
            "start_time": "2026-04-10T10:00:00Z",
        }, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch("zoom_integration.views.create_zoom_meeting")
    def test_create_meeting_token_expired(self, mock_create):
        """test if create_meeting should return 401 when token is expired"""
        mock_create.side_effect = PermissionError("Zoom authorization has expired")

        response = self.client.post("/api/v1/zoom/meetings/", {
            "topic": "Test",
            "start_time": "2026-04-10T10:00:00Z",
        }, format="json")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    @patch("zoom_integration.views.create_zoom_meeting")
    def test_create_meeting_unexpected_error_hides_details(self, mock_create):
        """502 responses must not echo raw exception text (URLs, stack hints)."""
        mock_create.side_effect = RuntimeError("upstream https://api.zoom.us/v2 leaked")

        response = self.client.post(
            "/api/v1/zoom/meetings/",
            {
                "topic": "Test",
                "start_time": "2026-04-10T10:00:00Z",
                "duration": 60,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(response.data.get("code"), "zoom_meeting_create_failed")
        self.assertNotIn("zoom.us", response.data.get("error", ""))
        self.assertNotIn("zoom.us", str(response.data))

    # ── Callback API ──
    @patch("zoom_integration.views.exchange_code_for_token")
    @patch("zoom_integration.views.save_token_for_user")
    def test_callback_success(self, mock_save, mock_exchange):
        """test if callback should redirect to frontend success page when OAuth callback is successful"""
        mock_exchange.return_value = {
            "access_token": "token",
            "refresh_token": "refresh",
            "expires_in": 3600,
        }
        mock_save.return_value = MagicMock()

        state = _build_zoom_oauth_state(self.user)

        response = self.client.get(
            "/api/v1/zoom/callback/",
            {"code": "auth_code_123", "state": state}
        )

        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        self.assertIn("zoom_connected=true", response["Location"])

    def test_callback_invalid_state(self):
        """test if callback should redirect to error page when state is invalid"""
        response = self.client.get(
            "/api/v1/zoom/callback/",
            {"code": "some_code", "state": "not-a-valid-signature"}
        )

        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        self.assertIn("zoom_error=invalid_state", response["Location"])

    @patch("core.services.oauth_state.signing.loads")
    def test_callback_state_expired(self, mock_loads):
        """test if callback should redirect when signed state is past max_age"""
        mock_loads.side_effect = signing.SignatureExpired("expired")

        state = _build_zoom_oauth_state(self.user)
        response = self.client.get(
            "/api/v1/zoom/callback/",
            {"code": "auth_code_123", "state": state},
        )

        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        self.assertIn("zoom_error=state_expired", response["Location"])

    def test_callback_user_denied(self):
        """test if callback should redirect to error page when user denies authorization"""
        state = _build_zoom_oauth_state(self.user)

        response = self.client.get(
            "/api/v1/zoom/callback/",
            {"error": "access_denied", "state": state}
        )

        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        self.assertIn("zoom_error=access_denied", response["Location"])


# ─────────────────────────────────────────────
# 4. Zoom meeting link API (ZoomMeetingData)
# ─────────────────────────────────────────────
class ZoomMeetingLinkAPITest(TestCase):
    """POST /api/v1/zoom/meetings/link/ — persist Zoom identity on a MediaJira meeting."""

    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="linkuser",
            email="link@example.com",
            password="testpass123",
        )
        self.other_user = User.objects.create_user(
            username="otherlink",
            email="other@example.com",
            password="testpass123",
        )
        self.org = Organization.objects.create(name="OrgZ", slug="org-z")
        self.project = Project.objects.create(
            name="Proj Z",
            organization=self.org,
        )
        self.other_project = Project.objects.create(
            name="Other Z",
            organization=self.org,
        )
        ProjectMember.objects.create(user=self.user, project=self.project, is_active=True)
        ProjectMember.objects.create(
            user=self.other_user, project=self.other_project, is_active=True
        )
        self.planning = MeetingTypeDefinition.objects.create(
            project=self.project,
            slug="planning",
            label="Planning",
        )
        self.meeting = Meeting.objects.create(
            project=self.project,
            title="M",
            type_definition=self.planning,
            objective="obj",
        )
        self.client.force_authenticate(user=self.user)

    def _link_url(self):
        return "/api/v1/zoom/meetings/link/"

    def test_link_creates_zoom_meeting_data(self):
        response = self.client.post(
            self._link_url(),
            {
                "project_id": self.project.id,
                "meeting_id": self.meeting.id,
                "zoom_meeting_id": "998877665544",
                "zoom_uuid": "raw+uuid/with/slashes",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        zd = ZoomMeetingData.objects.get(meeting=self.meeting)
        self.assertEqual(zd.zoom_meeting_id, "998877665544")
        self.assertEqual(zd.zoom_uuid, "raw+uuid/with/slashes")
        self.assertEqual(zd.sync_state, ZoomMeetingData.SyncState.NEVER)
        self.assertEqual(zd.zoom_host_user_id, self.user.id)

    def test_link_updates_existing_row(self):
        ZoomMeetingData.objects.create(
            meeting=self.meeting,
            zoom_meeting_id="111",
            zoom_uuid="old-uuid",
        )
        response = self.client.post(
            self._link_url(),
            {
                "project_id": self.project.id,
                "meeting_id": self.meeting.id,
                "zoom_meeting_id": "222",
                "zoom_uuid": "new-uuid",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(ZoomMeetingData.objects.filter(meeting=self.meeting).count(), 1)
        zd = ZoomMeetingData.objects.get(meeting=self.meeting)
        self.assertEqual(zd.zoom_meeting_id, "222")
        self.assertEqual(zd.zoom_uuid, "new-uuid")
        self.assertEqual(zd.zoom_host_user_id, self.user.id)

    def test_link_missing_zoom_meeting_id_returns_400(self):
        response = self.client.post(
            self._link_url(),
            {
                "project_id": self.project.id,
                "meeting_id": self.meeting.id,
                "zoom_uuid": "x",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_link_non_member_forbidden(self):
        self.client.force_authenticate(user=self.other_user)
        response = self.client.post(
            self._link_url(),
            {
                "project_id": self.project.id,
                "meeting_id": self.meeting.id,
                "zoom_meeting_id": "1",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_link_wrong_project_returns_404(self):
        response = self.client.post(
            self._link_url(),
            {
                "project_id": self.other_project.id,
                "meeting_id": self.meeting.id,
                "zoom_meeting_id": "1",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


# ─────────────────────────────────────────────
# 5. Webhook lookup + HTTP handler
# ─────────────────────────────────────────────
class ZoomWebhookLookupTest(TestCase):
    """find_zoom_meeting_data_for_webhook — composite vs meeting_id-only vs ambiguity."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="whuser",
            email="wh@example.com",
            password="testpass123",
        )
        self.org = Organization.objects.create(name="OrgWH", slug="org-wh")
        self.project = Project.objects.create(
            name="Proj WH",
            organization=self.org,
        )
        ProjectMember.objects.create(user=self.user, project=self.project, is_active=True)
        self.planning = MeetingTypeDefinition.objects.create(
            project=self.project,
            slug="planning",
            label="Planning",
        )
        self.m1 = Meeting.objects.create(
            project=self.project,
            title="M1",
            type_definition=self.planning,
            objective="o",
        )
        self.m2 = Meeting.objects.create(
            project=self.project,
            title="M2",
            type_definition=self.planning,
            objective="o",
        )
        ZoomMeetingData.objects.create(
            meeting=self.m1,
            zoom_meeting_id="100",
            zoom_uuid="uuid-a",
            zoom_host_user=self.user,
        )
        ZoomMeetingData.objects.create(
            meeting=self.m2,
            zoom_meeting_id="100",
            zoom_uuid="uuid-b",
            zoom_host_user=self.user,
        )

    def test_composite_match_unique(self):
        row = find_zoom_meeting_data_for_webhook("100", "uuid-a")
        self.assertIsNotNone(row)
        self.assertEqual(row.meeting_id, self.m1.id)

    def test_meeting_id_only_ambiguous_returns_none(self):
        self.assertIsNone(find_zoom_meeting_data_for_webhook("100", None))

    def test_meeting_id_only_unique(self):
        ZoomMeetingData.objects.filter(meeting=self.m2).delete()
        row = find_zoom_meeting_data_for_webhook("100", "")
        self.assertIsNotNone(row)
        self.assertEqual(row.meeting_id, self.m1.id)


class ZoomWebhookAPITest(TestCase):
    """POST /api/v1/zoom/webhook/"""

    def setUp(self):
        self.client = APIClient()

    @override_settings(ZOOM_WEBHOOK_SECRET_TOKEN="test_secret")
    def test_url_validation_returns_encrypted_token(self):
        body = {"event": "endpoint.url_validation", "payload": {"plainToken": "plain123"}}
        response = self.client.post(
            "/api/v1/zoom/webhook/",
            json.dumps(body),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["plainToken"], "plain123")
        self.assertEqual(
            response.data["encryptedToken"],
            encrypt_zoom_url_validation_token("plain123", "test_secret"),
        )

    @override_settings(ZOOM_WEBHOOK_SECRET_TOKEN="")
    def test_url_validation_without_secret_503(self):
        body = {"event": "endpoint.url_validation", "payload": {"plainToken": "plain123"}}
        response = self.client.post(
            "/api/v1/zoom/webhook/",
            json.dumps(body),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

    @override_settings(ZOOM_WEBHOOK_SECRET_TOKEN="test_secret")
    @patch("zoom_integration.views.find_zoom_meeting_data_for_webhook")
    @patch("zoom_integration.views.process_zoom_webhook_event.delay")
    def test_signed_event_enqueues(self, mock_delay, mock_find_row):
        mock_find_row.return_value = MagicMock(pk=42)
        secret = "test_secret"
        body_dict = {
            "event": "meeting.ended",
            "payload": {"object": {"id": "999", "uuid": "uu-1"}},
        }
        raw = json.dumps(body_dict).encode()
        ts = str(int(time.time()))
        msg = f"v0:{ts}:{raw.decode()}"
        digest = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
        sig = f"v0={digest}"
        response = self.client.post(
            "/api/v1/zoom/webhook/",
            data=raw,
            content_type="application/json",
            HTTP_X_ZM_REQUEST_TIMESTAMP=ts,
            HTTP_X_ZM_SIGNATURE=sig,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data.get("received"))
        mock_delay.assert_called_once_with(
            event_type="meeting.ended",
            zoom_meeting_data_id=42,
            webhook_uuid="uu-1",
        )

    @override_settings(ZOOM_WEBHOOK_SECRET_TOKEN="test_secret")
    def test_invalid_signature_401(self):
        body_dict = {"event": "meeting.ended", "payload": {"object": {"id": "1"}}}
        raw = json.dumps(body_dict).encode()
        ts = str(int(time.time()))
        response = self.client.post(
            "/api/v1/zoom/webhook/",
            data=raw,
            content_type="application/json",
            HTTP_X_ZM_REQUEST_TIMESTAMP=ts,
            HTTP_X_ZM_SIGNATURE="v0=deadbeef",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    @override_settings(ZOOM_WEBHOOK_SECRET_TOKEN="test_secret")
    def test_stale_timestamp_401(self):
        secret = "test_secret"
        body_dict = {"event": "meeting.ended", "payload": {"object": {"id": "1"}}}
        raw = json.dumps(body_dict).encode()
        old_ts = str(int(time.time()) - 400)
        msg = f"v0:{old_ts}:{raw.decode()}"
        digest = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
        sig = f"v0={digest}"
        response = self.client.post(
            "/api/v1/zoom/webhook/",
            data=raw,
            content_type="application/json",
            HTTP_X_ZM_REQUEST_TIMESTAMP=old_ts,
            HTTP_X_ZM_SIGNATURE=sig,
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    @override_settings(ZOOM_WEBHOOK_SECRET_TOKEN="test_secret")
    @patch("zoom_integration.views.find_zoom_meeting_data_for_webhook")
    @patch("zoom_integration.views.process_zoom_webhook_event.delay")
    def test_duplicate_post_still_200(self, mock_delay, mock_find_row):
        mock_find_row.return_value = MagicMock(pk=7)
        secret = "test_secret"
        body_dict = {
            "event": "recording.completed",
            "payload": {"object": {"id": "888"}},
        }
        raw = json.dumps(body_dict).encode()
        ts = str(int(time.time()))
        msg = f"v0:{ts}:{raw.decode()}"
        digest = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
        sig = f"v0={digest}"
        for _ in range(2):
            response = self.client.post(
                "/api/v1/zoom/webhook/",
                data=raw,
                content_type="application/json",
                HTTP_X_ZM_REQUEST_TIMESTAMP=ts,
                HTTP_X_ZM_SIGNATURE=sig,
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(mock_delay.call_count, 2)

    @override_settings(ZOOM_WEBHOOK_SECRET_TOKEN="test_secret")
    @patch("zoom_integration.views.find_zoom_meeting_data_for_webhook", return_value=None)
    @patch("zoom_integration.views.process_zoom_webhook_event.delay")
    def test_signed_event_no_row_skips_enqueue(self, mock_delay, _mock_find):
        secret = "test_secret"
        body_dict = {
            "event": "meeting.ended",
            "payload": {"object": {"id": "999", "uuid": "uu-1"}},
        }
        raw = json.dumps(body_dict).encode()
        ts = str(int(time.time()))
        msg = f"v0:{ts}:{raw.decode()}"
        digest = hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()
        sig = f"v0={digest}"
        response = self.client.post(
            "/api/v1/zoom/webhook/",
            data=raw,
            content_type="application/json",
            HTTP_X_ZM_REQUEST_TIMESTAMP=ts,
            HTTP_X_ZM_SIGNATURE=sig,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_delay.assert_not_called()


# ─────────────────────────────────────────────
# 5b. Post-meeting payload (no DB)
# ─────────────────────────────────────────────
class ZoomPostMeetingPayloadFeedbackTest(SimpleTestCase):
    def test_partial_sync_state_yields_no_user_feedback_code(self):
        from zoom_integration.post_meeting_payload import compute_user_feedback_code

        zd = ZoomMeetingData(
            meeting_status=ZoomMeetingData.MeetingStatus.ENDED,
            sync_state=ZoomMeetingData.SyncState.PARTIAL,
            summary_status=ZoomMeetingData.SummaryStatus.NOT_APPLICABLE,
            recording_status=ZoomMeetingData.RecordingStatus.UNKNOWN,
        )
        self.assertIsNone(compute_user_feedback_code(zd))


# ─────────────────────────────────────────────
# 6. Step D: sync services + task
# ─────────────────────────────────────────────
class ZoomSyncStepDTest(TestCase):
    """Post-meeting sync: mocked Zoom REST."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="syncuser",
            email="sync@example.com",
            password="testpass123",
        )
        make_credential(self.user)
        self.org = Organization.objects.create(name="OrgSync", slug="org-sync")
        self.project = Project.objects.create(
            name="Proj Sync",
            organization=self.org,
        )
        ProjectMember.objects.create(user=self.user, project=self.project, is_active=True)
        self.planning = MeetingTypeDefinition.objects.create(
            project=self.project,
            slug="planning",
            label="Planning",
        )
        self.meeting = Meeting.objects.create(
            project=self.project,
            title="SyncM",
            type_definition=self.planning,
            objective="o",
        )
        self.zd = ZoomMeetingData.objects.create(
            meeting=self.meeting,
            zoom_meeting_id="111222333",
            zoom_uuid="inst-uuid-1",
            zoom_host_user=self.user,
        )

    def _json_response(self, data: dict):
        m = MagicMock()
        m.status_code = 200
        m.content = b"{}"
        m.json.return_value = data
        m.raise_for_status = MagicMock()
        return m

    @patch("zoom_integration.services.requests.get")
    def test_layer1_meeting_ended_updates_db(self, mock_get):
        past = {
            "start_time": "2026-04-10T10:00:00Z",
            "end_time": "2026-04-10T11:00:00Z",
            "duration": 60,
        }
        participants_page = {"total_records": 2, "participants": []}

        def side_effect(url, **_kwargs):
            if "/participants" in url:
                return self._json_response(participants_page)
            return self._json_response(past)

        mock_get.side_effect = side_effect

        sync_zoom_meeting_for_event(self.zd.pk, EVENT_MEETING_ENDED, "")

        self.zd.refresh_from_db()
        self.assertEqual(self.zd.sync_state, ZoomMeetingData.SyncState.OK)
        self.assertEqual(self.zd.sync_error, "")
        self.assertIsNotNone(self.zd.last_sync_at)
        self.assertEqual(self.zd.meeting_status, ZoomMeetingData.MeetingStatus.ENDED)
        self.assertEqual(self.zd.duration_minutes, 60)
        self.assertEqual(self.zd.actual_participants_count, 2)

    @patch("zoom_integration.services.requests.get")
    def test_layer1_participants_subrequest_failure_still_saves_past_metrics(self, mock_get):
        past = {
            "start_time": "2026-04-10T10:00:00Z",
            "end_time": "2026-04-10T11:00:00Z",
            "duration": 60,
        }

        def side_effect(url, **_kwargs):
            if "/participants" in url:
                resp = MagicMock()
                resp.status_code = 403
                resp.content = b"{}"

                def raise_403():
                    exc = requests.exceptions.HTTPError()
                    exc.response = resp
                    raise exc

                resp.raise_for_status = raise_403
                return resp
            return self._json_response(past)

        mock_get.side_effect = side_effect

        sync_zoom_meeting_for_event(self.zd.pk, EVENT_MEETING_ENDED, "")

        self.zd.refresh_from_db()
        self.assertEqual(self.zd.sync_state, ZoomMeetingData.SyncState.PARTIAL)
        self.assertEqual(
            self.zd.sync_error,
            "Participant report unavailable (access denied).",
        )
        self.assertLessEqual(len(self.zd.sync_error), 500)
        self.assertIsNotNone(self.zd.last_sync_at)
        self.assertEqual(self.zd.meeting_status, ZoomMeetingData.MeetingStatus.ENDED)
        self.assertEqual(self.zd.duration_minutes, 60)
        self.assertIsNone(self.zd.actual_participants_count)

    @patch("zoom_integration.services.requests.get")
    def test_layer1_participants_subrequest_generic_failure_curated_message(self, mock_get):
        past = {
            "start_time": "2026-04-10T10:00:00Z",
            "end_time": "2026-04-10T11:00:00Z",
            "duration": 60,
        }

        def side_effect(url, **_kwargs):
            if "/participants" in url:
                raise requests.exceptions.ConnectionError("upstream reset")
            return self._json_response(past)

        mock_get.side_effect = side_effect

        sync_zoom_meeting_for_event(self.zd.pk, EVENT_MEETING_ENDED, "")

        self.zd.refresh_from_db()
        self.assertEqual(self.zd.sync_state, ZoomMeetingData.SyncState.PARTIAL)
        self.assertEqual(
            self.zd.sync_error,
            "Participant report unavailable (network error).",
        )
        self.assertEqual(self.zd.meeting_status, ZoomMeetingData.MeetingStatus.ENDED)

    @patch("zoom_integration.services.requests.get")
    def test_layer2_recording_completed_updates_db(self, mock_get):
        rec = {
            "recording_files": [
                {
                    "id": "f1",
                    "file_type": "MP4",
                    "recording_type": "shared_screen_with_speaker_view",
                    "play_url": "https://zoom.us/rec/play/1",
                    "download_url": "https://zoom.us/rec/download/1",
                }
            ],
            "duration": 3600,
            "total_size": 100,
            "recording_count": 1,
        }
        mock_get.return_value = self._json_response(rec)

        sync_zoom_meeting_for_event(self.zd.pk, EVENT_RECORDING_COMPLETED, "")

        self.zd.refresh_from_db()
        self.assertEqual(self.zd.sync_state, ZoomMeetingData.SyncState.OK)
        self.assertEqual(self.zd.recording_status, ZoomMeetingData.RecordingStatus.AVAILABLE)
        self.assertEqual(len(self.zd.recording_urls_json), 1)

    @patch("zoom_integration.services.requests.get")
    def test_layer2_recording_completed_persists_metadata_and_payload_matches(self, mock_get):
        """A: Zoom GET /meetings/{id}/recordings → DB + meeting detail–style payload."""
        before = self.zd.last_sync_at
        rec = {
            "account_id": "acc_123",
            "host_id": "host_123",
            "duration": 3,
            "recording_count": 1,
            "total_size": 123456,
            "recording_files": [
                {
                    "id": "rec_file_1",
                    "file_type": "MP4",
                    "recording_type": "shared_screen_with_speaker_view",
                    "play_url": "https://zoom.example/play/1",
                    "download_url": "https://zoom.example/download/1",
                }
            ],
        }
        mock_get.return_value = self._json_response(rec)

        sync_zoom_meeting_for_event(self.zd.pk, EVENT_RECORDING_COMPLETED, "")

        self.zd.refresh_from_db()
        self.assertEqual(self.zd.sync_state, ZoomMeetingData.SyncState.OK)
        self.assertEqual(self.zd.sync_error, "")
        self.assertNotEqual(self.zd.sync_state, ZoomMeetingData.SyncState.ERROR)
        self.assertIsNotNone(self.zd.last_sync_at)
        if before is not None:
            self.assertGreaterEqual(self.zd.last_sync_at, before)

        self.assertEqual(self.zd.recording_status, ZoomMeetingData.RecordingStatus.AVAILABLE)
        self.assertEqual(len(self.zd.recording_urls_json), 1)
        meta = self.zd.recording_metadata_json
        self.assertEqual(meta.get("recording_count"), 1)
        self.assertEqual(meta.get("account_id"), "acc_123")
        self.assertEqual(meta.get("host_id"), "host_123")

        payload = build_zoom_post_meeting_payload(self.zd)
        self.assertEqual(payload["recording_status"], ZoomMeetingData.RecordingStatus.AVAILABLE)
        self.assertEqual(payload["recording_file_count"], 1)
        self.assertEqual(len(payload["recording_files"]), 1)
        self.assertEqual(payload["recording_files"][0]["play_url"], "https://zoom.example/play/1")

    @patch("zoom_integration.services.requests.get")
    def test_layer2_recording_empty_files_sets_none_not_error(self, mock_get):
        """A (failure branch): empty recording_files → NONE, sync OK; not a global sync failure."""
        self.zd.summary_status = ZoomMeetingData.SummaryStatus.AVAILABLE
        self.zd.summary_text = "existing"
        self.zd.save(update_fields=["summary_status", "summary_text"])

        mock_get.return_value = self._json_response(
            {
                "account_id": "acc",
                "recording_files": [],
                "recording_count": 0,
            }
        )

        sync_zoom_meeting_for_event(self.zd.pk, EVENT_RECORDING_COMPLETED, "")

        self.zd.refresh_from_db()
        self.assertEqual(self.zd.sync_state, ZoomMeetingData.SyncState.OK)
        self.assertEqual(self.zd.recording_status, ZoomMeetingData.RecordingStatus.NONE)
        self.assertEqual(self.zd.recording_urls_json, [])
        payload = build_zoom_post_meeting_payload(self.zd)
        self.assertEqual(payload["recording_status"], ZoomMeetingData.RecordingStatus.NONE)
        self.assertEqual(payload["recording_file_count"], 0)
        self.assertEqual(compute_user_feedback_code(self.zd), "unavailable")

    @patch("zoom_integration.services.requests.get")
    def test_layer3_summary_completed_sets_available_and_payload(self, mock_get):
        """B: GET /meetings/{id}/meeting_summary → summary text + AVAILABLE."""
        before = self.zd.last_sync_at
        body = {
            "summary": (
                "The meeting reviewed campaign pacing, identified delivery risks, "
                "and assigned follow-up tasks."
            )
        }
        mock_get.return_value = self._json_response(body)

        sync_zoom_meeting_for_event(self.zd.pk, EVENT_SUMMARY_COMPLETED, "")

        self.zd.refresh_from_db()
        self.assertEqual(self.zd.sync_state, ZoomMeetingData.SyncState.OK)
        self.assertEqual(self.zd.summary_status, ZoomMeetingData.SummaryStatus.AVAILABLE)
        self.assertEqual(
            self.zd.summary_text,
            body["summary"],
        )
        self.assertIsNotNone(self.zd.last_sync_at)
        if before is not None:
            self.assertGreaterEqual(self.zd.last_sync_at, before)

        payload = build_zoom_post_meeting_payload(self.zd)
        self.assertEqual(payload["summary_status"], ZoomMeetingData.SummaryStatus.AVAILABLE)
        self.assertEqual(payload["summary_text"], body["summary"])

    @patch("zoom_integration.services.requests.get")
    def test_task_recording_completed_invokes_sync_and_updates_row(self, mock_get):
        """A: Celery task drives the same sync path as direct service call."""
        rec = {
            "recording_files": [
                {
                    "id": "f1",
                    "file_type": "MP4",
                    "recording_type": "shared_screen_with_speaker_view",
                    "play_url": "https://zoom.example/play/1",
                    "download_url": "https://zoom.example/download/1",
                }
            ],
            "recording_count": 1,
        }
        mock_get.return_value = self._json_response(rec)

        process_zoom_webhook_event.run(
            event_type=EVENT_RECORDING_COMPLETED,
            zoom_meeting_data_id=self.zd.pk,
            webhook_uuid="",
        )

        self.zd.refresh_from_db()
        self.assertEqual(self.zd.recording_status, ZoomMeetingData.RecordingStatus.AVAILABLE)
        self.assertEqual(self.zd.sync_state, ZoomMeetingData.SyncState.OK)

    @patch("zoom_integration.services.requests.get")
    def test_task_summary_completed_invokes_sync_and_updates_row(self, mock_get):
        mock_get.return_value = self._json_response({"summary": "Done."})

        process_zoom_webhook_event.run(
            event_type=EVENT_SUMMARY_COMPLETED,
            zoom_meeting_data_id=self.zd.pk,
            webhook_uuid="",
        )

        self.zd.refresh_from_db()
        self.assertEqual(self.zd.summary_status, ZoomMeetingData.SummaryStatus.AVAILABLE)
        self.assertEqual(self.zd.summary_text, "Done.")
        self.assertEqual(self.zd.sync_state, ZoomMeetingData.SyncState.OK)

    @patch("zoom_integration.services.requests.get")
    def test_meeting_ended_then_recording_completed_updates_same_zoom_meeting_data_row(self, mock_get):
        """D: later events append to the same ZoomMeetingData (no second row)."""
        past = {
            "start_time": "2026-04-10T10:00:00Z",
            "end_time": "2026-04-10T11:00:00Z",
            "duration": 60,
        }
        participants_page = {"total_records": 2, "participants": []}
        rec = {
            "recording_files": [
                {
                    "id": "rec_file_1",
                    "file_type": "MP4",
                    "recording_type": "shared_screen_with_speaker_view",
                    "play_url": "https://zoom.example/play/1",
                    "download_url": "https://zoom.example/download/1",
                }
            ],
            "recording_count": 1,
            "account_id": "acc_123",
        }

        def side_effect(url, **_kwargs):
            if "/recordings" in url:
                return self._json_response(rec)
            if "/participants" in url:
                return self._json_response(participants_page)
            if "/past_meetings/" in url:
                return self._json_response(past)
            raise AssertionError(f"unexpected url: {url}")

        mock_get.side_effect = side_effect

        pk_before = self.zd.pk
        sync_zoom_meeting_for_event(self.zd.pk, EVENT_MEETING_ENDED, "")
        self.zd.refresh_from_db()
        self.assertEqual(self.zd.pk, pk_before)
        self.assertEqual(self.zd.meeting_status, ZoomMeetingData.MeetingStatus.ENDED)
        self.assertEqual(self.zd.duration_minutes, 60)
        self.assertEqual(self.zd.recording_status, ZoomMeetingData.RecordingStatus.UNKNOWN)
        t_after_end = self.zd.last_sync_at

        sync_zoom_meeting_for_event(self.zd.pk, EVENT_RECORDING_COMPLETED, "")
        self.zd.refresh_from_db()

        self.assertEqual(ZoomMeetingData.objects.count(), 1)
        self.assertEqual(self.zd.pk, pk_before)
        self.assertEqual(self.zd.duration_minutes, 60)
        self.assertEqual(self.zd.recording_status, ZoomMeetingData.RecordingStatus.AVAILABLE)
        self.assertEqual(len(self.zd.recording_urls_json), 1)
        self.assertIsNotNone(self.zd.last_sync_at)
        self.assertGreater(self.zd.last_sync_at, t_after_end)

    @patch("zoom_integration.tasks.sync_zoom_meeting_for_event", side_effect=ValueError("zoom api failed"))
    def test_task_marks_failed_on_sync_error(self, _mock_sync):
        process_zoom_webhook_event.run(
            event_type=EVENT_MEETING_ENDED,
            zoom_meeting_data_id=self.zd.pk,
            webhook_uuid="",
        )

        self.zd.refresh_from_db()
        self.assertEqual(self.zd.sync_state, ZoomMeetingData.SyncState.ERROR)
        self.assertIn("zoom api failed", self.zd.sync_error)
        self.assertIsNotNone(self.zd.last_sync_at)

    def test_unknown_event_type_no_db_change(self):
        process_zoom_webhook_event.run(
            event_type="user.created",
            zoom_meeting_data_id=self.zd.pk,
            webhook_uuid="",
        )
        self.zd.refresh_from_db()
        self.assertEqual(self.zd.sync_state, ZoomMeetingData.SyncState.NEVER)

    @patch("zoom_integration.services.requests.get")
    def test_layer3_summary_404_sets_not_applicable(self, mock_get):
        m = MagicMock()
        m.status_code = 404
        m.content = b""
        mock_get.return_value = m

        sync_zoom_meeting_for_event(self.zd.pk, EVENT_SUMMARY_COMPLETED, "")

        self.zd.refresh_from_db()
        self.assertEqual(self.zd.sync_state, ZoomMeetingData.SyncState.OK)
        self.assertNotEqual(self.zd.sync_state, ZoomMeetingData.SyncState.ERROR)
        self.assertEqual(self.zd.summary_status, ZoomMeetingData.SummaryStatus.NOT_APPLICABLE)
        self.assertEqual(self.zd.summary_text, "")
        self.assertEqual(compute_user_feedback_code(self.zd), "not_applicable")
        payload = build_zoom_post_meeting_payload(self.zd)
        self.assertEqual(payload["summary_text"], "")
        self.assertEqual(payload["user_feedback_code"], "not_applicable")

from zoom_integration.services import _parse_vtt_to_text

class PraseVttToTextTests(TestCase):
    def test_strips_timestamps_and_header(self):
        vtt = (
            "WEBVTT\n\n"
            "1\n"
            "00:00:01.000 --> 00:00:04.000\n"
            "Tim: We need to cut the budget.\n\n"
            "2\n"
            "00:00:05.000 --> 00:00:08.000\n"
            "Sarah: I agree.\n"
        )
        result = _parse_vtt_to_text(vtt)
        self.assertIn("Tim: We need to cut the budget.", result)
        self.assertIn("Sarah: I agree.", result)
        self.assertNotIn("WEBVTT", result)

    def test_empty_vtt_returns_empty_string(self):
        self.assertEqual(_parse_vtt_to_text(""), "")

# @patch("zoom_integration.services.request.get")
# def test_transcript_completed_stores_text_on_meeting(self, mock_get):
#     recordings_response = self._json_response({
#         reco
#     })