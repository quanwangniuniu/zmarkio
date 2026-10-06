import logging

import requests
from celery import shared_task
from django.contrib.auth import get_user_model
from core.tenant_context import tenant_schema_context

from .models import AgentMessage, AgentWorkflowRun
from .services import _generate_miro_board_for_workflow_run

User = get_user_model()
logger = logging.getLogger(__name__)

AGENT_BOT_EMAIL = 'agent-bot@system.local'


class _TaskOrchestrator:
    def __init__(self, workflow_run):
        self.session = workflow_run.session
        self.user = workflow_run.session.user
        self.project = workflow_run.session.project


@shared_task
def generate_miro_board_for_workflow_run_task(workflow_run_id: str, context_payload: dict | None = None):
    try:
        workflow_run = AgentWorkflowRun.objects.select_related(
            "session",
            "session__user",
            "session__project",
            "miro_board",
        ).get(id=workflow_run_id, is_deleted=False)
    except AgentWorkflowRun.DoesNotExist:
        logger.warning("Workflow run not found for background Miro generation: %s", workflow_run_id)
        return

    if workflow_run.miro_board_id:
        logger.info("Skipping background Miro generation because board already exists for run=%s", workflow_run_id)
        return

    orchestrator = _TaskOrchestrator(workflow_run)
    logger.info("Starting background Miro generation for workflow_run=%s", workflow_run_id)

    try:
        _snapshot, board = _generate_miro_board_for_workflow_run(
            orchestrator,
            workflow_run,
            context_payload=context_payload,
        )
    except requests.HTTPError as exc:
        status_code = getattr(exc.response, "status_code", None)
        logger.exception(
            "Background Miro generation failed with HTTP error for workflow_run=%s",
            workflow_run_id,
        )
        AgentMessage.objects.create(
            session=workflow_run.session,
            role="assistant",
            content=f"Miro generation failed: HTTP {status_code or 'error'}.",
            message_type="error",
            metadata={
                "workflow_run_id": str(workflow_run.id),
                "event_type": "miro_generation_failed",
                "status_code": status_code,
            },
        )
        return
    except Exception as exc:
        logger.exception("Background Miro generation failed for workflow_run=%s", workflow_run_id)
        AgentMessage.objects.create(
            session=workflow_run.session,
            role="assistant",
            content=f"Miro generation failed: {exc}",
            message_type="error",
            metadata={
                "workflow_run_id": str(workflow_run.id),
                "event_type": "miro_generation_failed",
            },
        )
        return

    if board is None:
        logger.error(
            "Background Miro generation finished without a board for workflow_run=%s",
            workflow_run_id,
        )
        AgentMessage.objects.create(
            session=workflow_run.session,
            role="assistant",
            content="Miro generation failed: board was not created.",
            message_type="error",
            metadata={
                "workflow_run_id": str(workflow_run.id),
                "event_type": "miro_generation_failed",
            },
        )
        return

    logger.info(
        "Background Miro generation completed for workflow_run=%s board=%s",
        workflow_run_id,
        board.id,
    )
    AgentMessage.objects.create(
        session=workflow_run.session,
        role="assistant",
        content=f"Miro board is ready: {board.title}",
        message_type="text",
        metadata={
            "workflow_run_id": str(workflow_run.id),
            "board_id": str(board.id),
            "event_type": "miro_board_created",
        },
    )


@shared_task
def handle_chat_message_for_agent(message_id: int, tenant_schema: str = 'public'):
    with tenant_schema_context(tenant_schema):
        return _handle_chat_message_for_agent(message_id)


def _handle_chat_message_for_agent(message_id: int):
    """Process a chat message directed at the Agent Bot and send a reply."""
    from chat.models import ChatParticipant, Message
    from chat.services import MessageService
    from .chat_service import AgentChatService

    try:
        message = Message.objects.select_related('chat', 'sender').get(id=message_id)
    except Message.DoesNotExist:
        logger.warning("handle_chat_message_for_agent: message %s not found", message_id)
        return

    chat = message.chat

    # Validate bot is still a participant
    try:
        bot_user = User.objects.get(email=AGENT_BOT_EMAIL)
    except User.DoesNotExist:
        logger.warning("handle_chat_message_for_agent: bot user not found")
        return

    if not ChatParticipant.objects.filter(chat=chat, user=bot_user, is_active=True).exists():
        logger.info("handle_chat_message_for_agent: bot not a participant in chat %s", chat.id)
        return

    # Generate reply
    reply_text = AgentChatService.generate_reply(
        message=message.content or "",
        page_context="chat_widget",
        user_id=message.sender_id,
    )

    # Create bot reply via MessageService
    try:
        bot_message = MessageService.create_message(
            chat=chat,
            sender=bot_user,
            content=reply_text,
        )
    except ValueError:
        logger.warning("handle_chat_message_for_agent: bot cannot send to chat %s", chat.id)
        return

    # MessageService created a durable realtime outbox event in the same
    # transaction, so no direct broker publish is needed here.
    logger.info("handle_chat_message_for_agent: bot replied with message %s in chat %s", bot_message.id, chat.id)


# ============================================================================
# Workflow Trigger Tasks
# ============================================================================

@shared_task(name="agent.tasks.check_polling_triggers")
def check_polling_triggers() -> int:
    """
    Check all polling triggers and execute workflows when conditions are met.
    Runs every 5 minutes (configured in CELERY_BEAT_SCHEDULE).
    """
    from .trigger_handlers import PollingHandler
    return PollingHandler.check_all_polling_workflows()


@shared_task(name="agent.tasks.check_scheduled_triggers")
def check_scheduled_triggers() -> int:
    """
    Check all scheduled triggers and execute workflows at scheduled times.
    Runs every minute (configured in CELERY_BEAT_SCHEDULE).
    """
    from .trigger_handlers import ScheduledHandler
    return ScheduledHandler.check_all_scheduled_workflows()


@shared_task(name="agent.tasks.cleanup_old_trigger_logs")
def cleanup_old_trigger_logs() -> int:
    """
    Delete trigger logs older than 30 days.
    Runs daily at 02:00 UTC (configured in CELERY_BEAT_SCHEDULE).
    """
    from datetime import timedelta
    from django.utils import timezone
    from .models import WorkflowTriggerLog

    cutoff = timezone.now() - timedelta(days=30)
    deleted_count, _ = WorkflowTriggerLog.objects.filter(
        created_at__lt=cutoff
    ).delete()

    logger.info(f"Cleaned up {deleted_count} old trigger logs")
    return deleted_count


@shared_task(name="agent.tasks.execute_workflow_async")
def execute_workflow_async(workflow_id: str, trigger_context: dict) -> str:
    """
    Execute a workflow asynchronously (for polling/scheduled triggers).
    Returns workflow_run_id.
    """
    from .trigger_service import TriggerExecutionService
    from .models import AgentWorkflowDefinition

    try:
        workflow = AgentWorkflowDefinition.objects.get(id=workflow_id)
        return TriggerExecutionService.execute_workflow_trigger(
            workflow_id=workflow_id,
            trigger_type=trigger_context.get('trigger_type', 'polling'),
            trigger_context=trigger_context,
            user=workflow.created_by,
            project=workflow.project,
        )
    except Exception as e:
        logger.exception(f"Error executing workflow {workflow_id} asynchronously: {e}")
        return None
