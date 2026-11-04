import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import Mock

import pytest
from celery.exceptions import Retry
from django.core.cache.backends.locmem import LocMemCache
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework.throttling import SimpleRateThrottle

from chat.services import UnsafeUrlError
from core.crypto import encrypt_token
from core.models import Organization, OrganizationMembership
from csm.models import CustomerUser, Queue, RoutingRule, Ticket
from csm.tasks import notify_sla_breaches
from customer.models import Customer, CustomerOrganisation
from experience_group.models import ExperienceGroup
from external_api.enums import CredentialKind, WebhookEvent
from external_api.models import Credential, WebhookEndpoint
from external_api.tasks import deliver_webhook
from external_api.views import (
    MAX_CREDENTIALS_PER_PROJECT, MAX_WEBHOOKS_PER_PROJECT, AgentResource, ClientTokenView, CredentialView,
    CustomerResource, IntegrationView, RoutingRuleResource, TicketResource, WebhookView, hash_secret,
)

factory = APIRequestFactory()


@pytest.fixture(autouse=True)
def _stay_on_public_schema(monkeypatch):
    # Fixtures write tenant tables such as Project into public. A real request
    # switches into the organisation schema, which would hide those rows.
    monkeypatch.setattr('external_api.views.slug_to_schema_name', lambda slug: 'public')


@pytest.fixture(autouse=True)
def _fresh_rate_limits(monkeypatch):
    cache = LocMemCache('external-api-tests', {})
    cache.clear()
    monkeypatch.setattr(SimpleRateThrottle, 'cache', cache)


@pytest.fixture
def queue(project, customer_organisation):
    return Queue.objects.create(project=project, organisation=customer_organisation, name='Frontline', tier='T1')


def _credential(organization, project, *scopes, kind=CredentialKind.API_KEY):
    Credential.objects.create(
        organization=organization, project_id=project.id, name='Integration', kind=kind,
        public_id='abcd1234', secret_hash=hash_secret('super-secret'), scopes=list(scopes),
    )
    return 'zmk_abcd1234_super-secret'


def _call(view, method, data=None, key=None, **headers):
    if key:
        headers['HTTP_X_API_KEY'] = key
    return view(getattr(factory, method)('/', data, format='json', **headers))


def _admin_call(user, project, view, method, data=None, **kwargs):
    request = getattr(factory, method)(f'/?project={project.id}', data, format='json')
    force_authenticate(request, user=user)
    return view(request, **kwargs)


def _rows(data):
    return data['results'] if isinstance(data, dict) and 'results' in data else data


def _subscribe(organization, project, *events):
    return WebhookEndpoint.objects.create(
        organization=organization, project_id=project.id, url='https://example.com/hooks',
        events=list(events), secret_encrypted=encrypt_token('whsec_test'),
    )


# --- Authentication and access ----------------------------------------------

@pytest.mark.django_db
def test_api_key_is_required_and_scopes_limit_each_resource(organization, project, customer_organisation):
    own = Customer.objects.create(email='own@test.com', full_name='Own', project=project, organisation=customer_organisation)
    other = CustomerOrganisation.objects.create(
        name='Other', organization=Organization.objects.create(name='Other', email_domain='other.test'),
    )
    Customer.objects.create(email='foreign@test.com', full_name='Foreign', project=project, organisation=other)
    key = _credential(organization, project, 'customers:read')
    view = CustomerResource.as_view({'get': 'list', 'post': 'create'})

    assert _call(view, 'get').status_code == 401
    assert _call(view, 'get', key='zmk_abcd1234_wrong').status_code == 401
    assert _call(view, 'get', HTTP_AUTHORIZATION=f'Bearer {key}').status_code == 401
    listing = _call(view, 'get', key=key)
    assert listing.status_code == 200
    assert [row['id'] for row in _rows(listing.data)] == [own.id]
    new = {'email': 'new@test.com', 'full_name': 'New', 'organisation': customer_organisation.id}
    assert _call(view, 'post', new, key=key).status_code == 403
    assert _call(TicketResource.as_view({'get': 'list'}), 'get', key=key).status_code == 403


@pytest.mark.django_db
def test_write_key_creates_in_its_own_project_and_cannot_set_project_fields(
    organization, project, customer_organisation,
):
    key = _credential(organization, project, 'customers:write')
    view = CustomerResource.as_view({'post': 'create'})
    new = {'email': 'new@test.com', 'full_name': 'New', 'organisation': customer_organisation.id}

    rejected = _call(view, 'post', {**new, 'experience_group': 1}, key=key)
    created = _call(view, 'post', new, key=key)

    assert rejected.status_code == 400 and 'experience_group' in rejected.data
    assert created.status_code == 201, created.data
    assert Customer.objects.get(email='new@test.com').project_id == project.id


@pytest.mark.django_db
def test_client_credentials_grant_issues_a_working_bearer(organization, project, queue):
    _credential(organization, project, 'tickets:read', kind=CredentialKind.CLIENT)
    token_view = ClientTokenView.as_view()
    grant = {'grant_type': 'client_credentials', 'client_id': 'abcd1234', 'client_secret': 'super-secret'}

    assert _call(token_view, 'post', {**grant, 'grant_type': 'password'}).status_code == 400
    assert _call(token_view, 'post', {**grant, 'client_secret': 'wrong'}).status_code == 401
    token = _call(token_view, 'post', grant)
    assert token.status_code == 200 and token.data['token_type'] == 'Bearer'
    tickets = TicketResource.as_view({'get': 'list'})
    assert _call(tickets, 'get', HTTP_AUTHORIZATION=f"Bearer {token.data['access_token']}").status_code == 200
    assert _call(tickets, 'get', HTTP_AUTHORIZATION='Bearer not-a-token').status_code == 401
    assert _call(tickets, 'get', key=token.data['access_token']).status_code == 401


@pytest.mark.django_db
def test_each_credential_has_its_own_request_limit(organization, project, monkeypatch):
    monkeypatch.setattr(SimpleRateThrottle, 'THROTTLE_RATES', {'external_api': '2/minute'})
    first = _credential(organization, project, 'customers:read')
    Credential.objects.create(
        organization=organization, project_id=project.id, name='Other', kind=CredentialKind.API_KEY,
        public_id='efgh5678', secret_hash=hash_secret('other-secret'), scopes=['customers:read'],
    )
    customers = CustomerResource.as_view({'get': 'list'})

    assert [_call(customers, 'get', key=first).status_code for _ in range(3)] == [200, 200, 429]
    assert _call(customers, 'get', key='zmk_efgh5678_other-secret').status_code == 200


@pytest.mark.django_db
def test_token_requests_are_limited_per_caller(monkeypatch):
    monkeypatch.setattr(SimpleRateThrottle, 'THROTTLE_RATES', {'external_api_token': '2/minute'})
    grant = {'grant_type': 'client_credentials', 'client_id': 'unknown', 'client_secret': 'wrong'}

    assert [_call(ClientTokenView.as_view(), 'post', grant).status_code for _ in range(3)] == [401, 401, 429]


@pytest.mark.django_db
def test_tickets_from_another_workspace_stay_hidden(organization, project, queue):
    other = CustomerOrganisation.objects.create(
        name='Second', organization=Organization.objects.create(name='Second', email_domain='second.test'),
    )
    other_queue = Queue.objects.create(project=project, organisation=other, name='Elsewhere', tier='T1')
    ticket = Ticket.objects.create(queue=queue, title='In scope')
    Ticket.objects.create(queue=other_queue, title='Out of scope')

    response = _call(TicketResource.as_view({'get': 'list'}), 'get', key=_credential(organization, project, 'tickets:read'))

    assert [row['id'] for row in _rows(response.data)] == [ticket.id]


@pytest.mark.django_db
def test_agent_create_needs_a_workspace_member_and_never_grants_admin(
    organization, project, customer_organisation, queue, user,
):
    OrganizationMembership.objects.create(user=user, organization=organization, role='member', is_active=True)
    key = _credential(organization, project, 'agents:write')
    view = AgentResource.as_view({'post': 'create'})
    body = {'user_type': 'admin', 'organisation': customer_organisation.id, 'queue': queue.id}

    assert _call(view, 'post', {**body, 'email': 'stranger@test.com'}, key=key).status_code == 400
    assert _call(view, 'post', {**body, 'email': user.email}, key=key).status_code == 201
    assert CustomerUser.objects.get(user=user, queue=queue).user_type == 'agent'


@pytest.mark.django_db
def test_routing_rule_create_has_no_person_to_record(organization, project, queue):
    group = ExperienceGroup.objects.create(project=project, name='VIP')
    view = RoutingRuleResource.as_view({'post': 'create'})

    response = _call(view, 'post', {
        'name': 'VIP first', 'experience_group': group.id, 'target_queue': queue.id,
    }, key=_credential(organization, project, 'routing_rules:write'))

    assert response.status_code == 201, response.data
    assert RoutingRule.objects.get(pk=response.data['id']).created_by is None


# --- Admin console ------------------------------------------------------------

@pytest.mark.django_db
def test_org_admin_creates_and_revokes_credentials_and_secrets_show_once(organization, project, user):
    OrganizationMembership.objects.create(user=user, organization=organization, role='admin', is_active=True)
    new_key = {'name': 'Billing', 'kind': 'api_key', 'scopes': ['customers:read']}
    key = _admin_call(user, project, CredentialView.as_view(), 'post', new_key)
    new_hook = {'url': 'http://example.com/hook', 'events': ['ticket.created']}
    http = _admin_call(user, project, WebhookView.as_view(), 'post', new_hook)
    overview = _admin_call(user, project, IntegrationView.as_view(), 'get')

    assert key.status_code == 201 and key.data['secret'].startswith('zmk_')
    assert http.status_code == 400 and 'url' in http.data
    assert overview.status_code == 200
    assert overview.data['credentials'][0]['public_id'] == key.data['public_id']
    assert 'secret' not in overview.data['credentials'][0]

    assert _admin_call(user, project, CredentialView.as_view(), 'delete', pk=key.data['id']).status_code == 204
    customers = CustomerResource.as_view({'get': 'list'})
    assert _call(customers, 'get', key=key.data['secret']).status_code == 401
    assert _admin_call(user, project, IntegrationView.as_view(), 'get').data['credentials'] == []


@pytest.mark.django_db
def test_a_project_holds_a_limited_number_of_credentials_and_webhooks(organization, project, user):
    OrganizationMembership.objects.create(user=user, organization=organization, role='admin', is_active=True)
    for index in range(MAX_CREDENTIALS_PER_PROJECT):
        Credential.objects.create(
            organization=organization, project_id=project.id, name='Integration', kind=CredentialKind.API_KEY,
            public_id=f'key{index}', secret_hash=hash_secret('secret'),
        )
    for _ in range(MAX_WEBHOOKS_PER_PROJECT):
        _subscribe(organization, project, WebhookEvent.TICKET_CREATED)

    key = _admin_call(user, project, CredentialView.as_view(), 'post', {'name': 'Billing', 'kind': 'api_key'})
    new_hook = {'url': 'https://example.com/hook', 'events': ['ticket.created']}
    hook = _admin_call(user, project, WebhookView.as_view(), 'post', new_hook)

    assert key.status_code == 400 and 'at most' in key.data['detail']
    assert hook.status_code == 400 and 'at most' in hook.data['detail']


@pytest.mark.django_db
def test_csm_admin_who_is_not_an_org_admin_is_refused(project, customer_organisation, user):
    CustomerUser.objects.create(user=user, organisation=customer_organisation, user_type='admin', is_active=True)
    request = factory.post(f'/?project={project.id}', {'name': 'Billing', 'kind': 'api_key'}, format='json')
    force_authenticate(request, user=user)

    assert CredentialView.as_view()(request).status_code == 403


# --- Events -------------------------------------------------------------------

@pytest.fixture
def queued(monkeypatch):
    calls = []
    monkeypatch.setattr('external_api.tasks.deliver_webhook.delay', lambda *args: calls.append(args[1]))
    monkeypatch.setattr('external_api.tasks.transaction.on_commit', lambda fn: fn())
    return calls


@pytest.mark.django_db
def test_ticket_create_and_status_change_are_queued_for_subscribers(organization, project, queue, queued):
    _subscribe(organization, project, WebhookEvent.TICKET_CREATED, WebhookEvent.TICKET_STATUS_CHANGED)

    ticket = Ticket.objects.create(queue=queue, title='Hello')
    ticket.status = 'in_progress'
    ticket.save()
    ticket.save(update_fields=['title'])

    assert [payload['type'] for payload in queued] == ['ticket.created', 'ticket.status_changed']
    assert queued[1]['data']['previous_status'] == 'todo'


@pytest.mark.django_db
def test_sla_sweep_queues_one_breach(organization, project, queue, queued, monkeypatch):
    _subscribe(organization, project, WebhookEvent.SLA_BREACHED)
    Ticket.objects.create(queue=queue, title='Late', first_response_due=timezone.now() - timedelta(minutes=5))
    monkeypatch.setattr('csm.tasks.notify_sla_breach', lambda *args, **kwargs: None)
    monkeypatch.setattr('csm.services.sla._policy_for_ticket', lambda ticket: object())

    notify_sla_breaches()
    notify_sla_breaches()

    assert [(payload['type'], payload['data']['breach_type']) for payload in queued] == [('sla.breached', 'first_response')]


# --- Delivery -----------------------------------------------------------------

PAYLOAD = {'type': 'ticket.created', 'data': {'ticket_id': 1}}


@pytest.fixture
def endpoint(organization, project, monkeypatch):
    monkeypatch.setattr('external_api.tasks.validate_webhook_url', lambda url: (url, '93.184.216.34'))
    return _subscribe(organization, project, WebhookEvent.TICKET_CREATED)


@pytest.mark.django_db
def test_delivery_is_signed_and_logged(endpoint, monkeypatch):
    post = Mock(return_value=Mock(status_code=204))
    monkeypatch.setattr('external_api.tasks.requests.post', post)

    assert deliver_webhook(endpoint.id, PAYLOAD) == 204

    body, headers = post.call_args.kwargs['data'], post.call_args.kwargs['headers']
    assert json.loads(body) == PAYLOAD
    expected = hmac.new(b'whsec_test', body, hashlib.sha256).hexdigest()
    assert headers['X-Zmarkio-Signature'] == f'sha256={expected}'
    row = endpoint.deliveries.get()
    assert (row.event_type, row.response_code, row.attempt) == ('ticket.created', 204, 1)


@pytest.mark.django_db
def test_failed_delivery_retries_with_backoff_up_to_three_times(endpoint, monkeypatch):
    monkeypatch.setattr('external_api.tasks.requests.post', Mock(return_value=Mock(status_code=500)))
    retry = Mock(side_effect=Retry())
    monkeypatch.setattr(deliver_webhook, 'retry', retry)

    for retries in range(4):
        deliver_webhook.push_request(retries=retries)
        try:
            if retries < 3:
                with pytest.raises(Retry):
                    deliver_webhook.run(endpoint.id, PAYLOAD)
            else:
                assert deliver_webhook.run(endpoint.id, PAYLOAD) == 500
        finally:
            deliver_webhook.pop_request()

    assert [call.kwargs['countdown'] for call in retry.call_args_list] == [30, 120, 480]
    assert sorted(endpoint.deliveries.values_list('attempt', flat=True)) == [1, 2, 3, 4]


@pytest.mark.django_db
def test_blocked_url_is_logged_once_and_not_retried(endpoint, monkeypatch):
    monkeypatch.setattr('external_api.tasks.validate_webhook_url', Mock(side_effect=UnsafeUrlError('private')))
    post = Mock()
    monkeypatch.setattr('external_api.tasks.requests.post', post)

    assert deliver_webhook(endpoint.id, PAYLOAD) is None

    post.assert_not_called()
    assert endpoint.deliveries.get().response_code is None
