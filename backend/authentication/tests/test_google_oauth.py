"""
Google OAuth Unit Tests
Run with: python manage.py test authentication.tests.test_google_oauth
"""
import pytest
from django.test import TestCase, Client, override_settings
from django.contrib.auth import get_user_model
from unittest.mock import patch, Mock
import jwt
import time
from urllib.parse import parse_qs, urlencode, urlparse

from django.core import signing
from authentication import mfa
from authentication.views import GOOGLE_OAUTH_STATE_SALT, build_google_oauth_state
from core.admin_utils import assign_org_admin
from core.models import Organization

User = get_user_model()


@override_settings(
    GOOGLE_OAUTH_CLIENT_ID='test-client-id-12345',
    GOOGLE_OAUTH_CLIENT_SECRET='test-client-secret-67890',
    GOOGLE_OAUTH_REDIRECT_URI='http://localhost:8000/auth/google/callback/',
    FRONTEND_URL='http://localhost:3000'
)
class GoogleOAuthStartViewTest(TestCase):
    """Test Google OAuth start flow"""
    
    def setUp(self):
        self.client = Client()
    
    def test_oauth_start_success(self):
        """Test successful OAuth start flow"""
        response = self.client.get('/auth/google/start/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('authorization_url', data)
        self.assertIn('state', data)
        self.assertTrue(data['authorization_url'].startswith('https://accounts.google.com'))
        payload = signing.loads(data['state'], salt=GOOGLE_OAUTH_STATE_SALT, max_age=600)
        self.assertEqual(payload['provider'], 'google')
    
    def test_oauth_start_without_config(self):
        """Test error handling when configuration is missing"""
        with patch('django.conf.settings.GOOGLE_OAUTH_CLIENT_ID', ''):
            response = self.client.get('/auth/google/start/')
            self.assertEqual(response.status_code, 500)
            self.assertIn('Google OAuth is not configured', response.json()['error'])


@override_settings(
    GOOGLE_OAUTH_CLIENT_ID='test-client-id-12345',
    GOOGLE_OAUTH_CLIENT_SECRET='test-client-secret-67890',
    GOOGLE_OAUTH_REDIRECT_URI='http://localhost:8000/auth/google/callback/',
    FRONTEND_URL='http://localhost:3000'
)
class GoogleOAuthCallbackViewTest(TestCase):
    """Test Google OAuth callback handling"""

    def setUp(self):
        self.client = Client()

    def _callback_url(self, code='fake_code', state=None):
        params = {'code': code}
        if state is not None:
            params['state'] = state
        return f"/auth/google/callback/?{urlencode(params)}"

    def _signed_state(self):
        return build_google_oauth_state()

    def _set_session_state(self, state):
        session = self.client.session
        session['google_oauth_state'] = state
        session.save()
    
    def test_callback_without_code(self):
        """Test callback without authorization code"""
        response = self.client.get('/auth/google/callback/')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Missing authorization code', response.json()['error'])
    
    @patch('authentication.views.OAuth2Session')
    @patch('authentication.views.jwt.decode')
    @patch('authentication.views.jwt.get_unverified_header')
    @patch('authentication.views.requests.get')
    @patch('cryptography.x509.load_pem_x509_certificate')
    def test_callback_new_user_creation(self, mock_load_cert, mock_requests, mock_get_header, mock_jwt_decode, mock_oauth_session):
        """Test new user creation flow"""
        # Mock JWT header (for key ID lookup)
        mock_get_header.return_value = {'kid': 'key_id_123'}
        
        # Mock certificate loading
        mock_cert = Mock()
        mock_public_key = Mock()
        mock_cert.public_key.return_value = mock_public_key
        mock_load_cert.return_value = mock_cert
        
        # Mock user info returned by Google
        mock_jwt_decode.return_value = {
            'sub': 'google_user_id_123',
            'email': 'newuser@gmail.com',
            'email_verified': True,
            'name': 'New User'
        }
        
        # Mock token exchange
        mock_session_instance = Mock()
        mock_session_instance.fetch_token.return_value = {
            'id_token': 'fake_id_token',
            'access_token': 'fake_access_token'
        }
        mock_oauth_session.return_value = mock_session_instance
        
        # Mock certificate fetch
        mock_requests.return_value.json.return_value = {
            'key_id_123': '-----BEGIN CERTIFICATE-----\nfake_cert\n-----END CERTIFICATE-----'
        }
        
        response = self.client.get(self._callback_url(state=self._signed_state()))
        
        # Should redirect to set password page
        self.assertEqual(response.status_code, 302)
        self.assertIn('/set-password', response.url)
        
        # Verify user was created
        user = User.objects.get(email='newuser@gmail.com')
        self.assertEqual(user.google_id, 'google_user_id_123')
        self.assertTrue(user.google_registered)
        self.assertFalse(user.password_set)
        self.assertIsNotNone(user.verification_token)
    
    @patch('authentication.views.OAuth2Session')
    @patch('authentication.views.jwt.decode')
    @patch('authentication.views.jwt.get_unverified_header')
    @patch('authentication.views.requests.get')
    @patch('cryptography.x509.load_pem_x509_certificate')
    def test_callback_existing_user_login(self, mock_load_cert, mock_requests, mock_get_header, mock_jwt_decode, mock_oauth_session):
        """Test existing user login flow"""
        # Create existing user (with password already set)
        user = User.objects.create_user(
            username='existinguser',
            email='existing@gmail.com',
            password='Password123!',
            google_id='google_user_id_456',
            google_registered=True,
            password_set=True,
            is_verified=True
        )
        
        # Mock JWT header (for key ID lookup)
        mock_get_header.return_value = {'kid': 'key_id_456'}
        
        # Mock certificate loading
        mock_cert = Mock()
        mock_public_key = Mock()
        mock_cert.public_key.return_value = mock_public_key
        mock_load_cert.return_value = mock_cert
        
        # Mock user info returned by Google
        mock_jwt_decode.return_value = {
            'sub': 'google_user_id_456',
            'email': 'existing@gmail.com',
            'email_verified': True,
            'name': 'Existing User'
        }
        
        # Mock token exchange
        mock_session_instance = Mock()
        mock_session_instance.fetch_token.return_value = {
            'id_token': 'fake_id_token',
            'access_token': 'fake_access_token'
        }
        mock_oauth_session.return_value = mock_session_instance
        
        # Mock certificate fetch
        mock_requests.return_value.json.return_value = {
            'key_id_456': '-----BEGIN CERTIFICATE-----\nfake_cert\n-----END CERTIFICATE-----'
        }
        
        response = self.client.get(self._callback_url(state=self._signed_state()))
        
        # Should redirect to frontend callback page (with auth_data)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/auth/google/callback', response.url)
        self.assertIn('auth_data=', response.url)

    @patch('authentication.views.OAuth2Session')
    @patch('authentication.views.jwt.decode')
    @patch('authentication.views.jwt.get_unverified_header')
    @patch('authentication.views.requests.get')
    @patch('cryptography.x509.load_pem_x509_certificate')
    def test_callback_org_admin_gets_mfa_challenge_instead_of_tokens(self, mock_load_cert, mock_requests, mock_get_header, mock_jwt_decode, mock_oauth_session):
        """MED-326: Google sign-in must not let an org admin skip MFA."""
        organization = Organization.objects.create(name='Google MFA Org')
        user = User.objects.create_user(
            username='googleadmin',
            email='admin@gmail.com',
            password='Password123!',
            google_id='google_admin_id',
            google_registered=True,
            password_set=True,
            is_verified=True,
            organization=organization,
        )
        assign_org_admin(user, organization)

        mock_get_header.return_value = {'kid': 'key_id_admin'}
        mock_cert = Mock()
        mock_cert.public_key.return_value = Mock()
        mock_load_cert.return_value = mock_cert
        mock_jwt_decode.return_value = {
            'sub': 'google_admin_id',
            'email': 'admin@gmail.com',
            'email_verified': True,
            'name': 'Google Admin'
        }
        mock_session_instance = Mock()
        mock_session_instance.fetch_token.return_value = {
            'id_token': 'fake_id_token',
            'access_token': 'fake_access_token'
        }
        mock_oauth_session.return_value = mock_session_instance
        mock_requests.return_value.json.return_value = {
            'key_id_admin': '-----BEGIN CERTIFICATE-----\nfake_cert\n-----END CERTIFICATE-----'
        }

        response = self.client.get(self._callback_url(state=self._signed_state()))

        self.assertEqual(response.status_code, 302)
        # No token in the redirect URL; the mfa_token travels in an HttpOnly cookie.
        self.assertEqual(parse_qs(urlparse(response.url).query), {'mfa': ['required'], 'setup': ['1']})
        cookie = response.cookies[mfa.MFA_TOKEN_COOKIE]
        self.assertTrue(cookie['httponly'])
        self.assertEqual(cookie['path'], mfa.MFA_TOKEN_COOKIE_PATH)
        self.assertEqual(mfa.read_mfa_token(cookie.value), user)
        self.assertEqual(self.client.post('/auth/mfa/enroll/start/').status_code, 200)
    
    @patch('authentication.views.OAuth2Session')
    @patch('authentication.views.jwt.decode')
    @patch('authentication.views.jwt.get_unverified_header')
    @patch('authentication.views.requests.get')
    @patch('cryptography.x509.load_pem_x509_certificate')
    def test_callback_link_google_to_existing_password_account(self, mock_load_cert, mock_requests, mock_get_header, mock_jwt_decode, mock_oauth_session):
        """Test linking Google OAuth to existing email/password account"""
        # Create existing user with email/password (NO Google account yet)
        user = User.objects.create_user(
            username='existingpassworduser',
            email='password_user@gmail.com',
            password='Password123!',
            google_id=None,  # No Google account linked yet
            google_registered=False,
            password_set=True,
            is_verified=True
        )
        
        # Verify user has usable password
        self.assertTrue(user.has_usable_password())
        self.assertTrue(user.password_set)
        
        # Mock JWT header (for key ID lookup)
        mock_get_header.return_value = {'kid': 'key_id_789'}
        
        # Mock certificate loading
        mock_cert = Mock()
        mock_public_key = Mock()
        mock_cert.public_key.return_value = mock_public_key
        mock_load_cert.return_value = mock_cert
        
        # Mock user info returned by Google (same email as existing user)
        mock_jwt_decode.return_value = {
            'sub': 'google_user_id_new_789',
            'email': 'password_user@gmail.com',
            'email_verified': True,
            'name': 'Password User'
        }
        
        # Mock token exchange
        mock_session_instance = Mock()
        mock_session_instance.fetch_token.return_value = {
            'id_token': 'fake_id_token',
            'access_token': 'fake_access_token'
        }
        mock_oauth_session.return_value = mock_session_instance
        
        # Mock certificate fetch
        mock_requests.return_value.json.return_value = {
            'key_id_789': '-----BEGIN CERTIFICATE-----\nfake_cert\n-----END CERTIFICATE-----'
        }
        
        response = self.client.get(self._callback_url(state=self._signed_state()))
        
        # Should redirect to frontend callback page (successful login)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/auth/google/callback', response.url)
        self.assertIn('auth_data=', response.url)
        
        # Verify user was updated correctly
        user.refresh_from_db()
        self.assertEqual(user.google_id, 'google_user_id_new_789')
        self.assertTrue(user.google_registered)
        self.assertTrue(user.password_set)  # Should remain True
        self.assertTrue(user.has_usable_password())  # Should still have password
        
        # Verify user can still login with email/password
        from django.contrib.auth import authenticate
        auth_user = authenticate(username=user.email, password='Password123!')
        self.assertIsNotNone(auth_user)
        self.assertEqual(auth_user.id, user.id)
    
    @patch('authentication.views.OAuth2Session')
    @patch('authentication.views.jwt.decode')
    @patch('authentication.views.jwt.get_unverified_header')
    @patch('authentication.views.requests.get')
    @patch('cryptography.x509.load_pem_x509_certificate')
    def test_callback_unverified_email(self, mock_load_cert, mock_requests, mock_get_header, mock_jwt_decode, mock_oauth_session):
        """Test rejection logic for unverified email"""
        # Mock JWT header (for key ID lookup)
        mock_get_header.return_value = {'kid': 'key_id_789'}
        
        # Mock certificate loading
        mock_cert = Mock()
        mock_public_key = Mock()
        mock_cert.public_key.return_value = mock_public_key
        mock_load_cert.return_value = mock_cert
        
        mock_jwt_decode.return_value = {
            'sub': 'google_user_id_789',
            'email': 'unverified@gmail.com',
            'email_verified': False,  # Unverified
            'name': 'Unverified User'
        }
        
        mock_session_instance = Mock()
        mock_session_instance.fetch_token.return_value = {
            'id_token': 'fake_id_token'
        }
        mock_oauth_session.return_value = mock_session_instance
        
        # Mock certificate fetch
        mock_requests.return_value.json.return_value = {
            'key_id_789': '-----BEGIN CERTIFICATE-----\nfake_cert\n-----END CERTIFICATE-----'
        }
        
        response = self.client.get(self._callback_url(state=self._signed_state()))
        
        self.assertEqual(response.status_code, 400)
        self.assertIn('Email not verified', response.json()['error'])
    
    def test_callback_code_reuse(self):
        """Test authorization code reuse detection"""
        # First use of code (will fail because it's fake)
        # This mainly tests error handling logic
        with patch('authentication.views.OAuth2Session') as mock_oauth_session:
            mock_session_instance = Mock()
            mock_session_instance.fetch_token.side_effect = Exception('invalid_grant')
            mock_oauth_session.return_value = mock_session_instance
            
            response = self.client.get(self._callback_url(state=self._signed_state()))

            # Should return friendly error message
            self.assertEqual(response.status_code, 400)

    def test_callback_rejects_missing_state(self):
        """Test callback rejects code-only OAuth callbacks without state."""
        response = self.client.get('/auth/google/callback/?code=fake_code')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['errorCode'], 'OAUTH_STATE_INVALID')

    def test_callback_rejects_invalid_state(self):
        """Test callback rejects tampered OAuth state."""
        response = self.client.get(self._callback_url(state='invalid-state'))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['errorCode'], 'OAUTH_STATE_INVALID')

    @patch('authentication.views.OAuth2Session')
    def test_callback_accepts_legacy_session_state_once_for_deploy_grace(self, mock_oauth_session):
        """Test callback accepts and consumes one pre-deploy legacy session state."""
        session_state = 'legacy-session-state'
        self._set_session_state(session_state)

        mock_session_instance = Mock()
        mock_session_instance.fetch_token.side_effect = Exception('invalid_grant')
        mock_oauth_session.return_value = mock_session_instance

        response = self.client.get(self._callback_url(state=session_state))

        self.assertEqual(response.status_code, 400)
        self.assertIn('Authorization code expired or already used', response.json()['error'])
        mock_session_instance.fetch_token.assert_called_once()
        self.assertNotIn('google_oauth_state', self.client.session)

    @patch('authentication.views.OAuth2Session')
    def test_callback_accepts_signed_query_state_without_session_cookie(self, mock_oauth_session):
        """Test callback accepts signed query state when the session cookie is unavailable."""
        mock_session_instance = Mock()
        mock_session_instance.fetch_token.side_effect = Exception('invalid_grant')
        mock_oauth_session.return_value = mock_session_instance

        response = self.client.get(self._callback_url(state=self._signed_state()))

        self.assertEqual(response.status_code, 400)
        self.assertIn('Authorization code expired or already used', response.json()['error'])
        mock_session_instance.fetch_token.assert_called_once()


@override_settings(
    FRONTEND_URL='http://localhost:3000'
)
class GoogleSetPasswordViewTest(TestCase):
    """Test set password flow"""
    
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='googleuser',
            email='google@example.com',
            google_id='google_id_123',
            google_registered=True,
            password_set=False,
            is_verified=True
        )
        self.user.verification_token = 'test_token_123'
        self.user.set_unusable_password()
        self.user.save()
    
    def test_set_password_success(self):
        """Test successful password setting"""
        response = self.client.post('/auth/google/set-password/', {
            'token': 'test_token_123',
            'password': 'NewPassword123!'
        }, content_type='application/json')
        
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('token', data)
        self.assertIn('user', data)
        
        # Verify password was set
        self.user.refresh_from_db()
        self.assertTrue(self.user.password_set)
        self.assertTrue(self.user.check_password('NewPassword123!'))
        self.assertIsNone(self.user.verification_token)
    
    def test_set_password_invalid_token(self):
        """Test invalid token"""
        response = self.client.post('/auth/google/set-password/', {
            'token': 'invalid_token',
            'password': 'NewPassword123!'
        }, content_type='application/json')
        
        self.assertEqual(response.status_code, 400)
        self.assertIn('Invalid or expired token', response.json()['error'])
    
    def test_set_password_weak_password(self):
        """Test weak password rejection"""
        response = self.client.post('/auth/google/set-password/', {
            'token': 'test_token_123',
            'password': '123'  # Too short
        }, content_type='application/json')
        
        self.assertEqual(response.status_code, 400)
        self.assertIn('Password validation failed', response.json()['error'])
    
    def test_set_password_for_non_google_user(self):
        """Test that non-Google users cannot use this endpoint"""
        regular_user = User.objects.create_user(
            username='regularuser',
            email='regular@example.com',
            password='Password123!',
            google_registered=False
        )
        regular_user.verification_token = 'regular_token'
        regular_user.save()
        
        response = self.client.post('/auth/google/set-password/', {
            'token': 'regular_token',
            'password': 'NewPassword123!'
        }, content_type='application/json')
        
        self.assertEqual(response.status_code, 400)
        self.assertIn('only for Google OAuth users', response.json()['error'])


class UserModelTest(TestCase):
    """Test user model related logic"""
    
    def test_username_uniqueness(self):
        """Test username uniqueness constraint"""
        User.objects.create_user(
            username='john',
            email='john1@example.com',
            password='Password123!'
        )
        
        # When creating a second user, a different username should be auto-generated
        # This logic is implemented in views.py, here we test the database constraint
        with self.assertRaises(Exception):
            User.objects.create_user(
                username='john',  # Duplicate
                email='john2@example.com',
                password='Password123!'
            )
    
    def test_email_uniqueness(self):
        """Test email uniqueness constraint"""
        User.objects.create_user(
            username='user1',
            email='duplicate@example.com',
            password='Password123!'
        )
        
        with self.assertRaises(Exception):
            User.objects.create_user(
                username='user2',
                email='duplicate@example.com',  # Duplicate
                password='Password123!'
            )
