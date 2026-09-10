"""
RAG indexing signals for the notion_editor app (MED-264).

Draft/ContentBlock handlers follow the same broad-save principle as
meetings/signals.py: they just say "this draft's content may have changed"
and let `index_source_document`'s content_hash + pipeline_hash check decide
cheaply whether anything actually needs re-embedding (see that module's
docstring, and meetings/signals.py's, for the reliability trade-offs of
`transaction.on_commit` + logged-and-swallowed `.delay()` failures — the
same applies here, not repeated below).

The one exception is DraftProjectLink: ownership movement is NOT a "maybe
changed" signal like a content edit — ownership changing is itself the
event, and it requires two distinct actions (clean up the OLD project,
index the NEW one), not a single "re-check this document" nudge. That's why
DraftProjectLink alone gets an old/new project_id diff via pre_save +
post_save, reusing rag.indexing's existing move-detection branch (an
`index_document_task` call against the OLD project_id will itself discover
the draft now belongs elsewhere and clean up — no separate cleanup code
path needed here).
"""
import logging

from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from core.tenant_context import current_tenant_schema
from notion_editor.models import ContentBlock, Draft, DraftProjectLink
from rag.models import DocumentSourceType

logger = logging.getLogger(__name__)


def _enqueue_draft_index(project_id: int, draft_id, tenant_schema: str | None = None) -> None:
    schema = tenant_schema or current_tenant_schema()

    def enqueue() -> None:
        try:
            from rag.tasks import index_document_task
            index_document_task.delay(
                tenant_schema=schema,
                project_id=project_id,
                source_type=DocumentSourceType.NOTION_DRAFT,
                source_id=str(draft_id),
            )
        except Exception:
            logger.exception('Failed to enqueue RAG indexing for draft %s', draft_id)

    transaction.on_commit(enqueue)


def _enqueue_draft_index_if_linked(draft_id) -> None:
    """Cheap pre-check so editing an unassigned draft (likely the common
    case — most existing Drafts predate project scoping) doesn't enqueue a
    Celery task that can only ever no-op. Purely an optimization:
    `index_source_document` already handles an unassigned draft correctly
    (EXCLUDED, idempotent) if this check and the task's actual run
    disagree because the link changed in between.
    """
    project_id = DraftProjectLink.objects.filter(draft_id=draft_id).values_list('project_id', flat=True).first()
    if project_id is None:
        return
    _enqueue_draft_index(project_id, draft_id)


@receiver(post_save, sender=Draft)
def handle_draft_saved(sender, instance: Draft, **kwargs) -> None:
    _enqueue_draft_index_if_linked(instance.pk)


@receiver(post_save, sender=ContentBlock)
def handle_content_block_saved(sender, instance: ContentBlock, **kwargs) -> None:
    _enqueue_draft_index_if_linked(instance.draft_id)


@receiver(post_delete, sender=ContentBlock)
def handle_content_block_deleted(sender, instance: ContentBlock, **kwargs) -> None:
    # draft_id is a plain FK column on `instance`, always available without
    # a query regardless of what happens to the parent Draft.
    _enqueue_draft_index_if_linked(instance.draft_id)


@receiver(pre_save, sender=DraftProjectLink)
def capture_draft_project_link_old_project(sender, instance: DraftProjectLink, **kwargs) -> None:
    """Stash the pre-update project_id on the instance so post_save can
    diff old vs new. Standard Django pattern for detecting a field change
    across a save: pre_save is the only point with access to what's
    currently in the DB before this save overwrites it.
    """
    if instance.pk:
        instance._old_project_id = (
            DraftProjectLink.objects.filter(pk=instance.pk).values_list('project_id', flat=True).first()
        )
    else:
        instance._old_project_id = None


@receiver(post_save, sender=DraftProjectLink)
def handle_draft_project_link_saved(sender, instance: DraftProjectLink, created: bool, **kwargs) -> None:
    old_project_id = getattr(instance, '_old_project_id', None)
    new_project_id = instance.project_id
    draft_id = instance.draft_id
    tenant_schema = current_tenant_schema()

    if old_project_id is not None and old_project_id != new_project_id:
        # Reassigned, not a fresh link. index_document_task against the OLD
        # project_id will re-extract, see the draft now belongs to
        # new_project_id, and take rag.indexing's existing MOVED branch
        # (hard-delete old_project_id's chunks, reset its state) — no
        # separate cleanup logic needed here.
        _enqueue_draft_index(old_project_id, draft_id, tenant_schema)

    _enqueue_draft_index(new_project_id, draft_id, tenant_schema)


@receiver(post_delete, sender=DraftProjectLink)
def handle_draft_project_link_deleted(sender, instance: DraftProjectLink, **kwargs) -> None:
    # project_id/draft_id are plain FK columns on `instance` — always
    # available without a query, regardless of whether this fired from an
    # explicit unlink or a cascade delete of the Draft itself. Either way,
    # index_document_task will find the draft unassigned (or gone) and take
    # the EXCLUDED path.
    _enqueue_draft_index(instance.project_id, instance.draft_id)
