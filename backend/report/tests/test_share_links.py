"""Share-link create, revoke, and public-read rules for Custom KPIs."""

import os
from datetime import timedelta

import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")

import django
from django.conf import settings

if not settings.configured:
    django.setup()

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Organization, Project, ProjectMember
from report.models import CustomKPI, ReportShareLink

CREATE_URL = reverse("report:report-share-link")


def public_url(token):
    return reverse("report:public-report-share-link", kwargs={"token": token})


@pytest.fixture
def share_client(kpi_user):
    organization = Organization.objects.create(name="Share Org")
    project = Project.objects.create(
        name="Share Project", organization=organization, owner=kpi_user
    )
    other = Project.objects.create(
        name="Other Share Project", organization=organization, owner=kpi_user
    )
    ProjectMember.objects.create(user=kpi_user, project=project, is_active=True)
    ProjectMember.objects.create(user=kpi_user, project=other, is_active=True)
    api_client = APIClient()
    api_client.force_authenticate(user=kpi_user)
    return {"client": api_client, "user": kpi_user, "project": project, "other": other}


def _create(client, project, days=7):
    return client.post(
        CREATE_URL, {"project": project.slug, "days": days}, format="json"
    )


@pytest.mark.django_db
def test_get_current_link_is_empty_and_does_not_create(share_client):
    response = share_client["client"].get(
        f"{CREATE_URL}?project={share_client['project'].slug}"
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data == {"link": None}
    assert ReportShareLink.objects.filter(project=share_client["project"]).count() == 0


@pytest.mark.django_db
def test_get_current_link_returns_the_unrevoked_link(share_client):
    created = _create(share_client["client"], share_client["project"])
    response = share_client["client"].get(
        f"{CREATE_URL}?project={share_client['project'].slug}"
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data["link"]["token"] == created.data["token"]
    assert response.data["link"]["days_left"] >= 6


@pytest.mark.django_db
def test_get_current_link_keeps_an_expired_unrevoked_row(share_client):
    created = _create(share_client["client"], share_client["project"])
    link = ReportShareLink.objects.get(token=created.data["token"])
    link.expires_at = timezone.now() - timedelta(hours=1)
    link.save(update_fields=["expires_at", "updated_at"])

    response = share_client["client"].get(
        f"{CREATE_URL}?project={share_client['project'].slug}"
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data["link"]["token"] == link.token
    assert response.data["link"]["days_left"] == 0
    link.refresh_from_db()
    assert link.revoked_at is None


@pytest.mark.django_db
def test_create_issues_a_new_link(share_client):
    response = _create(share_client["client"], share_client["project"])

    assert response.status_code == status.HTTP_201_CREATED
    assert response.data["reused"] is False
    assert response.data["project"] == share_client["project"].slug
    link = ReportShareLink.objects.get(token=response.data["token"])
    assert link.revoked_at is None
    assert link.expires_at > timezone.now()


@pytest.mark.django_db
def test_create_reuses_an_unexpired_link(share_client):
    first = _create(share_client["client"], share_client["project"])
    second = _create(share_client["client"], share_client["project"], days=30)

    assert second.status_code == status.HTTP_200_OK
    assert second.data["reused"] is True
    assert second.data["token"] == first.data["token"]
    assert ReportShareLink.objects.filter(project=share_client["project"]).count() == 1


@pytest.mark.django_db
def test_create_releases_an_expired_link_then_issues_a_new_one(share_client):
    first = _create(share_client["client"], share_client["project"])
    old = ReportShareLink.objects.get(token=first.data["token"])
    old.expires_at = timezone.now() - timedelta(days=1)
    old.save(update_fields=["expires_at", "updated_at"])

    second = _create(share_client["client"], share_client["project"])

    assert second.status_code == status.HTTP_201_CREATED
    assert second.data["token"] != old.token
    old.refresh_from_db()
    assert old.revoked_at is not None
    assert old.revoked_at > old.expires_at
    assert (
        ReportShareLink.objects.filter(
            project=share_client["project"], revoked_at__isnull=True
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_public_read_reports_410_after_an_expired_link_is_released(share_client):
    first = _create(share_client["client"], share_client["project"])
    old = ReportShareLink.objects.get(token=first.data["token"])
    old.expires_at = timezone.now() - timedelta(hours=1)
    old.save(update_fields=["expires_at", "updated_at"])
    _create(share_client["client"], share_client["project"])

    response = APIClient().get(public_url(old.token))

    assert response.status_code == status.HTTP_410_GONE
    assert response.data["code"] == "LINK_EXPIRED"


@pytest.mark.django_db
def test_public_read_reports_404_when_revoked_before_expiry(share_client):
    created = _create(share_client["client"], share_client["project"])
    deleted = share_client["client"].delete(
        f"{CREATE_URL}?project={share_client['project'].slug}"
    )
    assert deleted.status_code == status.HTTP_204_NO_CONTENT

    response = APIClient().get(public_url(created.data["token"]))

    assert response.status_code == status.HTTP_404_NOT_FOUND
    link = ReportShareLink.objects.get(token=created.data["token"])
    assert link.revoked_at < link.expires_at


@pytest.mark.django_db
def test_public_read_returns_only_the_linked_projects_kpis(share_client):
    created = _create(share_client["client"], share_client["project"])
    CustomKPI.objects.create(
        project=share_client["project"],
        name="Blended ROAS",
        formula="revenue / spend",
        created_by=share_client["user"],
    )
    CustomKPI.objects.create(
        project=share_client["other"],
        name="Other ROAS",
        formula="revenue / spend",
        created_by=share_client["user"],
    )

    response = APIClient().get(public_url(created.data["token"]))

    assert response.status_code == status.HTTP_200_OK
    assert [kpi["name"] for kpi in response.data["kpis"]] == ["Blended ROAS"]
    assert set(response.data["kpis"][0]) == {
        "name",
        "formula",
        "display_format",
        "value",
        "error",
    }


@pytest.mark.django_db
def test_missing_token_is_404():
    response = APIClient().get(public_url("missing-token"))
    assert response.status_code == status.HTTP_404_NOT_FOUND
