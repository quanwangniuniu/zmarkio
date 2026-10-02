"""TenantAwareStreamingResponse iterates in the schema it was created in.

TenantSchemaMiddleware resets search_path to public before a streaming body is
iterated; these tests stand in for that reset with tenant_schema_context.
"""
import pytest
from django.core.signals import request_finished
from django.db import close_old_connections, connection

from core.services.tenant import slug_to_schema_name
from core.tenant_context import current_tenant_schema, tenant_schema_context
from core.tenant_streaming import TenantAwareStreamingResponse

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _tenant_search_path_isolation():
    if connection.vendor != 'postgresql':
        pytest.skip('Tenant search_path switching is PostgreSQL-specific')
    yield
    with connection.cursor() as cursor:
        cursor.execute('SET search_path TO public')


def _schema_per_chunk():
    for _ in range(2):
        yield current_tenant_schema()


def test_stream_runs_in_the_schema_active_when_the_response_was_created(organization):
    tenant = slug_to_schema_name(organization.slug)
    with tenant_schema_context(tenant):
        response = TenantAwareStreamingResponse(_schema_per_chunk())
    # The middleware has reset search_path by the time the body is iterated.
    assert current_tenant_schema() == 'public'

    chunks = [chunk.decode() for chunk in response.streaming_content]

    assert chunks == [tenant, tenant]


def test_stream_restores_the_previous_schema_when_done(organization):
    tenant = slug_to_schema_name(organization.slug)
    with tenant_schema_context(tenant):
        response = TenantAwareStreamingResponse(_schema_per_chunk())

    list(response.streaming_content)

    assert current_tenant_schema() == 'public'


def test_stream_restores_the_previous_schema_when_the_content_raises(organization):
    tenant = slug_to_schema_name(organization.slug)

    def failing():
        yield 'first'
        raise RuntimeError('boom')

    with tenant_schema_context(tenant):
        response = TenantAwareStreamingResponse(failing())

    with pytest.raises(RuntimeError, match='boom'):
        list(response.streaming_content)

    assert current_tenant_schema() == 'public'


def test_stream_restores_the_previous_schema_when_closed_early(organization):
    tenant = slug_to_schema_name(organization.slug)
    with tenant_schema_context(tenant):
        response = TenantAwareStreamingResponse(_schema_per_chunk())

    iterator = iter(response.streaming_content)
    next(iterator)
    assert current_tenant_schema() == tenant
    # A client that disconnects mid-download: the server closes the response.
    # close() also sends request_finished, whose close_old_connections would
    # close the test's connection; Django's test client detaches it the same way.
    request_finished.disconnect(close_old_connections)
    try:
        response.close()
    finally:
        request_finished.connect(close_old_connections)

    assert current_tenant_schema() == 'public'
