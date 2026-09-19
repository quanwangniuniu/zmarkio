from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import Project

User = get_user_model()


class DeleteAccountTests(APITestCase):
    def test_delete_account_with_missing_active_project(self):
        user = User.objects.create_user(
            email='delete-account@example.test',
            username='delete-account',
            password='TestPassword123!',
        )
        # Raw-SQL project deletion can leave this cross-schema reference stale.
        missing_project_id = 999999
        self.assertFalse(Project.objects.filter(pk=missing_project_id).exists())
        user.active_project_id = missing_project_id
        user.save(update_fields=['active_project'])
        self.client.force_authenticate(user=user)

        response = self.client.delete(
            reverse('me-delete'), {'confirm': 'DELETE MY ACCOUNT'}, format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertTrue(user.is_deleted)
        self.assertFalse(user.has_usable_password())
        self.assertEqual(user.email, f'deleted_{user.id}@removed.invalid')
        self.assertEqual(user.username, f'deleted_{user.id}')
