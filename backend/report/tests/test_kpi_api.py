"""API tests for custom KPIs: per-project persistence, access scoping, and the
inline-error contract the formula builder depends on."""

import os

import pytest

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backend.settings")

import django
from django.conf import settings

if not settings.configured:
    django.setup()

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.models import Organization, Project
from report.models import CustomKPI

User = get_user_model()

LIST_URL = reverse("report:custom-kpi-list-create")
PREVIEW_URL = reverse("report:custom-kpi-preview")
CATALOG_URL = reverse("report:kpi-metric-catalog")


def detail_url(kpi_id):
    return reverse("report:custom-kpi-detail", kwargs={"id": kpi_id})


@pytest.fixture
def client(kpi_warehouse):
    api_client = APIClient()
    api_client.force_authenticate(user=kpi_warehouse["user"])
    return api_client


@pytest.fixture
def outsider_client(db):
    """Authenticated, but a member of no project in these tests."""
    outsider = User.objects.create_user(
        username="outsider", email="outsider@example.com", password="testpass123"
    )
    api_client = APIClient()
    api_client.force_authenticate(user=outsider)
    return api_client


def _create_kpi(client, project, name="Blended ROAS", formula="revenue / spend"):
    return client.post(
        LIST_URL,
        {"project": project.slug, "name": name, "formula": formula},
        format="json",
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_create_kpi_persists_against_the_project(client, kpi_warehouse):
    response = _create_kpi(client, kpi_warehouse["project"])
    assert response.status_code == status.HTTP_201_CREATED

    kpi = CustomKPI.objects.get(id=response.data["id"])
    assert kpi.project_id == kpi_warehouse["project"].id
    assert kpi.formula == "revenue / spend"
    assert kpi.created_by_id == kpi_warehouse["user"].id


@pytest.mark.django_db
def test_list_returns_values_computed_from_the_warehouse(client, kpi_warehouse):
    _create_kpi(client, kpi_warehouse["project"])

    response = client.get(LIST_URL, {"project": kpi_warehouse["project"].slug})
    assert response.status_code == status.HTTP_200_OK

    results = response.data["results"] if "results" in response.data else response.data
    assert len(results) == 1
    # 1200 revenue / 300 spend over the default window.
    assert results[0]["value"] == "4"
    assert results[0]["error"] is None


@pytest.mark.django_db
def test_kpi_name_is_unique_within_a_project(client, kpi_warehouse):
    _create_kpi(client, kpi_warehouse["project"])
    duplicate = _create_kpi(client, kpi_warehouse["project"])
    assert duplicate.status_code == status.HTTP_400_BAD_REQUEST
    assert "name" in duplicate.data


@pytest.mark.django_db
def test_same_kpi_name_is_allowed_in_another_project(client, kpi_warehouse):
    _create_kpi(client, kpi_warehouse["project"])
    other = _create_kpi(client, kpi_warehouse["other_project"])
    assert other.status_code == status.HTTP_201_CREATED


@pytest.mark.django_db
def test_update_revalidates_the_formula(client, kpi_warehouse):
    kpi_id = _create_kpi(client, kpi_warehouse["project"]).data["id"]

    ok = client.patch(detail_url(kpi_id), {"formula": "clicks / impressions"}, format="json")
    assert ok.status_code == status.HTTP_200_OK
    assert CustomKPI.objects.get(id=kpi_id).formula == "clicks / impressions"

    bad = client.patch(detail_url(kpi_id), {"formula": "revenue / mystery"}, format="json")
    assert bad.status_code == status.HTTP_400_BAD_REQUEST
    assert CustomKPI.objects.get(id=kpi_id).formula == "clicks / impressions"


@pytest.mark.django_db
def test_delete_removes_the_kpi(client, kpi_warehouse):
    kpi_id = _create_kpi(client, kpi_warehouse["project"]).data["id"]
    response = client.delete(detail_url(kpi_id))
    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert not CustomKPI.objects.filter(id=kpi_id).exists()


# ---------------------------------------------------------------------------
# Formula errors surface inline, not as failures
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_create_rejects_an_invalid_formula_with_a_field_error(client, kpi_warehouse):
    response = _create_kpi(
        client, kpi_warehouse["project"], formula="revenue / mystery"
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "formula" in response.data
    assert "mystery" in str(response.data["formula"])
    assert not CustomKPI.objects.exists()


@pytest.mark.django_db
def test_preview_returns_a_value_without_saving(client, kpi_warehouse):
    response = client.post(
        PREVIEW_URL,
        {"project": kpi_warehouse["project"].slug, "formula": "revenue / spend"},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.data["value"] == "4"
    assert response.data["error"] is None
    assert not CustomKPI.objects.exists()


@pytest.mark.django_db
def test_preview_reports_a_bad_formula_in_the_body_not_as_a_400(client, kpi_warehouse):
    """The builder calls this while the user types; a half-typed formula is
    not a failed request."""
    response = client.post(
        PREVIEW_URL,
        {"project": kpi_warehouse["project"].slug, "formula": "revenue / "},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.data["value"] is None
    assert response.data["error"]["message"]


@pytest.mark.django_db
def test_preview_reports_division_by_zero(client, kpi_warehouse):
    response = client.post(
        PREVIEW_URL,
        {"project": kpi_warehouse["project"].slug, "formula": "revenue / leads"},
        format="json",
    )
    assert response.status_code == status.HTTP_200_OK
    assert response.data["error"]["code"] == "#DIV/0!"


@pytest.mark.django_db
def test_metric_catalog_lists_referenceable_names(client):
    response = client.get(CATALOG_URL)
    assert response.status_code == status.HTTP_200_OK
    keys = {metric["key"] for metric in response.data["metrics"]}
    assert {"spend", "revenue", "clicks", "impressions"} <= keys


# ---------------------------------------------------------------------------
# Access scoping
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_outsider_cannot_list_another_projects_kpis(
    client, outsider_client, kpi_warehouse
):
    _create_kpi(client, kpi_warehouse["project"])

    response = outsider_client.get(LIST_URL, {"project": kpi_warehouse["project"].slug})
    assert response.status_code == status.HTTP_200_OK
    results = response.data["results"] if "results" in response.data else response.data
    assert results == []


@pytest.mark.django_db
def test_outsider_cannot_create_a_kpi_in_a_project(outsider_client, kpi_warehouse):
    response = _create_kpi(outsider_client, kpi_warehouse["project"])
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert not CustomKPI.objects.exists()


@pytest.mark.django_db
def test_outsider_cannot_read_or_delete_a_kpi(client, outsider_client, kpi_warehouse):
    kpi_id = _create_kpi(client, kpi_warehouse["project"]).data["id"]

    assert outsider_client.get(detail_url(kpi_id)).status_code == status.HTTP_404_NOT_FOUND
    assert outsider_client.delete(detail_url(kpi_id)).status_code == status.HTTP_404_NOT_FOUND
    assert CustomKPI.objects.filter(id=kpi_id).exists()


@pytest.mark.django_db
def test_outsider_cannot_preview_against_a_project(outsider_client, kpi_warehouse):
    response = outsider_client.post(
        PREVIEW_URL,
        {"project": kpi_warehouse["project"].slug, "formula": "revenue / spend"},
        format="json",
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_anonymous_requests_are_rejected(kpi_warehouse):
    anonymous = APIClient()
    assert anonymous.get(LIST_URL).status_code in (
        status.HTTP_401_UNAUTHORIZED,
        status.HTTP_403_FORBIDDEN,
    )
