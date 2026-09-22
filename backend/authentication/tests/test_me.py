from django.urls import reverse
from django.db import connection
from rest_framework.test import APITestCase
from rest_framework import status
from django.contrib.auth import get_user_model
from core.models import Organization, Role
from core.services.tenant import slug_to_schema_name
from access_control.models import UserRole
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()

class MeViewTests(APITestCase):

    def setUp(self):
        self.me_url = reverse('me')

        # Create organization (triggers provision_tenant_schema which resets
        # search_path to public after provisioning the org schema).
        self.organization = Organization.objects.create(
            name="Test Organization",
            email_domain="test.com"
        )

        # Create user
        self.user = User.objects.create_user(
            email="testuser@test.com",
            password="securepass",
            username="testuser",
            is_verified=True,
            is_active=True,
            organization=self.organization
        )

        # Role and UserRole are tenant-scoped; access_control migrations are
        # stubs (no public DDL). Must create them in the org schema.
        _schema = slug_to_schema_name(self.organization.slug)
        try:
            with connection.cursor() as cursor:
                cursor.execute('SET search_path TO %s, public', [_schema])
            self.role = Role.objects.create(
                name="Media Buyer",
                organization=self.organization,
                level=30
            )
            UserRole.objects.create(user=self.user, role=self.role)
        finally:
            with connection.cursor() as cursor:
                cursor.execute('SET search_path TO public')

        # Generate token
        refresh = RefreshToken.for_user(self.user)
        self.access_token = str(refresh.access_token)

    def test_authenticated_user_me(self):
        """Test that authenticated user can access their profile data"""
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.access_token}')
        response = self.client.get(self.me_url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('id', response.data)
        self.assertIn('email', response.data)
        self.assertIn('username', response.data)
        self.assertIn('is_verified', response.data)
        self.assertIn('organization', response.data)
        self.assertIn('roles', response.data)
        
        # Check specific values
        self.assertEqual(response.data['email'], 'testuser@test.com')
        self.assertEqual(response.data['username'], 'testuser')
        self.assertTrue(response.data['is_verified'])
        self.assertEqual(response.data['organization']['id'], self.organization.id)
        self.assertEqual(response.data['organization']['name'], 'Test Organization')
        self.assertIn('Media Buyer', response.data['roles'])

    def test_unauthenticated_user_me(self):
        """Test that unauthenticated user cannot access profile data"""
        response = self.client.get(self.me_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_invalid_token_me(self):
        """Test that invalid token returns 401"""
        self.client.credentials(HTTP_AUTHORIZATION='Bearer invalid_token')
        response = self.client.get(self.me_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_user_without_organization(self):
        """Test user without organization"""
        user_no_org = User.objects.create_user(
            email="noorg@test.com",
            password="securepass",
            username="noorg",
            is_verified=True,
            is_active=True,
            organization=None
        )
        
        refresh = RefreshToken.for_user(user_no_org)
        access_token = str(refresh.access_token)
        
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')
        response = self.client.get(self.me_url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data['organization'])
        self.assertEqual(response.data['roles'], [])

    def test_user_with_multiple_roles(self):
        """Test user with multiple roles"""
        # Role and UserRole are tenant-scoped; create in org schema.
        _schema = slug_to_schema_name(self.organization.slug)
        try:
            with connection.cursor() as cursor:
                cursor.execute('SET search_path TO %s, public', [_schema])
            admin_role = Role.objects.create(
                name="Admin",
                organization=self.organization,
                level=10
            )
            UserRole.objects.create(user=self.user, role=admin_role)
        finally:
            with connection.cursor() as cursor:
                cursor.execute('SET search_path TO public')
        
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.access_token}')
        response = self.client.get(self.me_url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        roles = response.data['roles']
        self.assertIn('Media Buyer', roles)
        self.assertIn('Admin', roles)
        self.assertEqual(len(roles), 2) 
    def _authenticate(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {self.access_token}')

    def _make_customer_user(self, user_type):
        """Create a CustomerUser row for self.user with the given role."""
        from customer.models import CustomerOrganisation
        from csm.models import CustomerUser

        org = CustomerOrganisation.objects.create(name=f"Acme {user_type}")
        return CustomerUser.objects.create(
            user=self.user, organisation=org, user_type=user_type, is_active=True,
        )

    def test_me_reports_plain_user_as_neither_admin_nor_supervisor(self):
        self._authenticate()
        response = self.client.get(self.me_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data['is_csm_admin'])
        self.assertFalse(response.data['is_csm_supervisor'])

    def test_me_reports_supervisor_as_supervisor_but_not_csm_admin(self):
        """A supervisor must be distinguishable from a CSM admin.

        is_csm_admin gates the CSM settings area and is admin-only, so the
        quality inspection area needs its own flag.
        """
        self._make_customer_user('supervisor')
        self._authenticate()
        response = self.client.get(self.me_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['is_csm_supervisor'])
        self.assertFalse(response.data['is_csm_admin'])

    def test_me_reports_csm_admin_as_supervisor_too(self):
        """Admins supervise: the two roles are treated together everywhere else."""
        self._make_customer_user('admin')
        self._authenticate()
        response = self.client.get(self.me_url)

        self.assertTrue(response.data['is_csm_admin'])
        self.assertTrue(response.data['is_csm_supervisor'])

    def test_me_reports_inactive_supervisor_as_not_supervisor(self):
        customer_user = self._make_customer_user('supervisor')
        customer_user.is_active = False
        customer_user.save(update_fields=['is_active'])

        self._authenticate()
        response = self.client.get(self.me_url)

        self.assertFalse(response.data['is_csm_supervisor'])

    def test_me_reports_staff_as_supervisor(self):
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])

        self._authenticate()
        response = self.client.get(self.me_url)

        self.assertTrue(response.data['is_csm_supervisor'])
