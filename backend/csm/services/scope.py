"""Queue visibility helpers for CSM.

Two distinct scopes live here:

* ``accessible_queues_for`` — queues a user may work in. Agents included.
  This is the long-standing rule extracted verbatim from
  ``ConversationViewSet._accessible_queues`` so the agent workspace and the
  quality inspection area cannot drift apart.
* ``supervised_queues_for`` — queues a user may *review*. Supervisors and
  admins only, and a strict subset of the accessible set.
"""

from django.db.models import Q

from core.admin_utils import get_csm_supervisor_org_ids
from csm.models import CustomerUser, Queue, QueueAgent


def accessible_queues_for(user):
    """Active queues the user may work in (staff see all)."""
    qs = Queue.objects.filter(is_active=True)
    if user.is_staff or user.is_superuser:
        return qs

    admin_org_ids = CustomerUser.objects.filter(
        user=user,
        is_active=True,
        user_type__in=('supervisor', 'admin'),
        organisation__isnull=False,
    ).values_list('organisation_id', flat=True)
    agent_queue_ids = QueueAgent.objects.filter(user=user).values_list('queue_id', flat=True)
    profile_queue_ids = CustomerUser.objects.filter(
        user=user,
        is_active=True,
        queue__isnull=False,
    ).values_list('queue_id', flat=True)

    return qs.filter(
        Q(organisation_id__in=admin_org_ids)
        | Q(id__in=agent_queue_ids)
        | Q(id__in=profile_queue_ids)
    ).distinct()


def supervised_queues_for(user, include_inactive=True):
    """Queues the user supervises, for quality inspection.

    Unlike ``accessible_queues_for`` this includes **deactivated** queues by
    default: quality inspection is historical, so archiving a queue must not
    erase its conversations from review or shift past report totals.

    Being an agent on a queue grants no supervision over it — a user who is an
    agent on queue A and a supervisor of org B sees org B only.
    """
    qs = Queue.objects.all() if include_inactive else Queue.objects.filter(is_active=True)
    if user.is_staff or user.is_superuser:
        return qs

    org_ids = list(get_csm_supervisor_org_ids(user))
    if not org_ids:
        return Queue.objects.none()
    return qs.filter(organisation_id__in=org_ids).distinct()
