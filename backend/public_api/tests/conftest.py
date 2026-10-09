"""Fixtures for the public API: credentials bound to (organization, project)."""
import pytest
from rest_framework.test import APIClient

from core.models import Organization, Project
from csm.models import CustomerUser
from customer.models import CustomerOrganisation
from public_api.permissions import ALL_SCOPES
from public_api.services import create_api_key, create_oauth_client


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
    def _make(scopes=ALL_SCOPES, organization_id=None, project_id=None):
        return create_api_key(
            organization_id=organization_id or organization.id,
            project_id=project_id or project.id,
            name='Integration',
            scopes=list(scopes),
            user=None,
        )
    return _make


@pytest.fixture
def api_key(make_key):
    """(ApiKey, raw key) with every scope."""
    return make_key()


@pytest.fixture
def key_client(api_client, api_key):
    api_client.credentials(HTTP_X_API_KEY=api_key[1])
    return api_client


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


def client_for(raw_key):
    """A second, independent client, for tests that need two credentials at once."""
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=raw_key)
    return client


@pytest.fixture
def tenant_project(organization, project):
    """
    Mirror `project` into its organisation's schema. A credential's request runs
    there (as a logged-in user's would), and services such as routing rules
    resolve the project's organisation from that schema.
    """
    from core.services.tenant import slug_to_schema_name
    from core.tenant_context import tenant_schema_context

    with tenant_schema_context(slug_to_schema_name(organization.slug)):
        if not Project.objects.filter(pk=project.pk).exists():
            Project.objects.bulk_create([Project(
                pk=project.pk, name=project.name, slug=project.slug,
                organization_id=organization.id, owner_id=project.owner_id,
            )])
    return project


@pytest.fixture
def workspace(project, organization, customer_organisation, csm_queue, experience_group, user):
    """One row of every public resource in the credential's workspace."""
    from csm.models import Conversation, CustomerUser, QuickReplyTemplate, TemplateTag, Ticket
    from customer.models import Customer

    customer = Customer.objects.create(
        email='alice@example.com', full_name='Alice', project=project,
        organisation=customer_organisation, organization=organization, experience_group=experience_group,
    )
    agent = CustomerUser.objects.create(
        user=user, user_type='agent', organisation=customer_organisation, queue=csm_queue, is_active=True,
    )
    conversation = Conversation.objects.create(customer=customer, queue=csm_queue, assigned_to=agent)
    ticket = Ticket.objects.create(queue=csm_queue, title='Refund', customer_email=customer.email, conversation=conversation)
    TemplateTag.objects.create(organisation=customer_organisation, name='billing')
    template = QuickReplyTemplate.objects.create(
        organisation=customer_organisation, title='Refund ETA', content='5 days', tags=['billing'],
    )
    return {
        'customer': customer, 'agent': agent, 'conversation': conversation, 'ticket': ticket,
        'template': template, 'queue': csm_queue, 'customer_organisation': customer_organisation,
    }


def resolving_to(address):
    """
    Make the webhook URL guard resolve every host to `address`. Only chat.services'
    reference to `socket` is replaced: patching socket.getaddrinfo itself would
    also redirect the test's own Redis and database connections.
    """
    import socket
    from unittest.mock import MagicMock, patch

    fake = MagicMock(gaierror=socket.gaierror)
    fake.getaddrinfo.return_value = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 0))]
    return patch('chat.services.socket', fake)
