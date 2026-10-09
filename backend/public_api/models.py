from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel


class ApiKey(TimeStampedModel):
    """
    A key an external system sends as `X-API-Key: zmk_<prefix>_<secret>`.

    Only a SHA-256 of the secret is stored; the full key is shown once, on
    creation. The prefix is stored in clear so a request finds its row with one
    indexed lookup and admins can tell keys apart.
    """

    organization = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE, related_name='api_keys',
    )
    # core.Project is tenant-scoped and its ids repeat across organisation
    # schemas, so the project is pinned by (organization, project_id).
    project_id = models.PositiveIntegerField()
    name = models.CharField(max_length=200)
    prefix = models.CharField(max_length=16, unique=True)
    key_hash = models.CharField(max_length=64)
    scopes = models.JSONField(default=list)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['organization', 'project_id'], name='papi_key_org_project_idx'),
        ]

    @property
    def is_active(self):
        return self.revoked_at is None

    def __str__(self):
        return f"ApiKey '{self.name}' (zmk_{self.prefix})"


class OAuthClient(TimeStampedModel):
    """
    Binds a django-oauth-toolkit client-credentials Application to one
    (organization, project) and a set of scopes. Bearer tokens issued to the
    application act as this client, never as the admin who created it.
    """

    organization = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE, related_name='oauth_clients',
    )
    project_id = models.PositiveIntegerField()
    name = models.CharField(max_length=200)
    # Cleared on revoke: deleting the application also deletes its tokens.
    application = models.OneToOneField(
        settings.OAUTH2_PROVIDER_APPLICATION_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='zmarkio_client',
    )
    # Kept after revoke so the admin list still identifies the client.
    client_id = models.CharField(max_length=100)
    scopes = models.JSONField(default=list)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['organization', 'project_id'], name='papi_oauth_org_project_idx'),
        ]

    @property
    def is_active(self):
        return self.revoked_at is None and self.application_id is not None

    def __str__(self):
        return f"OAuthClient '{self.name}' ({self.client_id})"


class WebhookEvent(models.TextChoices):
    TICKET_CREATED = 'ticket.created', 'Ticket created'
    TICKET_STATUS_CHANGED = 'ticket.status_changed', 'Ticket status changed'
    SLA_BREACHED = 'sla.breached', 'SLA breached'


class WebhookEndpoint(TimeStampedModel):
    """An external URL that receives signed POSTs for the events it subscribes to."""

    organization = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE, related_name='webhook_endpoints',
    )
    project_id = models.PositiveIntegerField()
    url = models.URLField(max_length=2000)
    # WebhookEvent values.
    events = models.JSONField(default=list)
    # core.crypto.encrypt_token output; the plain secret is shown once, on create.
    secret_encrypted = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='+',
    )

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['organization', 'project_id'], name='papi_hook_org_project_idx'),
        ]

    def __str__(self):
        return f'WebhookEndpoint {self.url}'


class WebhookDelivery(models.Model):
    """One delivery attempt. Retries of an event are further rows with the same event_id."""

    class Status(models.TextChoices):
        SUCCEEDED = 'succeeded', 'Succeeded'
        RETRYING = 'retrying', 'Failed, retry scheduled'
        FAILED = 'failed', 'Failed'

    endpoint = models.ForeignKey(WebhookEndpoint, on_delete=models.CASCADE, related_name='deliveries')
    event_id = models.UUIDField()
    event_type = models.CharField(max_length=64)
    # Snapshot: the endpoint's URL may be edited after the attempt.
    target_url = models.URLField(max_length=2000)
    attempt = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=16, choices=Status.choices)
    response_code = models.PositiveSmallIntegerField(null=True, blank=True)
    error = models.CharField(max_length=1000, blank=True, default='')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['endpoint', '-created_at'], name='papi_delivery_endpoint_idx'),
        ]

    def __str__(self):
        return f'{self.event_type} → {self.target_url} (attempt {self.attempt}, {self.status})'
