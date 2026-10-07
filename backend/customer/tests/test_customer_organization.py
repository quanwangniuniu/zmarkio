"""Customer.organization: the workspace a customer belongs to (MED-226)."""
import importlib

import pytest
from django.apps import apps as django_apps

from customer.models import Customer, CustomerOrganisation

pytestmark = pytest.mark.django_db

backfill = importlib.import_module('customer.migrations.0013_customer_organization').backfill_organization


def test_create_sets_organization_from_project(member_client, project, organization):
    response = member_client.post(
        f'/api/customers/?project={project.id}',
        {'email': 'new@example.com', 'full_name': 'New Customer'},
        format='json',
    )

    assert response.status_code == 201, response.data
    assert Customer.objects.get(pk=response.data['id']).organization_id == organization.id


def test_backfill_copies_organization_from_customer_organisation(project, customer_organisation, organization):
    linked = Customer.objects.create(
        email='linked@example.com', full_name='Linked', project=project, organisation=customer_organisation,
    )
    unlinked = Customer.objects.create(email='unlinked@example.com', full_name='Unlinked', project=project)
    orphan_org = CustomerOrganisation.objects.create(name='No workspace')
    orphan = Customer.objects.create(
        email='orphan@example.com', full_name='Orphan', project=project, organisation=orphan_org,
    )

    backfill(django_apps, None)

    linked.refresh_from_db()
    unlinked.refresh_from_db()
    orphan.refresh_from_db()
    assert linked.organization_id == organization.id
    # No customer organisation (or one with no workspace): left NULL rather than guessed from project_id.
    assert unlinked.organization_id is None
    assert orphan.organization_id is None
