from django.db import models


class CredentialKind(models.TextChoices):
    API_KEY = 'api_key', 'API key'
    CLIENT = 'client', 'Bearer client'


class WebhookEvent(models.TextChoices):
    TICKET_CREATED = 'ticket.created', 'Ticket created'
    TICKET_STATUS_CHANGED = 'ticket.status_changed', 'Ticket status changed'
    SLA_BREACHED = 'sla.breached', 'SLA breached'
