import unittest
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from django.contrib.auth import get_user_model

from authentication.login_security import LoginSecurityService
from authentication.models import AuthenticationLockout
from core.services.auth_tokens import build_user_refresh_token

User = get_user_model()


TEST_CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'auth-login-throttle-tests',
    }
}


@override_settings(CACHES=TEST_CACHES)
class LoginViewTests(APITestCase):

    def setUp(self):
        cache.clear()
        AuthenticationLockout.objects.all().delete()
        self.login_url = reverse('login')
        self.user = User.objects.create_user(
            email="loginuser@example.com",
            password="securepass",
            username="loginuser",
            is_verified=True,
            is_active=True
        )

    def test_successful_login(self):
        data = {
            "email": "loginuser@example.com",
            "password": "securepass"
        }
        response = self.client.post(self.login_url, data)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("token", response.data)
        self.assertIn("user", response.data)

    def test_invalid_credentials(self):
        data = {
            "email": "loginuser@example.com",
            "password": "wrongpass"
        }
        response = self.client.post(self.login_url, data)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn("error", response.data)

    def test_five_failed_attempts_triggers_backoff(self):
        data = {
            "email": "loginuser@example.com",
            "password": "wrongpass"
        }

        for _ in range(LoginSecurityService.BACKOFF_THRESHOLD - 1):
            response = self.client.post(self.login_url, data, REMOTE_ADDR='203.0.113.10')
            self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        response = self.client.post(self.login_url, data, REMOTE_ADDR='203.0.113.10')

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(response.data["errorCode"], LoginSecurityService.TOO_MANY_ATTEMPTS_CODE)
        self.assertTrue(response.data["requires_captcha"])
        self.assertGreaterEqual(response.data["retry_after_seconds"], 1)

    def test_sustained_failed_attempts_create_lockout(self):
        data = {
            "email": "loginuser@example.com",
            "password": "wrongpass"
        }

        response = None
        for _ in range(LoginSecurityService.LOCKOUT_THRESHOLD):
            response = self.client.post(self.login_url, data, REMOTE_ADDR='203.0.113.20')

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(response.data["errorCode"], LoginSecurityService.LOGIN_LOCKED_CODE)
        self.assertTrue(response.data["requires_captcha"])
        self.assertIn("lockout_until", response.data)
        self.assertTrue(AuthenticationLockout.objects.filter(
            scope=AuthenticationLockout.Scope.IP,
            identifier='203.0.113.20',
            resolved_at__isnull=True,
        ).exists())

    def test_sustained_failed_attempts_create_username_lockout_across_ips(self):
        data = {
            "email": "loginuser@example.com",
            "password": "wrongpass"
        }

        response = None
        for attempt in range(LoginSecurityService.LOCKOUT_THRESHOLD):
            response = self.client.post(
                self.login_url,
                data,
                REMOTE_ADDR=f'203.0.113.{attempt + 100}',
            )

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(response.data["errorCode"], LoginSecurityService.LOGIN_LOCKED_CODE)
        self.assertTrue(AuthenticationLockout.objects.filter(
            scope=AuthenticationLockout.Scope.USERNAME,
            identifier='loginuser@example.com',
            resolved_at__isnull=True,
        ).exists())

    def test_active_lockout_blocks_login_before_authentication(self):
        data = {
            "email": "loginuser@example.com",
            "password": "wrongpass"
        }

        for _ in range(LoginSecurityService.LOCKOUT_THRESHOLD):
            self.client.post(self.login_url, data, REMOTE_ADDR='203.0.113.30')

        valid_response = self.client.post(
            self.login_url,
            {"email": "loginuser@example.com", "password": "securepass"},
            REMOTE_ADDR='203.0.113.30',
        )

        self.assertEqual(valid_response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertEqual(valid_response.data["errorCode"], LoginSecurityService.LOGIN_LOCKED_CODE)

    def test_successful_login_clears_failed_attempt_counters(self):
        wrong_data = {
            "email": "loginuser@example.com",
            "password": "wrongpass"
        }
        valid_data = {
            "email": "loginuser@example.com",
            "password": "securepass"
        }

        for _ in range(LoginSecurityService.BACKOFF_THRESHOLD - 1):
            response = self.client.post(self.login_url, wrong_data, REMOTE_ADDR='203.0.113.40')
            self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        response = self.client.post(self.login_url, valid_data, REMOTE_ADDR='203.0.113.40')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        for _ in range(LoginSecurityService.BACKOFF_THRESHOLD - 1):
            response = self.client.post(self.login_url, wrong_data, REMOTE_ADDR='203.0.113.40')
            self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    @unittest.skip(
        "Email verification gate is disabled in authentication.views.LoginView "
        "while no SMTP/SES/Mailgun service is wired up. Restore this test when "
        "the verification gate (commented block around line 197) is re-enabled."
    )
    def test_unverified_user(self):
        user = User.objects.create_user(
            email="unverified@example.com",
            password="securepass",
            username="unverified",
            is_verified=False,
            is_active=True
        )
        data = {
            "email": "unverified@example.com",
            "password": "securepass"
        }
        response = self.client.post(self.login_url, data)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("error", response.data)

    def test_inactive_user(self):
        user = User.objects.create_user(
            email="inactive@example.com",
            password="securepass",
            username="inactive",
            is_verified=True,
            is_active=False
        )
        data = {
            "email": "inactive@example.com",
            "password": "securepass"
        }
        response = self.client.post(self.login_url, data)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn("error", response.data)

    def test_missing_email(self):
        data = {
            "password": "securepass"
        }
        response = self.client.post(self.login_url, data)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", response.data)

    def test_missing_password(self):
        data = {
            "email": "loginuser@example.com"
        }
        response = self.client.post(self.login_url, data)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", response.data)

    @patch('authentication.views.SessionRegistry.remove_session')
    @patch('channels.layers.get_channel_layer')
    def test_logout_emits_session_revoked_event(self, mock_get_channel_layer, mock_remove_session):
        calls = []

        class FakeChannelLayer:
            async def group_send(self, group, message):
                calls.append((group, message))

        mock_get_channel_layer.return_value = FakeChannelLayer()
        # Logout identifies the session by the refresh cookie, not the access token.
        refresh = build_user_refresh_token(self.user)
        self.client.cookies[settings.REFRESH_COOKIE_NAME] = str(refresh)

        response = self.client.post(reverse('logout'), {})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['message'], 'Logged out successfully.')
        mock_remove_session.assert_called_once_with(str(self.user.id), refresh['refresh_jti'])
        self.assertEqual(calls, [(
            f'chat_user_{self.user.id}',
            {
                'type': 'user_session_revoked',
                'reason': 'logout',
            },
        )])
        self.assertEqual(response.cookies[settings.REFRESH_COOKIE_NAME]['max-age'], 0)

    @patch('authentication.views.SessionRegistry.remove_session')
    @patch('channels.layers.get_channel_layer')
    def test_logout_without_refresh_cookie_still_succeeds(self, mock_get_channel_layer, mock_remove_session):
        # e.g. the cookie already expired: nothing to end server-side, but the
        # response must still be 200 and still clear the cookie.
        response = self.client.post(reverse('logout'), {})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_remove_session.assert_not_called()
        mock_get_channel_layer.assert_not_called()
        self.assertEqual(response.cookies[settings.REFRESH_COOKIE_NAME]['max-age'], 0)
