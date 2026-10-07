"""
The rows a public API credential may see, for every resource.

CSM tables live in the public schema and project ids repeat across organisation
schemas, so a project id alone never identifies a workspace. Every queryset here
pins the credential's organisation as well as its project, and every public view
and write validator builds on these functions instead of filtering on its own.
"""

from django.contrib.auth import get_user_model
from django.db.models import Q

from csm.models import (
    Conversation, ConversationMessage, CustomerUser, Queue, QuickReplyTemplate, RoutingRule, SLAPolicy, Ticket,
)
from customer.models import Customer, CustomerOrganisation, CustomerStatusLabel, Region
from experience_group.models import ExperienceGroup

User = get_user_model()


def customer_organisations(principal):
    return CustomerOrganisation.objects.filter(organization_id=principal.organization_id)


def queues(principal):
    return Queue.objects.filter(
        project_id=principal.project_id,
        organisation__organization_id=principal.organization_id,
    )


def tickets(principal):
    return Ticket.objects.filter(queue__in=queues(principal))


def conversations(principal):
    # Conversations without a queue (portal intake before routing) can't be
    # attributed to a workspace and are not exposed.
    return Conversation.objects.filter(queue__in=queues(principal))


def messages(principal):
    return ConversationMessage.objects.filter(conversation__in=conversations(principal))


def customers(principal):
    return Customer.objects.filter(
        organization_id=principal.organization_id,
        project_id=principal.project_id,
    )


def templates(principal):
    return QuickReplyTemplate.objects.filter(organisation__organization_id=principal.organization_id)


def routing_rules(principal):
    return RoutingRule.objects.filter(
        organization_id=principal.organization_id,
        project_id=principal.project_id,
    )


def agents(principal):
    return CustomerUser.objects.filter(organisation__organization_id=principal.organization_id)


# Project-level configuration. ExperienceGroup, CustomerStatusLabel and Region
# have no organisation column, so like the internal API they are matched on the
# project id; a region tied to another workspace's customer organisation is
# excluded. Adding an organisation to these tables is a follow-up.

def experience_groups(principal):
    return ExperienceGroup.objects.filter(project_id=principal.project_id)


def status_labels(principal):
    return CustomerStatusLabel.objects.filter(project_id=principal.project_id)


def regions(principal):
    return Region.objects.filter(project_id=principal.project_id).filter(
        Q(organisation__isnull=True) | Q(organisation__organization_id=principal.organization_id),
    )


def sla_policies(principal):
    return SLAPolicy.objects.filter(project_id=principal.project_id)


def assignable_users(principal):
    """People with an active CSM profile in the workspace: who a ticket may be assigned to."""
    return User.objects.filter(
        customer_user_profiles__organisation__organization_id=principal.organization_id,
        customer_user_profiles__is_active=True,
    ).distinct()
