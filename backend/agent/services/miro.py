"""Miro board generation for agent workflow runs."""
import logging

from .common import _create_agent_status_message

logger = logging.getLogger(__name__)

# Legacy SSE + persisted assistant row — distinct from the board-ready message Celery sends later.
MIRO_LEGACY_BG_QUEUED_MESSAGE = (
    "Queued Miro board generation — we'll notify you here when the board is ready."
)


def _generate_miro_board_for_workflow_run(orchestrator, workflow_run, context_payload=None):
    """Generate Miro snapshot (Gemini) and persist the board.

    Legacy ``generate_miro`` is an explicit user action — clicking Generate Miro
    counts as approval, so we never pause on a separate miro_board approval step.
    """
    from ..approval_gate import KIND_MIRO_BOARD
    from ..miro_generation import (
        build_miro_generation_context_from_run,
        call_gemini_miro_generator,
        deserialize_miro_generation_context,
        serialize_miro_generation_context,
    )
    from ..miro_board_service import create_board_from_snapshot
    from ..models import AgentPendingExternalApproval

    snapshot = workflow_run.miro_snapshot
    if not snapshot:
        try:
            context = deserialize_miro_generation_context(context_payload)
        except ValueError:
            logger.warning(
                "Invalid Miro generation context payload for workflow_run=%s; rebuilding from run",
                getattr(workflow_run, "id", workflow_run),
            )
            context = None
        if context is None:
            context = build_miro_generation_context_from_run(
                session=orchestrator.session,
                workflow_run=workflow_run,
            )
            context = serialize_miro_generation_context(context)
        snapshot = call_gemini_miro_generator(
            context,
            user_id=str(orchestrator.user.id),
            agent_session=orchestrator.session,
        )

    board, persisted_snapshot = create_board_from_snapshot(
        project=orchestrator.project,
        session=orchestrator.session,
        workflow_run=workflow_run,
        snapshot=snapshot,
    )
    workflow_run.miro_snapshot = persisted_snapshot
    workflow_run.miro_board = board
    workflow_run.save(update_fields=['miro_snapshot', 'miro_board'])

    AgentPendingExternalApproval.objects.filter(
        workflow_run=workflow_run,
        kind=KIND_MIRO_BOARD,
        status='pending',
    ).update(status='approved')

    return persisted_snapshot, board


def _enqueue_miro_generation_for_workflow_run(orchestrator, workflow_run):
    """Queue Miro generation so task creation can return immediately."""
    from ..miro_generation import (
        build_miro_generation_context_from_run,
        serialize_miro_generation_context,
    )
    from ..tasks import generate_miro_board_for_workflow_run_task

    context = build_miro_generation_context_from_run(
        session=orchestrator.session,
        workflow_run=workflow_run,
    )
    context_payload = serialize_miro_generation_context(context)

    logger.info(
        "Queueing background Miro generation for workflow_run=%s session=%s",
        workflow_run.id,
        orchestrator.session.id,
    )
    generate_miro_board_for_workflow_run_task.delay(
        str(workflow_run.id),
        context_payload=context_payload,
    )


class MiroMixin:
    """Legacy Miro background generation for AgentOrchestrator."""

    def _legacy_start_miro_background_if_needed(self, workflow_run):
        """Enqueue Celery job and persist started row at most once per workflow run."""
        from django.db import transaction

        from ..models import AgentMessage, AgentWorkflowRun

        wr_pk = workflow_run.pk
        with transaction.atomic():
            # IMPORTANT: do not join the nullable `miro_board` FK while taking a row lock.
            # Postgres rejects `FOR UPDATE` on the nullable side of an outer join.
            locked = AgentWorkflowRun.objects.select_for_update().get(pk=wr_pk)

            if getattr(locked, 'miro_board_id', None):
                # Fetch title without a locking join.
                title = ''
                try:
                    from miro.models import Board
                    board = Board.objects.filter(id=locked.miro_board_id).only('title').first()
                    title = (getattr(board, 'title', None) or '') if board else ''
                except Exception:
                    title = ''
                return 'already_exists', locked, title

            dup = AgentMessage.objects.filter(
                session=self.session,
                role='assistant',
                metadata__contains={
                    'event_type': 'miro_generation_started',
                    'workflow_run_id': str(locked.id),
                },
            ).exists()

            _enqueue_miro_generation_for_workflow_run(self, locked)
            if not dup:
                _create_agent_status_message(
                    self.session,
                    MIRO_LEGACY_BG_QUEUED_MESSAGE,
                    event_type='miro_generation_started',
                    workflow_run_id=str(locked.id),
                )
            return 'started', locked, None
