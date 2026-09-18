"""temporary tenant switches must restore the caller's schema.

Landing on public after a nested switch is accidentally correct today for
public-schema tables, but it is a trap for later tenant-scoped work on the
same request.
"""
import uuid

import pytest
from django.db import connection
from psycopg2 import sql
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from core.models import Organization
from core.services.tenant import slug_to_schema_name
from core.tenant_context import current_tenant_schema, tenant_schema_context
from core.views import CreateOrganizationView, JoinOrganizationBySlugView

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _tenant_search_path_isolation():
    if connection.vendor != 'postgresql':
        pytest.skip('Tenant search_path switching is PostgreSQL-specific')
    yield
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute('SET search_path TO public')


def _set_search_path(schema_name: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            sql.SQL('SET search_path TO {}, public').format(
                sql.Identifier(schema_name)
            )
        )


def _unique_org_name(prefix: str) -> str:
    return f'{prefix} {uuid.uuid4().hex[:8]}'


def test_tenant_schema_context_restores_previous_schema(organization):
    original = slug_to_schema_name(organization.slug)
    _set_search_path(original)

    with tenant_schema_context('public'):
        assert current_tenant_schema() == 'public'

    assert current_tenant_schema() == original


def test_tenant_schema_context_restores_previous_schema_on_error(organization):
    original = slug_to_schema_name(organization.slug)
    _set_search_path(original)

    with pytest.raises(RuntimeError, match='boom'):
        with tenant_schema_context('public'):
            raise RuntimeError('boom')

    assert current_tenant_schema() == original


def test_creating_organization_restores_caller_schema(organization):
    original = slug_to_schema_name(organization.slug)
    _set_search_path(original)

    Organization.objects.create(name=_unique_org_name('MED-402 Provision'))

    assert current_tenant_schema() == original


def test_create_organization_view_restores_caller_schema(user, organization):
    original = slug_to_schema_name(organization.slug)
    _set_search_path(original)

    factory = APIRequestFactory()
    request = factory.post(
        '/api/core/organizations/create/',
        {'name': _unique_org_name('MED-402 Created')},
        format='json',
    )
    force_authenticate(request, user=user)
    response = CreateOrganizationView.as_view()(request)

    assert response.status_code == status.HTTP_201_CREATED
    assert current_tenant_schema() == original


def test_join_organization_view_restores_caller_schema(user, organization):
    other = Organization.objects.create(name=_unique_org_name('MED-402 Join Target'))
    original = slug_to_schema_name(organization.slug)
    _set_search_path(original)

    factory = APIRequestFactory()
    request = factory.post(
        '/api/core/organizations/join/',
        {'slug': other.slug},
        format='json',
    )
    force_authenticate(request, user=user)
    response = JoinOrganizationBySlugView.as_view()(request)

    assert response.status_code == status.HTTP_201_CREATED
    assert current_tenant_schema() == original
