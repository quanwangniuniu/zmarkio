"""
Account deletion must not abort when the avatar file cannot be removed, but
the leftover file must be logged (MED-401).
"""
import logging
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.db.models.fields.files import FieldFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

User = get_user_model()


@pytest.mark.django_db
def test_avatar_delete_failure_is_logged_and_account_still_deleted(caplog):
    user = User.objects.create_user(
        email="avatar-delete@example.test",
        username="avatar-delete",
        password="TestPassword123!",
    )
    user.avatar.name = "avatars/leftover.png"
    user.save(update_fields=["avatar"])
    client = APIClient()
    client.force_authenticate(user=user)

    with patch.object(FieldFile, "delete", side_effect=PermissionError("read-only volume")), caplog.at_level(
        logging.WARNING, logger="authentication.views"
    ):
        response = client.delete(reverse("me-delete"), {"confirm": "DELETE MY ACCOUNT"}, format="json")

    assert response.status_code == status.HTTP_200_OK
    user.refresh_from_db()
    assert user.is_deleted
    assert f"Failed to delete avatar file for deleted user {user.id} (name=avatars/leftover.png)" in caplog.text
