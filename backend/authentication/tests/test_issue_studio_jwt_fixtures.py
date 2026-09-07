import json

import pytest
from django.core.management import call_command
from django.db.models.signals import post_save
from io import StringIO

from core.models import CustomUser, Organization
from core.services.tenant import provision_tenant_schema


pytestmark = pytest.mark.django_db


def test_issue_studio_jwt_fixtures_round_trip():
    from stripe_meta.signals import create_default_subscription

    post_save.disconnect(create_default_subscription, sender=Organization)
    try:
        org = Organization.objects.create(
            name="JWT Parity Org",
            slug="jwt-parity-org",
            is_active=True,
        )
        provision_tenant_schema(org.slug)
    finally:
        post_save.connect(create_default_subscription, sender=Organization)

    out = StringIO()
    call_command("issue_studio_jwt_fixtures", org_slug=org.slug, stdout=out)
    payload = json.loads(out.getvalue().strip())

    assert payload["org_slug"] == org.slug
    assert payload["auth_token_version"] == 1
    assert payload["django_accepts_access"] is True
    assert payload["django_rejects_expired"] is True
    assert payload["django_rejects_pre_rotation"] is True
    assert payload["access_token"]
    assert payload["pre_rotation_access_token"] != payload["access_token"]

    user = CustomUser.objects.get(id=payload["user_id"])
    assert user.auth_token_version == 1
    assert user.email == "studio-jwt-parity@ci.test"
