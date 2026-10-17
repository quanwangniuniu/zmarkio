"""Forwarding agent output to project members through the Agent Bot."""
import logging

logger = logging.getLogger(__name__)


def _get_or_create_bot_private_chat(bot, target_user, project):
    """Find or create a private chat with exactly 2 participants: bot and target.

    Unlike ChatService.create_private_chat, this enforces participant_count==2
    so it won't accidentally match a group-like chat where bot was added as a
    third participant (e.g. via @Agent lazy-join).
    """
    from chat.models import Chat, ChatType, ChatParticipant

    # First, find chats that contain both bot and target_user
    chat = (
        Chat.objects.filter(
            project=project,
            type=ChatType.PRIVATE,
            participants__user=bot,
        )
        .filter(participants__user=target_user)
        .distinct()
        .first()
    )

    # Second, verify it has exactly 2 participants (not a group chat)
    if chat:
        participant_count = chat.participants.count()
        if participant_count != 2:
            # Not exactly 2 participants, might be a group chat
            chat = None

    # If found, reactivate any inactive participants
    if chat:
        participants = ChatParticipant.objects.filter(chat=chat, user__in=[bot, target_user])
        for participant in participants:
            if not participant.is_active:
                participant.is_active = True
                participant.save(update_fields=['is_active', 'updated_at'])
        return chat, False

    # Not found, create new chat
    chat = Chat.objects.create(project=project, type=ChatType.PRIVATE)
    ChatParticipant.objects.create(chat=chat, user=bot, is_active=True)
    ChatParticipant.objects.create(chat=chat, user=target_user, is_active=True)
    return chat, True


def _forward_to_users(forwards, sender, project):
    """Send messages to users based on Dify forwards structure.

    Uses the Agent Bot system user as the chat sender so that
    the private chat always involves two distinct users — avoiding the
    sender==target bug when forwarding to oneself.
    """
    from chat.services import MessageService
    from core.models import ProjectMember
    from core.utils.bot_user import get_agent_bot_user

    bot = get_agent_bot_user()
    sender_name = sender.get_full_name() or sender.username or sender.email

    results = []
    for item in forwards:
        username = (item.get('username') or '').strip()
        content = (item.get('content') or '').strip()
        if not username or not content:
            continue

        prefixed_content = f"from {sender_name} by agent:\n{content}"

        members = (
            ProjectMember.objects.filter(project=project, is_active=True)
            .exclude(user=bot)
            .filter(user__username__iexact=username)
            .select_related('user')
        )
        if not members.exists():
            members = (
                ProjectMember.objects.filter(project=project, is_active=True)
                .exclude(user=bot)
                .filter(user__email__iexact=username)
                .select_related('user')
            )

        if not members.exists():
            logger.warning(f"Forward target '{username}' not found in project {project.id}")
            results.append({"username": username, "status": "not_found"})
            continue

        if members.count() > 1:
            logger.warning(f"Forward target '{username}' is ambiguous in project {project.id}")
            results.append({"username": username, "status": "ambiguous"})
            continue

        target_user = members.first().user
        try:
            chat, _ = _get_or_create_bot_private_chat(bot, target_user, project)
            message = MessageService.create_message(chat=chat, sender=bot, content=prefixed_content)
            logger.info(
                "Agent forwarded message for project=%s sender=%s target_user=%s username=%s chat=%s message=%s",
                project.id,
                sender.id,
                target_user.id,
                username,
                chat.id,
                message.id,
            )
            results.append({"username": username, "status": "sent", "user_id": target_user.id})
        except Exception as e:
            logger.error(f"Failed to forward to {username}: {e}")
            results.append({"username": username, "status": "error", "detail": str(e)})

    return results
