# Retrospective Engine Signals
# Handles automatic retrospective task creation and status updates

import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_delete
from django.dispatch import receiver

from core.tenant_context import current_tenant_schema
from rag.models import DocumentSourceType

from .models import Insight, RetrospectiveTask

# Optional Celery import - only import if Celery is available
try:
    from .tasks import generate_retrospective
    CELERY_AVAILABLE = True
except ImportError:
    CELERY_AVAILABLE = False
    generate_retrospective = None

User = get_user_model()

logger = logging.getLogger(__name__)


# --- RAG indexing (MED-264) ------------------------------------------------
# Same broad-save principle as meetings/signals.py and notion_editor/signals.py:
# these handlers just say "this retrospective's content may have changed" and
# let index_source_document's content_hash + pipeline_hash check decide
# cheaply whether anything actually needs re-embedding. See those modules'
# docstrings for the transaction.on_commit + best-effort-delivery trade-offs,
# which apply identically here and aren't repeated.
#
# RetrospectiveTask/Insight are public-schema models (not registered in
# core.tenant_config.get_tenant_models()), unlike Meeting/Draft/Project --
# but `current_tenant_schema()` still reports the org schema the current
# request is running under, which is what indexing needs to know which
# project's tenant-scoped DocumentChunk/DocumentIndexState rows to touch.

def _enqueue_retrospective_index(project_id, retrospective_id, tenant_schema=None) -> None:
    schema = tenant_schema or current_tenant_schema()

    def enqueue() -> None:
        try:
            from rag.tasks import index_document_task
            index_document_task.delay(
                tenant_schema=schema,
                project_id=project_id,
                source_type=DocumentSourceType.RETROSPECTIVE,
                source_id=str(retrospective_id),
            )
        except Exception:
            logger.exception('Failed to enqueue RAG indexing for retrospective %s', retrospective_id)

    transaction.on_commit(enqueue)


@receiver(pre_delete, sender=Insight)
def capture_insight_campaign_before_delete(sender, instance, **kwargs):
    """Capture campaign_id BEFORE deletion, not in post_delete -- mirrors
    meetings/signals.py's MeetingDocument fix: during a RetrospectiveTask
    cascade delete we don't rely on any assumption about reverse deletion
    ordering. If the parent can't be resolved here, RetrospectiveTask's own
    post_delete handler below independently enqueues the same cleanup for
    the whole source (a harmless duplicate; indexing is idempotent).
    """
    try:
        instance._rag_project_id = instance.retrospective.campaign_id
    except RetrospectiveTask.DoesNotExist:
        instance._rag_project_id = None
# --- end RAG indexing additions; existing handlers below extended in place ---


@receiver(post_save, sender=RetrospectiveTask)
def handle_retrospective_status_change(sender, instance, created, **kwargs):
    """
    Handle retrospective task status changes
    """
    _enqueue_retrospective_index(instance.campaign_id, instance.id)

    if created:
        # New retrospective task created
        # Could trigger initial KPI data generation
        pass
    else:
        # Status changed - could trigger notifications or follow-up actions
        if instance.status == 'completed':
            # Retrospective completed - could trigger report generation
            pass
        elif instance.status == 'approved':
            # Retrospective approved - could trigger notifications
            pass


@receiver(post_save, sender=Insight)
def handle_insight_creation(sender, instance, created, **kwargs):
    """
    Handle insight creation and updates
    """
    _enqueue_retrospective_index(instance.retrospective.campaign_id, instance.retrospective_id)

    if created:
        # New insight created - could trigger notifications
        pass
    else:
        # Insight updated - could trigger re-evaluation
        pass


@receiver(post_delete, sender=RetrospectiveTask)
def handle_retrospective_deletion(sender, instance, **kwargs):
    """
    Handle retrospective task deletion
    """
    # campaign_id/id are plain columns on `instance` -- no relation query,
    # no cascade-ordering concern. index_document_task will find the row
    # missing and take the EXCLUDED path (hard-delete any existing chunks).
    _enqueue_retrospective_index(instance.campaign_id, instance.id)
    # Clean up related data if needed
    pass


@receiver(post_delete, sender=Insight)
def handle_insight_deletion(sender, instance, **kwargs):
    """
    Handle insight deletion
    """
    project_id = getattr(instance, '_rag_project_id', None)
    if project_id is not None:
        _enqueue_retrospective_index(project_id, instance.retrospective_id)
    # else: parent RetrospectiveTask was already gone by pre_delete (a
    # cascade delete, which that model's own post_delete handler above
    # already covers).
    # Clean up related data if needed
    pass 