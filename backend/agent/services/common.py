"""Small helpers shared across the agent service modules."""
import json
import logging

from ..models import AgentSession, AgentMessage

logger = logging.getLogger(__name__)


def _create_agent_status_message(session, content, *, event_type, message_type='text', **metadata):
    if not isinstance(session, AgentSession):
        logger.debug(
            "Skipping agent status message creation for non-model session=%s event_type=%s",
            getattr(session, 'id', session),
            event_type,
        )
        return None
    logger.info(
        "Creating agent status message for session=%s event_type=%s",
        session.id,
        event_type,
    )
    return AgentMessage.objects.create(
        session=session,
        role='assistant',
        content=content,
        message_type=message_type,
        metadata={'event_type': event_type, **metadata},
    )


def _coerce_json(value):
    """Parse a JSON string if possible, otherwise return the original value."""
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value
