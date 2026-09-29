from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from core.services.erasure import erase_user_data, is_erased_user

User = get_user_model()


class DeleteAccountTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="delete-account@example.test",
            username="delete-account",
            password="TestPassword123!",
            first_name="Private",
            job="Manager",
        )
        self.client.force_authenticate(user=self.user)

    @patch("core.tasks.perform_user_erasure.delay")
    @patch("authentication.session_registry.SessionRegistry.revoke_all_sessions")
    def test_request_disables_access_and_queues_worker(self, revoke, delay):
        response = self.client.delete(
            reverse("me-delete"), {"confirm": "DELETE MY ACCOUNT"}, format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        revoke.assert_called_once_with(self.user.pk)
        delay.assert_called_once_with(self.user.pk)
        fresh_user = User.objects.get(pk=self.user.pk)
        self.assertFalse(fresh_user.is_active)
        self.assertEqual(fresh_user.auth_token_version, 1)
        self.assertEqual(fresh_user.email, "delete-account@example.test")
        self.assertFalse(hasattr(fresh_user, "is_deleted"))

        self.assertTrue(erase_user_data(self.user.pk))
        erased = User.objects.get(pk=self.user.pk)
        self.assertTrue(is_erased_user(erased))
        self.assertFalse(erased.has_usable_password())
        self.assertEqual(erased.email, f"deleted_{erased.pk}@removed.invalid")
        self.assertEqual(erased.username, f"deleted_{erased.pk}")
        self.assertEqual(erased.first_name, "")
        self.assertEqual(erased.job, "")
        self.assertTrue(erase_user_data(self.user.pk))

    @patch("core.tasks.perform_user_erasure.delay")
    @patch("authentication.session_registry.SessionRegistry.revoke_all_sessions")
    def test_invalid_confirmation_does_not_change_user(self, revoke, delay):
        response = self.client.delete(reverse("me-delete"), {"confirm": "wrong"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(User.objects.get(pk=self.user.pk).is_active)
        revoke.assert_not_called()
        delay.assert_not_called()

    @patch("core.tasks.perform_user_erasure.delay", side_effect=RuntimeError("broker unavailable"))
    @patch("authentication.session_registry.SessionRegistry.revoke_all_sessions")
    def test_queue_failure_rolls_back_account_disable(self, revoke, delay):
        response = self.client.delete(
            reverse("me-delete"), {"confirm": "DELETE MY ACCOUNT"}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        fresh_user = User.objects.get(pk=self.user.pk)
        self.assertTrue(fresh_user.is_active)
        self.assertEqual(fresh_user.auth_token_version, 0)

    def test_worker_refuses_to_erase_active_account(self):
        self.assertFalse(erase_user_data(self.user.pk))
        self.assertEqual(User.objects.get(pk=self.user.pk).email, "delete-account@example.test")

    @patch("core.tasks.perform_user_erasure.delay")
    @patch("authentication.session_registry.SessionRegistry.revoke_all_sessions")
    def test_request_does_not_follow_stale_active_project(self, revoke, delay):
        self.user.active_project_id = 999999
        self.user.save(update_fields=["active_project"])

        response = self.client.delete(
            reverse("me-delete"), {"confirm": "DELETE MY ACCOUNT"}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertTrue(erase_user_data(self.user.pk))
        self.assertIsNone(User.objects.get(pk=self.user.pk).active_project_id)
