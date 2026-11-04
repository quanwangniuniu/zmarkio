from django.db import models
from django.db.models.signals import post_init, post_save
from django.dispatch import receiver
from django.utils import timezone

from core.models import TimeStampedModel
from external_api.enums import CredentialKind, WebhookEvent


class Credential(TimeStampedModel):
    """An API key or a bearer client for one organisation and project.

    The secret is stored as a SHA-256 hex digest and shown once, on create.
    """

    organization = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE, related_name='external_credentials',
    )
    # core.Project ids repeat across organisation schemas, so the project is
    # stored as an integer next to the organisation.
    project_id = models.PositiveIntegerField()
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=16, choices=CredentialKind.choices)
    public_id = models.CharField(max_length=64, unique=True)
    secret_hash = models.CharField(max_length=64)
    # '<resource>:read' / '<resource>:write' entries, see external_api.views.SCOPES.
    scopes = models.JSONField(default=list)

    class Meta:
        ordering = ['-created_at', '-id']


class WebhookEndpoint(TimeStampedModel):
    organization = models.ForeignKey(
        'core.Organization', on_delete=models.CASCADE, related_name='webhook_endpoints',
    )
    project_id = models.PositiveIntegerField()
    url = models.URLField(max_length=2000)
    events = models.JSONField(default=list)
    # core.crypto.encrypt_token output. The plain secret is shown once, on create.
    secret_encrypted = models.TextField()

    class Meta:
        ordering = ['-created_at', '-id']


class WebhookDelivery(models.Model):
    """One delivery attempt. Each retry is another row."""

    endpoint = models.ForeignKey(WebhookEndpoint, on_delete=models.CASCADE, related_name='deliveries')
    event_type = models.CharField(max_length=64)
    attempt = models.PositiveSmallIntegerField(default=1)
    # Empty when the request never got a response.
    response_code = models.PositiveSmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at', '-id']


@receiver(post_init, sender='csm.Ticket')
def _remember_ticket_status(sender, instance, **kwargs):
    # __dict__, so a deferred status is not loaded here.
    instance._webhook_status = instance.__dict__.get('status')


@receiver(post_save, sender='csm.Ticket')
def _ticket_saved(sender, instance, created, update_fields, **kwargs):
    from external_api.tasks import emit_ticket_event

    previous = instance._webhook_status
    instance._webhook_status = instance.status
    if created:
        emit_ticket_event(WebhookEvent.TICKET_CREATED, instance)
        return
    if previous and previous != instance.status and (update_fields is None or 'status' in update_fields):
        emit_ticket_event(WebhookEvent.TICKET_STATUS_CHANGED, instance, previous_status=previous)
    # The SLA sweep saves exactly this flag when it records a breach.
    for breach_type in ('first_response', 'resolution'):
        flag = f'{breach_type}_breach_notified'
        if update_fields and flag in update_fields and getattr(instance, flag):
            emit_ticket_event(WebhookEvent.SLA_BREACHED, instance, breach_type=breach_type)
