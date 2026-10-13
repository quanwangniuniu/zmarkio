from django.conf import settings
from django.urls import reverse
from django.test import override_settings
from django.db import connection
from rest_framework.test import APITestCase
from rest_framework import status
from django.contrib.auth import get_user_model
from core.models import Organization, Role
from core.services.tenant import slug_to_schema_name
from access_control.models import UserRole

User = get_user_model()

@override_settings(ROOT_URLCONF='backend.urls')
class SsoRedirectViewTests(APITestCase):
    """Test cases for SsoRedirectView"""

    def setUp(self):
        self.sso_redirect_url = reverse('sso-redirect')

    def test_sso_redirect_success(self):
        """Test successful SSO redirect returns mock redirect URL"""
        response = self.client.get(self.sso_redirect_url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("redirect_url", response.data)
        self.assertEqual(
            response.data["redirect_url"], 
            "https://mock-sso-provider.com/auth?state=mockstate"
        )

    def test_sso_redirect_method_not_allowed(self):
        """Test that POST method is not allowed"""
        response = self.client.post(self.sso_redirect_url)
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_sso_redirect_put_method_not_allowed(self):
        """Test that PUT method is not allowed"""
        response = self.client.put(self.sso_redirect_url)
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_sso_redirect_delete_method_not_allowed(self):
        """Test that DELETE method is not allowed"""
        response = self.client.delete(self.sso_redirect_url)
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)


@override_settings(ROOT_URLCONF='backend.urls')
class SsoCallbackViewTests(APITestCase):
    """Test cases for SsoCallbackView"""

    def setUp(self):
        self.sso_callback_url = reverse('sso-callback')

        # Create test organization (this also provisions the tenant schema)
        self.organization = Organization.objects.create(
            name="Test Agency",
            email_domain="testagency.com"
        )

        # Role is tenant-scoped; create it inside the org schema so the FK in
        # tenant.access_control_userrole is satisfied during constraint checks.
        _schema = slug_to_schema_name(self.organization.slug)
        with connection.cursor() as cursor:
            cursor.execute('SET search_path TO %s, public', [_schema])
        try:
            self.default_role = Role.objects.create(
                organization=self.organization,
                name="Media Buyer",
                level=30
            )
        finally:
            with connection.cursor() as cursor:
                cursor.execute('SET search_path TO public')

    def test_sso_callback_success_new_user(self):
        """Test successful SSO callback creates new user and assigns role"""
        email = "newuser@testagency.com"
        response = self.client.get(f"{self.sso_callback_url}?email={email}")
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("message", response.data)
        self.assertIn("token", response.data)
        self.assertIn(settings.REFRESH_COOKIE_NAME, response.cookies)
        self.assertIn("user", response.data)
        
        # Check user was created
        user = User.objects.get(email=email)
        # Username is now UUID-based, so just check it starts with "user_" and is 13 chars
        self.assertTrue(user.username.startswith("user_"))
        self.assertEqual(len(user.username), 13)  # "user_" + 8 chars = 13
        self.assertEqual(user.organization, self.organization)
        self.assertTrue(user.is_verified)
        self.assertTrue(user.is_active)
        
        # Check role was assigned — UserRole lives in org schema
        _schema = slug_to_schema_name(self.organization.slug)
        try:
            with connection.cursor() as _cur:
                _cur.execute('SET search_path TO %s, public', [_schema])
            user_role = UserRole.objects.get(user=user, role=self.default_role)
            self.assertIsNotNone(user_role)
        finally:
            with connection.cursor() as _cur:
                _cur.execute('SET search_path TO public')

    def test_sso_callback_success_existing_user(self):
        """Test successful SSO callback updates existing user"""
        email = "existinguser@testagency.com"
        
        # Create existing user with hash-based username
        import hashlib
        expected_username = f"user_{hashlib.md5(email.encode()).hexdigest()[:12]}"
        existing_user = User.objects.create_user(
            email=email,
            username=expected_username,  # Username is hash-based
            password="password123",
            is_verified=False,
            is_active=False,
            organization=None
        )
        
        response = self.client.get(f"{self.sso_callback_url}?email={email}")
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Check user was updated
        existing_user.refresh_from_db()
        self.assertEqual(existing_user.organization, self.organization)
        self.assertTrue(existing_user.is_verified)
        self.assertTrue(existing_user.is_active)
        
        # Check role was assigned — UserRole lives in org schema
        _schema = slug_to_schema_name(self.organization.slug)
        try:
            with connection.cursor() as _cur:
                _cur.execute('SET search_path TO %s, public', [_schema])
            user_role = UserRole.objects.get(user=existing_user, role=self.default_role)
            self.assertIsNotNone(user_role)
        finally:
            with connection.cursor() as _cur:
                _cur.execute('SET search_path TO public')

    def test_sso_callback_default_email(self):
        """Test SSO callback works with default email when no email provided"""
        # Create organization for the default email domain
        default_org = Organization.objects.create(
            name="Agency X",
            email_domain="agencyX.com"
        )
        Role.objects.create(
            organization=default_org,
            name="Media Buyer",
            level=30
        )
        
        response = self.client.get(self.sso_callback_url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("token", response.data)
        
        # Check default user was created
        default_user = User.objects.get(email="buyer@agencyX.com")
        # Username is now UUID-based, so just check it starts with "user_" and is 13 chars
        self.assertTrue(default_user.username.startswith("user_"))
        self.assertEqual(len(default_user.username), 13)  # "user_" + 8 chars = 13

    def test_sso_callback_case_insensitive_domain_matching(self):
        """Test that domain matching is case insensitive"""
        # Test with uppercase domain
        email = "user1@TESTAGENCY.COM"
        response = self.client.get(f"{self.sso_callback_url}?email={email}")
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Test with mixed case domain
        email = "user2@TestAgency.Com"
        response = self.client.get(f"{self.sso_callback_url}?email={email}")
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_sso_callback_no_organization_found(self):
        """Test SSO callback fails when no organization matches email domain"""
        email = "user@nonexistent.com"
        response = self.client.get(f"{self.sso_callback_url}?email={email}")
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", response.data)
        self.assertEqual(
            response.data["error"], 
            "No organization found for this email domain."
        )

    def test_sso_callback_invalid_email_format(self):
        """Test SSO callback handles invalid email format gracefully"""
        # Test with email without @ symbol
        response = self.client.get(f"{self.sso_callback_url}?email=invalidemail")
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", response.data)

    def test_sso_callback_empty_email(self):
        """Test SSO callback with empty email parameter"""
        response = self.client.get(f"{self.sso_callback_url}?email=")
        
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", response.data)

    def test_sso_callback_method_not_allowed(self):
        """Test that POST method is not allowed"""
        response = self.client.post(self.sso_callback_url)
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_sso_callback_creates_default_role_if_not_exists(self):
        """Test that default role is created if it doesn't exist"""
        # Role is tenant-scoped; delete it from the org schema
        _schema = slug_to_schema_name(self.organization.slug)
        with connection.cursor() as cursor:
            cursor.execute('SET search_path TO %s, public', [_schema])
        try:
            Role.objects.filter(organization=self.organization, name="Media Buyer").delete()
        finally:
            with connection.cursor() as cursor:
                cursor.execute('SET search_path TO public')

        email = "user@testagency.com"
        response = self.client.get(f"{self.sso_callback_url}?email={email}")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Check default role was created in the org schema
        with connection.cursor() as cursor:
            cursor.execute('SET search_path TO %s, public', [_schema])
        try:
            default_role = Role.objects.get(organization=self.organization, name="Media Buyer")
            self.assertEqual(default_role.level, 30)
        finally:
            with connection.cursor() as cursor:
                cursor.execute('SET search_path TO public')

    def test_sso_callback_user_role_not_duplicated(self):
        """Test that user role is not duplicated on multiple calls"""
        email = "duplicateuser@testagency.com"
        
        # First call
        response1 = self.client.get(f"{self.sso_callback_url}?email={email}")
        self.assertEqual(response1.status_code, status.HTTP_200_OK)
        
        # Second call
        response2 = self.client.get(f"{self.sso_callback_url}?email={email}")
        self.assertEqual(response2.status_code, status.HTTP_200_OK)
        
        # Check only one user role exists — UserRole lives in org schema
        user = User.objects.get(email=email)
        _schema = slug_to_schema_name(self.organization.slug)
        try:
            with connection.cursor() as _cur:
                _cur.execute('SET search_path TO %s, public', [_schema])
            user_roles = UserRole.objects.filter(user=user, role=self.default_role)
            self.assertEqual(user_roles.count(), 1)
        finally:
            with connection.cursor() as _cur:
                _cur.execute('SET search_path TO public')



    def test_sso_callback_long_email(self):
        """Test SSO callback with very long email address"""
        long_email = "a" * 50 + "@testagency.com"
        response = self.client.get(f"{self.sso_callback_url}?email={long_email}")
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Check user was created
        user = User.objects.get(email=long_email)
        self.assertIsNotNone(user)

    def test_sso_callback_response_structure(self):
        """Test that SSO callback response has correct structure"""
        email = "testuser@testagency.com"
        response = self.client.get(f"{self.sso_callback_url}?email={email}")
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Check response structure
        expected_keys = ["message", "token", "user"]
        for key in expected_keys:
            self.assertIn(key, response.data)
        
        # Check user data structure
        user_data = response.data["user"]
        expected_user_keys = ["id", "email", "username", "is_verified", "organization"]
        for key in expected_user_keys:
            self.assertIn(key, user_data)
        
        # Check organization data structure
        org_data = user_data["organization"]
        expected_org_keys = ["id", "name"]
        for key in expected_org_keys:
            self.assertIn(key, org_data)

    def test_sso_callback_token_validity(self):
        """Test that returned token is valid JWT format"""
        email = "tokenuser@testagency.com"
        response = self.client.get(f"{self.sso_callback_url}?email={email}")
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        
        # Check token format (basic JWT structure: header.payload.signature)
        token = response.data["token"]
        self.assertIsInstance(token, str)
        self.assertGreater(len(token), 50)  # JWT tokens are typically long
        
        # Refresh token comes only as an HttpOnly cookie, never in the body
        self.assertNotIn("refresh", response.data)
        refresh_cookie = response.cookies[settings.REFRESH_COOKIE_NAME]
        self.assertTrue(refresh_cookie["httponly"])
        self.assertGreater(len(refresh_cookie.value), 50)

    def test_sso_callback_username_uniqueness_resolved(self):
        """Test that using email as username resolves uniqueness conflicts"""
        # Create organization for the second domain
        org2 = Organization.objects.create(
            name="Different Agency",
            email_domain="differentagency.com"
        )
        Role.objects.create(
            organization=org2,
            name="Media Buyer",
            level=30
        )
        
        # Create users with similar email prefixes but different domains
        email1 = "john@testagency.com"
        email2 = "john@differentagency.com"
        
        # Both should work without conflicts
        response1 = self.client.get(f"{self.sso_callback_url}?email={email1}")
        response2 = self.client.get(f"{self.sso_callback_url}?email={email2}")
        
        self.assertEqual(response1.status_code, status.HTTP_200_OK)
        self.assertEqual(response2.status_code, status.HTTP_200_OK)
        
        # Check both users were created with unique usernames
        user1 = User.objects.get(email=email1)
        user2 = User.objects.get(email=email2)
        
        # Both usernames should be UUID-based and unique
        self.assertTrue(user1.username.startswith("user_"))
        self.assertTrue(user2.username.startswith("user_"))
        self.assertEqual(len(user1.username), 13)  # "user_" + 8 chars = 13
        self.assertEqual(len(user2.username), 13)  # "user_" + 8 chars = 13
        self.assertNotEqual(user1.username, user2.username) 