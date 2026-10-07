"""Fixtures for the public API: credentials bound to (organization, project)."""
import pytest
from rest_framework.test import APIClient

from core.models import Organization, Project
from csm.models import CustomerUser
from customer.models import CustomerOrganisation
from public_api.scopes import ALL_SCOPES
from public_api.services.credentials import create_api_key, create_oauth_client


@pytest.fixture
def csm_admin(user, customer_organisation):
    """`user` as a CSM admin of the workspace's customer organisation."""
    CustomerUser.objects.create(
        user=user, user_type='admin', organisation=customer_organisation, is_active=True,
    )
    return user


@pytest.fixture
def admin_client(api_client, csm_admin):
    api_client.force_authenticate(user=csm_admin)
    return api_client


@pytest.fixture
def make_key(organization, project):
    def _make(scopes=ALL_SCOPES, organization_id=None, project_id=None, **kwargs):
        return create_api_key(
            organization_id=organization_id or organization.id,
            project_id=project_id or project.id,
            name=kwargs.pop('name', 'Integration'),
            scopes=list(scopes),
            user=None,
            **kwargs,
        )
    return _make


@pytest.fixture
def api_key(make_key):
    """(ApiKey, raw key) with every scope."""
    return make_key()


@pytest.fixture
def key_client(api_key):
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=api_key[1])
    return client


@pytest.fixture
def oauth_client(organization, project):
    """(OAuthClient, client secret) with every scope."""
    return create_oauth_client(
        organization_id=organization.id, project_id=project.id,
        name='CRM sync', scopes=list(ALL_SCOPES), user=None,
    )


@pytest.fixture
def other_workspace(db):
    """A second organisation whose project has the SAME id space, with its own CSM org."""
    organization = Organization.objects.create(name='Other Workspace', email_domain='other.test')
    project = Project.objects.create(name='Other Project', organization=organization)
    customer_organisation = CustomerOrganisation.objects.create(name='Other Customers', organization=organization)
    return {'organization': organization, 'project': project, 'customer_organisation': customer_organisation}
