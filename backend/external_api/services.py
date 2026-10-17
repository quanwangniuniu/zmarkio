"""
External API logic: what a credential may see (scoped_* querysets pin organisation and project,
because project ids repeat across organisation schemas), credentials, and signed webhook delivery.
"""

import hashlib
import hmac
import json
import secrets
import time
import uuid
from functools import partial
from urllib.parse import urlparse

import requests
from django.contrib.auth import get_user_model
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from oauth2_provider.generators import generate_client_secret
from oauth2_provider.models import get_application_model

from chat.services import UnsafeUrlError, _pinned_address, resolve_public_url
from core.crypto import decrypt_token, encrypt_token
from core.models import Organization, Project
from csm.models import (
    Conversation, CustomerUser, Queue, QuickReplyTemplate, RoutingRule, SLAPolicy, Ticket,
)
from customer.models import Customer, CustomerOrganisation, CustomerStatusLabel, Region
from experience_group.models import ExperienceGroup
from external_api.models import ApiKey, OAuthClient, WebhookEndpoint

User = get_user_model()

KEY_PREFIX = 'zmk'
WEBHOOK_TIMEOUT_SECONDS = 10


def scoped_customer_organisations(principal):
    return CustomerOrganisation.objects.filter(organization_id=principal.organization_id)


def scoped_queues(principal):
    return Queue.objects.filter(
        project_id=principal.project_id,
        organisation__organization_id=principal.organization_id,
    )


def scoped_tickets(principal):
    return Ticket.objects.filter(queue__in=scoped_queues(principal))


def scoped_conversations(principal):
    # Conversations without a queue (portal intake before routing) can't be
    # attributed to a workspace and are not exposed.
    return Conversation.objects.filter(queue__in=scoped_queues(principal))


def scoped_customers(principal):
    return Customer.objects.filter(
        organization_id=principal.organization_id,
        project_id=principal.project_id,
    )


def scoped_templates(principal):
    return QuickReplyTemplate.objects.filter(organisation__organization_id=principal.organization_id)


def scoped_routing_rules(principal):
    return RoutingRule.objects.filter(
        organization_id=principal.organization_id,
        project_id=principal.project_id,
    )


def scoped_agents(principal):
    return CustomerUser.objects.filter(organisation__organization_id=principal.organization_id)


# Project-level configuration. ExperienceGroup, CustomerStatusLabel and Region
# have no organisation column, so like the internal API they are matched on the
# project id; a region tied to another workspace's customer organisation is
# excluded. Adding an organisation to these tables is a follow-up.

def scoped_experience_groups(principal):
    return ExperienceGroup.objects.filter(project_id=principal.project_id)


def scoped_status_labels(principal):
    return CustomerStatusLabel.objects.filter(project_id=principal.project_id)


def scoped_regions(principal):
    return Region.objects.filter(project_id=principal.project_id).filter(
        Q(organisation__isnull=True) | Q(organisation__organization_id=principal.organization_id),
    )


def scoped_sla_policies(principal):
    return SLAPolicy.objects.filter(project_id=principal.project_id)


def scoped_workspace(principal):
    return Organization.objects.filter(pk=principal.organization_id)


def scoped_project(principal):
    return Project.objects.filter(pk=principal.project_id)


def scoped_assignable_users(principal):
    """People with an active CSM profile in the workspace: who a ticket may be assigned to."""
    return User.objects.filter(
        customer_user_profiles__organisation__organization_id=principal.organization_id,
        customer_user_profiles__is_active=True,
    ).distinct()


def _hash_secret(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


def create_api_key(*, organization_id, project_id, name, scopes, user):
    """Return (ApiKey, full key). The full key is not recoverable afterwards."""
    prefix = secrets.token_hex(4)  # unique=True on the column guards the rare clash
    secret = secrets.token_urlsafe(32)
    key = ApiKey.objects.create(
        organization_id=organization_id,
        project_id=project_id,
        name=name,
        prefix=prefix,
        key_hash=_hash_secret(secret),
        scopes=scopes,
        created_by=user,
    )
    return key, f'{KEY_PREFIX}_{prefix}_{secret}'


def verify_api_key(raw):
    """The active ApiKey for `raw`, or None. The organisation is loaded with it."""
    parts = raw.split('_', 2)
    if len(parts) != 3 or parts[0] != KEY_PREFIX:
        return None
    _, prefix, secret = parts
    key = ApiKey.objects.select_related('organization').filter(prefix=prefix).first()
    if key is None or not hmac.compare_digest(key.key_hash, _hash_secret(secret)):
        return None
    if not key.is_active or not key.organization.is_active:
        return None
    return key


def revoke_api_key(key):
    if key.revoked_at is None:
        key.revoked_at = timezone.now()
        key.save(update_fields=['revoked_at', 'updated_at'])
    return key


@transaction.atomic
def create_oauth_client(*, organization_id, project_id, name, scopes, user):
    """Return (OAuthClient, client secret). The secret is stored hashed by the toolkit."""
    Application = get_application_model()
    secret = generate_client_secret()
    application = Application.objects.create(
        name=name,
        client_type=Application.CLIENT_CONFIDENTIAL,
        authorization_grant_type=Application.GRANT_CLIENT_CREDENTIALS,
        client_secret=secret,
        skip_authorization=True,
    )
    client = OAuthClient.objects.create(
        organization_id=organization_id,
        project_id=project_id,
        name=name,
        application=application,
        client_id=application.client_id,
        scopes=scopes,
        created_by=user,
    )
    return client, secret


@transaction.atomic
def revoke_oauth_client(client):
    """Delete the toolkit application, which invalidates every token it was issued."""
    application = client.application
    if application is not None:
        client.application = None
        client.save(update_fields=['application', 'updated_at'])
        application.delete()
    return client


def canonical_json(payload):
    return json.dumps(payload, sort_keys=True, separators=(',', ':'), cls=DjangoJSONEncoder)


def delivery_headers(endpoint, payload, body):
    timestamp = int(time.time())
    secret = decrypt_token(endpoint.secret_encrypted)
    signature = hmac.new(secret.encode(), f'{timestamp}.'.encode() + body, hashlib.sha256).hexdigest()
    return {
        'Content-Type': 'application/json',
        'X-Zmarkio-Signature': f't={timestamp},v1={signature}',
        'X-Zmarkio-Event': payload['type'],
        'X-Zmarkio-Delivery': payload['id'],
    }


def validate_webhook_url(url):
    """Return (normalized url, vetted address); raise UnsafeUrlError unless the server may deliver to `url`."""
    if urlparse(url).scheme.lower() != 'https':
        raise UnsafeUrlError('Webhook URLs must use https.')
    return resolve_public_url(url)


def post_json_safely(url, body, headers):
    """POST `body` (bytes) to `url`; the caller must close the returned response."""
    normalized, address = validate_webhook_url(url)
    with _pinned_address(urlparse(normalized).hostname, address):
        return requests.post(
            normalized, data=body, headers=headers,
            timeout=WEBHOOK_TIMEOUT_SECONDS, allow_redirects=False, stream=True,
        )


def create_endpoint(*, organization_id, project_id, url, events, user):
    """Return (endpoint, signing secret). The secret is not recoverable from the API afterwards."""
    secret = f'whsec_{secrets.token_urlsafe(32)}'
    endpoint = WebhookEndpoint.objects.create(
        organization_id=organization_id,
        project_id=project_id,
        url=url,
        events=events,
        secret_encrypted=encrypt_token(secret),
        created_by=user,
    )
    return endpoint, secret


def build_event(event_type, *, organization_id, project_id, data):
    payload = {
        'id': str(uuid.uuid4()),
        'type': event_type,
        'created_at': timezone.now().isoformat(),
        'organization_id': organization_id,
        'project_id': project_id,
        'data': data,
    }
    # Round-trip so dates and decimals are plain JSON for Celery.
    return json.loads(canonical_json(payload))


def emit_ticket_event(event_type, ticket, **extra):
    """Queue `event_type` for every active endpoint of the ticket's workspace subscribed to it."""
    from csm.serializers import TicketSerializer
    from external_api.tasks import deliver_webhook

    queue = ticket.queue
    if queue is None or queue.organisation is None or queue.project_id is None:
        return  # no workspace to announce it to
    organization_id = queue.organisation.organization_id
    endpoint_ids = list(
        WebhookEndpoint.objects.filter(
            organization_id=organization_id,
            project_id=queue.project_id,
            events__contains=[event_type],
        ).values_list('id', flat=True)
    )
    if not endpoint_ids:
        return
    data = {'ticket': TicketSerializer(ticket).data, **extra}
    payload = build_event(event_type, organization_id=organization_id, project_id=queue.project_id, data=data)
    for endpoint_id in endpoint_ids:
        # After commit: nothing is announced for a write that rolls back.
        transaction.on_commit(partial(deliver_webhook.delay, endpoint_id, payload))
