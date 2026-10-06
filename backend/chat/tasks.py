import logging
import asyncio
from functools import wraps
from datetime import timedelta
from typing import Any, Dict, Optional

import requests
from celery import shared_task
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from core.tenant_context import tenant_schema_context
from .models import ChatOutboxEvent, LinkPreview, Message, MessageStatus, ChatParticipant, ScheduledMessage, MessageAttachment
from .metrics import (
    chat_broadcast_publish_failures_total,
    chat_link_preview_create_races_total,
    chat_link_preview_fetch_outcomes_total,
    chat_link_preview_refusals_total,
)
from .realtime import broadcast_event_to_user_groups_sync
from .services import (
    ChatService,
    LinkPreviewFetchError,
    MessageService,
    OnlineStatusService,
    UnsafeUrlError,
    chat_group_name,
    claim_link_preview_fetch_slot,
    claim_recipients_for_delivery,
    fetch_url_safely,
    normalize_preview_url,
    parse_opengraph,
    release_unpublished_recipients,
)
from .url_helpers import build_messages_action_url

User = get_user_model()
logger = logging.getLogger(__name__)


def tenant_schema_task(task_function):
    """Select the task's explicit tenant for all ORM work and always restore it."""
    @wraps(task_function)
    def wrapped(*args, **kwargs):
        tenant_schema = kwargs.get('tenant_schema', 'public')
        with tenant_schema_context(tenant_schema):
            return task_function(*args, **kwargs)
    return wrapped


# How long each outcome is reused before the URL is fetched again. Separating them
# matters: a page that resolved fine is worth holding for a day, but a host that
# timed out may be back within the hour.
def _link_preview_ttl(status: str) -> timedelta:
    if status == LinkPreview.STATUS_FAILED:
        hours = getattr(settings, 'LINK_PREVIEW_FAILURE_TTL_HOURS', 1)
    elif status == LinkPreview.STATUS_BLOCKED:
        hours = getattr(settings, 'LINK_PREVIEW_BLOCKED_TTL_HOURS', 24)
    else:
        hours = getattr(settings, 'LINK_PREVIEW_SUCCESS_TTL_HOURS', 24)
    return timedelta(hours=hours)


def _link_preview_claim_ttl() -> timedelta:
    return timedelta(minutes=getattr(settings, 'LINK_PREVIEW_CLAIM_TTL_MINUTES', 5))

CHAT_OUTBOX_BATCH_SIZE = getattr(settings, 'CHAT_OUTBOX_BATCH_SIZE', 200)
CHAT_OUTBOX_CLAIM_TTL_SECONDS = getattr(settings, 'CHAT_OUTBOX_CLAIM_TTL_SECONDS', 300)


def _claim_pending_outbox_events():
    """Claim a bounded public-schema batch without holding locks during Redis I/O."""
    now = timezone.now()
    stale_before = now - timedelta(seconds=CHAT_OUTBOX_CLAIM_TTL_SECONDS)
    with tenant_schema_context('public'):
        with transaction.atomic():
            events = list(
                ChatOutboxEvent.objects
                .select_for_update(skip_locked=True)
                .filter(published_at__isnull=True, available_at__lte=now)
                .filter(Q(claimed_at__isnull=True) | Q(claimed_at__lt=stale_before))
                .order_by('created_at', 'id')[:CHAT_OUTBOX_BATCH_SIZE]
            )
            if events:
                ChatOutboxEvent.objects.filter(
                    id__in=[event.id for event in events]
                ).update(claimed_at=now)
    return events


def _publish_outbox_event(event: ChatOutboxEvent) -> None:
    task_kwargs = {'tenant_schema': event.tenant_schema}
    if event.event_type == ChatOutboxEvent.EVENT_MESSAGE_REALTIME:
        notify_new_message.apply_async(args=[event.aggregate_id], kwargs=task_kwargs)
        return
    if event.event_type == ChatOutboxEvent.EVENT_MESSAGE_NOTIFICATIONS:
        notify_message_recipients.apply_async(args=[event.aggregate_id], kwargs=task_kwargs)
        return
    raise ValueError(f'Unsupported chat outbox event type: {event.event_type}')


@shared_task
def dispatch_pending_chat_outbox() -> int:
    """Publish durable chat events to Celery and mark successful hand-offs."""
    events = _claim_pending_outbox_events()
    published_count = 0

    for index, event in enumerate(events):
        try:
            _publish_outbox_event(event)
        except Exception as exc:
            retry_delay = min(300, max(2, 2 ** min(event.attempt_count + 1, 8)))
            with tenant_schema_context('public'):
                ChatOutboxEvent.objects.filter(
                    id=event.id,
                    published_at__isnull=True,
                ).update(
                    claimed_at=None,
                    available_at=timezone.now() + timedelta(seconds=retry_delay),
                    attempt_count=F('attempt_count') + 1,
                    last_error=str(exc)[:2000],
                )
                # A broker failure generally affects the whole batch. Release
                # unattempted claims immediately and let the next sweep retry.
                remaining_ids = [item.id for item in events[index + 1:]]
                if remaining_ids:
                    ChatOutboxEvent.objects.filter(
                        id__in=remaining_ids,
                        published_at__isnull=True,
                    ).update(claimed_at=None)
            logger.exception('Chat outbox publish failed for event %s', event.id)
            break

        with tenant_schema_context('public'):
            ChatOutboxEvent.objects.filter(
                id=event.id,
                published_at__isnull=True,
            ).update(
                claimed_at=None,
                published_at=timezone.now(),
                attempt_count=F('attempt_count') + 1,
                last_error='',
            )
        published_count += 1

    if events:
        logger.info(
            'Chat outbox dispatch: claimed=%s published=%s',
            len(events),
            published_count,
        )
    return published_count


def _build_forwarded_from_payload(message: Message) -> Optional[Dict[str, Any]]:
    """Build structured forwarded source payload for realtime message events."""
    is_forwarded = bool(
        message.forwarded_from_message_id
        or message.forwarded_from_sender_display
        or message.forwarded_from_created_at
    )
    if not is_forwarded:
        return None

    return {
        'message_id': message.forwarded_from_message_id,
        'sender_display': message.forwarded_from_sender_display or '',
        'created_at': message.forwarded_from_created_at.isoformat() if message.forwarded_from_created_at else None,
    }


def _build_reply_to_payload(message: Message) -> Optional[Dict[str, Any]]:
    """Build structured reply_to payload for realtime message events."""
    if not message.reply_to_id or not message.reply_to:
        return None

    reply_msg = message.reply_to
    return {
        'id': reply_msg.id,
        'sender': {
            'id': reply_msg.sender.id,
            'username': reply_msg.sender.username,
            'email': reply_msg.sender.email,
        },
        'content': reply_msg.content,
        'created_at': reply_msg.created_at.isoformat() if reply_msg.created_at else None,
    }


def build_realtime_message_payload(message: Message) -> Dict[str, Any]:
    """Serialize message payload for websocket/celery delivery with attachments and forward metadata."""
    attachments = []
    for attachment in message.attachments.all():
        attachments.append({
            'id': attachment.id,
            'message': attachment.message_id,
            'file_type': attachment.file_type,
            'file_url': attachment.file.url if attachment.file else None,
            'thumbnail_url': attachment.thumbnail.url if attachment.thumbnail else None,
            'file_size': attachment.file_size,
            'file_size_display': attachment.file_size_display,
            'original_filename': attachment.original_filename,
            'mime_type': attachment.mime_type,
            'created_at': attachment.created_at.isoformat(),
        })

    forwarded_from = _build_forwarded_from_payload(message)
    reply_to = _build_reply_to_payload(message)
    return {
        'id': message.id,
        'seq': message.seq,
        'chat_id': message.chat.id,
        'sender': {
            'id': message.sender.id,
            'username': message.sender.username,
            'email': message.sender.email,
        },
        'content': message.content,
        'created_at': message.created_at.isoformat(),
        'updated_at': message.updated_at.isoformat(),
        'has_attachments': bool(message.has_attachments or attachments),
        'attachment_count': len(attachments),
        'attachments': attachments,
        'is_forwarded': forwarded_from is not None,
        'forwarded_from': forwarded_from,
        'reply_to': reply_to,
    }


def _publish_to_chat_group(channel_layer, chat_id, claimed_user_ids, event):
    """Publish one event to a chat's group, on behalf of the claimed recipients.

    Returns the same (delivered, failures) shape as the per-recipient
    broadcaster so callers do not branch on which one ran. A single publish has
    a single outcome, so either every claimed recipient counts as delivered or
    none of them do.
    """
    if not claimed_user_ids:
        return [], {}
    try:
        async_to_sync(channel_layer.group_send)(chat_group_name(chat_id), event)
    except Exception as exc:
        return [], {user_id: exc for user_id in claimed_user_ids}
    return list(claimed_user_ids), {}


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
@tenant_schema_task
def deliver_message_task(self, message_id: int, tenant_schema: str = 'public'):
    """
    Celery task to deliver a message to offline users.
    
    This task is triggered when a message is sent to offline users.
    It stores the message in a queue and attempts delivery when users come online.
    
    Args:
        message_id: ID of the message to deliver
    """
    try:
        message = Message.objects.select_related(
            'chat', 'sender', 'reply_to', 'reply_to__sender'
        ).prefetch_related('attachments').get(id=message_id)
        # Message creation commits only O(1) rows. The outbox guarantees this
        # idempotent recipient fan-out is eventually executed after commit.
        MessageService._create_recipient_statuses(
            message,
            message.sender,
            ignore_conflicts=True,
        )
        message_payload = build_realtime_message_payload(message)

        pending_statuses = list(MessageStatus.objects.filter(
            message=message,
            status='sent'
        ).values('id', 'user_id'))

        if not pending_statuses:
            logger.debug("No pending recipients for message %s", message_id)
            return

        pending_user_ids = [status['user_id'] for status in pending_statuses]
        online_user_ids = OnlineStatusService.get_online_users(pending_user_ids)
        claimed_user_ids = claim_recipients_for_delivery(message_id, online_user_ids)

        if not claimed_user_ids:
            logger.info(
                "deliver_message_task: message=%s pending=%s online=%s delivered=0",
                message_id,
                len(pending_statuses),
                len(online_user_ids),
            )
            return

        channel_layer = get_channel_layer()
        delivered_user_ids, publish_failures = broadcast_event_to_user_groups_sync(
            channel_layer,
            claimed_user_ids,
            {
                'type': 'chat_message',
                'message': message_payload,
            },
        )
        # Back to pending, for this task's own retry or the user's next reconnect.
        release_unpublished_recipients(message_id, publish_failures)

        logger.info(
            "deliver_message_task: message=%s pending=%s online=%s delivered=%s publish_failures=%s",
            message_id,
            len(pending_statuses),
            len(online_user_ids),
            len(delivered_user_ids),
            len(publish_failures),
        )

        # Remaining offline users are recovered from durable MessageStatus rows
        # when they reconnect. Retry only transient WebSocket publication errors.
        if publish_failures:
            raise RuntimeError(
                f"WebSocket publish failed for {len(publish_failures)} recipient(s)"
            )
        
    except Message.DoesNotExist:
        logger.error(f"Message {message_id} not found")
    except Exception as e:
        logger.exception("Error delivering message %s", message_id)
        raise self.retry(exc=e)


async def _broadcast_presence_to_recipients(channel_layer, recipient_ids, event):
    await asyncio.gather(*(
        channel_layer.group_send(f'chat_user_{recipient_id}', event)
        for recipient_id in recipient_ids
    ))


def get_offline_broadcast_params(user_id: int, offline_token: str):
    """Finalize offline presence in cache/DB; return (version, recipient_ids) for broadcast.

    Separated from finalize_presence_offline_now so that the consumer's
    async disconnect() handler can call this via sync_to_async and then
    broadcast directly from its own async context, avoiding the nested
    sync_to_async → async_to_sync deadlock that occurs when
    InMemoryChannelLayer asyncio.Queue objects are accessed from a thread
    that is itself running inside the test event loop.

    Returns (None, []) when the user reconnected before this ran.
    """
    version = OnlineStatusService.finalize_offline_if_still_disconnected(user_id, offline_token)
    if version is None:
        return None, []
    recipient_ids = ChatService.get_presence_recipient_ids(user_id)
    recipient_ids = OnlineStatusService.get_online_users(recipient_ids)
    return version, recipient_ids


def finalize_presence_offline_now(user_id: int, offline_token: str) -> bool:
    """Finalize delayed offline presence once and broadcast if state changed.

    Safe to call from regular sync threads (e.g. Celery workers) where there
    is no outer asyncio event loop.  Do NOT call this from inside sync_to_async
    — use get_offline_broadcast_params + consumer.broadcast_presence_update
    instead to avoid nested async_to_sync deadlocks.
    """
    version, recipient_ids = get_offline_broadcast_params(user_id, offline_token)
    if version is None or not recipient_ids:
        return False

    channel_layer = get_channel_layer()
    event = {
        'type': 'presence_update',
        'user_id': user_id,
        'is_online': False,
        'version': version,
        'timestamp': timezone.now().isoformat(),
    }
    async_to_sync(_broadcast_presence_to_recipients)(channel_layer, recipient_ids, event)
    return True


@shared_task(bind=True, max_retries=2, default_retry_delay=5)
@tenant_schema_task
def finalize_presence_offline(
    self,
    user_id: int,
    offline_token: str,
    tenant_schema: str = 'public',
):
    """Finalize delayed offline presence and notify online shared-chat users."""
    try:
        finalize_presence_offline_now(user_id, offline_token)
    except Exception as e:
        logger.error(f"Error finalizing offline presence for user {user_id}: {e}")
        raise self.retry(exc=e)


@shared_task
def cleanup_old_online_status():
    """
    Celery periodic task to clean up stale online status entries.
    
    This is a fallback to ensure Redis doesn't accumulate stale entries.
    Should be run periodically (e.g., every hour).
    """
    try:
        # Get all keys matching the pattern
        pattern = f'{OnlineStatusService.ONLINE_KEY_PREFIX}:*'
        keys = cache.keys(pattern) if hasattr(cache, 'keys') else []
        
        cleaned = 0
        for key in keys:
            # Redis TTL will handle expiration, but we can force cleanup here if needed
            if not cache.get(key):
                cache.delete(key)
                cleaned += 1
        
        logger.info(f"Cleaned up {cleaned} stale online status entries")
        
    except Exception as e:
        logger.error(f"Error cleaning up online status: {e}")


# How long an unlinked (message=None) attachment may live before it is treated
# as abandoned and swept. Must stay comfortably LONGER than the client outbox
# retention window: a message can sit in the offline outbox for a long time
# before a successful send links its attachments, and we must never delete an
# attachment for a message the user is still going to send. Overridable via
# settings for environments that keep the outbox around longer.
ORPHAN_ATTACHMENT_TTL_HOURS = getattr(settings, 'CHAT_ORPHAN_ATTACHMENT_TTL_HOURS', 48)


@shared_task
def cleanup_orphaned_attachments() -> int:
    """Delete abandoned attachment uploads that were never linked to a message.

    Attachments use a two-phase upload: the file is uploaded first (row created
    with message=None) and linked to a Message only when the user actually sends
    it. If the send never happens — cancelled draft, failed validation, spam
    rejection, abandoned outbox entry — the row stays orphaned with message=None
    and its file lingers in storage forever. This periodic sweep removes those
    orphans once they are older than ORPHAN_ATTACHMENT_TTL_HOURS.

    Returns the number of attachments deleted (used by tests and logging).
    """
    cutoff = timezone.now() - timedelta(hours=ORPHAN_ATTACHMENT_TTL_HOURS)
    orphans = MessageAttachment.objects.filter(
        message__isnull=True,
        created_at__lt=cutoff,
    )

    deleted = 0
    # .iterator() streams rows so a large backlog is not all loaded at once.
    for attachment in orphans.iterator():
        try:
            # Delete the stored file FIRST, then the DB row. In this order a
            # mid-way failure leaves a still-tracked row we retry next run,
            # never a DB-less file we can no longer locate.
            if attachment.file:
                attachment.file.delete(save=False)
            attachment.delete()
            deleted += 1
        except Exception as e:
            # Isolate per-item failures so one bad file cannot abort the sweep.
            logger.error("Failed to delete orphaned attachment %s: %s", attachment.id, e)

    logger.info("cleanup_orphaned_attachments: deleted %s orphaned attachment(s)", deleted)
    return deleted


@shared_task
@tenant_schema_task
def send_typing_indicator(
    chat_id: int,
    user_id: int,
    is_typing: bool,
    tenant_schema: str = 'public',
):
    """
    Celery task to broadcast typing indicator to chat participants.
    
    Args:
        chat_id: ID of the chat
        user_id: ID of the user typing
        is_typing: True if user is typing, False if stopped
    """
    try:
        channel_layer = get_channel_layer()
        
        # Get all active participants except the typer
        participant_ids = list(ChatParticipant.objects.filter(
            chat_id=chat_id,
            is_active=True
        ).exclude(user_id=user_id).values_list('user_id', flat=True))

        succeeded, failed = broadcast_event_to_user_groups_sync(
            channel_layer,
            participant_ids,
            {
                'type': 'typing_indicator',
                'chat_id': chat_id,
                'user_id': user_id,
                'is_typing': is_typing,
            },
        )

        logger.debug(
            "send_typing_indicator: chat=%s user=%s recipients=%s sent=%s failed=%s",
            chat_id,
            user_id,
            len(participant_ids),
            len(succeeded),
            len(failed),
        )
        
    except Exception as e:
        logger.error(f"Error sending typing indicator: {e}")


@shared_task(bind=True, max_retries=3)
@tenant_schema_task
def update_message_status_task(
    self,
    message_id: int,
    user_id: int,
    status: str,
    tenant_schema: str = 'public',
):
    """
    Celery task to update message status and notify sender.
    
    Args:
        message_id: ID of the message
        user_id: ID of the user whose status changed
        status: New status ('delivered' or 'read')
    """
    try:
        message = Message.objects.select_related('sender').get(id=message_id)
        msg_status = MessageStatus.objects.get(message=message, user_id=user_id)
        
        # Update status
        if status == 'delivered':
            msg_status.mark_as_delivered()
        elif status == 'read':
            msg_status.mark_as_read()
        else:
            logger.warning(f"Invalid status '{status}' for message {message_id}")
            return
        
        # Notify sender via WebSocket
        channel_layer = get_channel_layer()
        async_to_sync(channel_layer.group_send)(
            f'chat_user_{message.sender.id}',
            {
                'type': 'message_status_update',
                'message_id': message_id,
                'user_id': user_id,
                'status': status,
            }
        )
        
        logger.info(f"Updated message {message_id} status to '{status}' for user {user_id}")
        
    except (Message.DoesNotExist, MessageStatus.DoesNotExist) as e:
        logger.error(f"Message or status not found: {e}")
    except Exception as e:
        logger.error(f"Error updating message status: {e}")
        raise self.retry(exc=e)


@shared_task(bind=True, max_retries=3, default_retry_delay=5)
@tenant_schema_task
def notify_new_message(
    self,
    message_id: int,
    tenant_schema: str = 'public',
):
    """
    Celery task to notify all chat participants of a new message.

    This is called after a message is created to push notifications
    to all participants (both online and offline).

    Args:
        message_id: ID of the newly created message
    """
    try:
        message = Message.objects.select_related(
            'chat', 'sender', 'reply_to', 'reply_to__sender'
        ).prefetch_related('attachments').get(id=message_id)
        MessageService._create_recipient_statuses(
            message,
            message.sender,
            ignore_conflicts=True,
        )
        message_payload = build_realtime_message_payload(message)

        participant_ids = list(ChatParticipant.objects.filter(
            chat=message.chat,
            is_active=True
        ).exclude(user=message.sender).values_list('user_id', flat=True))

        channel_layer = get_channel_layer()
        online_user_ids = OnlineStatusService.get_online_users(participant_ids)

        claimed_user_ids = claim_recipients_for_delivery(message_id, online_user_ids)
        event = {
            'type': 'chat_message',
            'message': message_payload,
            # Chat groups also contain the sender's sockets. Preserve the REST
            # contract (the sender already has the created response) without
            # falling back to O(participants) Redis publishes.
            'exclude_user_id': message.sender_id,
        }

        if getattr(settings, 'CHAT_CHANNEL_GROUPS_ENABLED', False):
            # One publish for the whole chat instead of one per recipient.
            # The group carries every connection entitled to this chat, so
            # there is no per-recipient result to inspect: the publish either
            # happened for all of them or for none.
            delivered_user_ids, publish_failures = _publish_to_chat_group(
                channel_layer, message.chat_id, claimed_user_ids, event
            )
        else:
            delivered_user_ids, publish_failures = broadcast_event_to_user_groups_sync(
                channel_layer,
                claimed_user_ids,
                event,
            )
        # The delivery task scheduled below picks these up as ordinary pending
        # recipients.
        release_unpublished_recipients(message_id, publish_failures)

        delivered_user_id_set = set(delivered_user_ids)
        pending_user_ids = [
            user_id for user_id in participant_ids
            if user_id not in delivered_user_id_set
        ]

        if pending_user_ids:
            deliver_message_task.apply_async(
                args=[message_id],
                kwargs={'tenant_schema': tenant_schema},
                countdown=5,
            )

        logger.info(
            "notify_new_message: message=%s chat=%s recipients=%s online=%s "
            "delivered=%s pending=%s publish_failures=%s",
            message_id,
            message.chat_id,
            len(participant_ids),
            len(online_user_ids),
            len(delivered_user_ids),
            len(pending_user_ids),
            len(publish_failures),
        )

    except Message.DoesNotExist:
        logger.warning("notify_new_message: message=%s not found", message_id)
    except Exception as e:
        logger.exception("notify_new_message failed for message %s", message_id)
        raise self.retry(exc=e)


def _notification_exists_for_message(*, recipient_id: int, event_type: str, message_id: int) -> bool:
    """Best-effort idempotency guard for retryable per-message notification tasks."""
    from notifications.models import Notification

    return Notification.objects.filter(
        recipient_id=recipient_id,
        event_type=event_type,
        metadata__message_id=message_id,
    ).exists()


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
@tenant_schema_task
def notify_message_recipients(
    self,
    message_id: int,
    tenant_schema: str = 'public',
):
    """
    Create persisted in-app notifications for a newly-created chat message.

    WebSocket fanout stays in ``notify_new_message``; this task owns Activity /
    notification rows so the message create request does not loop over all
    recipients synchronously.
    """
    try:
        from notifications.services import create_notification, create_or_update_chat_notification
        from notifications.models import NotificationCategory, NotificationEventType

        message = (
            Message.objects
            .select_related('chat', 'chat__project', 'sender', 'parent_message', 'parent_message__sender')
            .prefetch_related('mentions__mentioned_user')
            .get(id=message_id)
        )
        if message.is_deleted or message.is_revoked:
            return

        recipients = list(
            ChatParticipant.objects.filter(chat=message.chat, is_active=True)
            .exclude(user=message.sender)
            .select_related('user')
        )
        participant_by_user_id = {participant.user_id: participant for participant in recipients}
        active_recipient_ids = set(participant_by_user_id)
        actor_name = message.sender.username or message.sender.email or ""

        for recipient in recipients:
            if recipient.is_currently_muted():
                continue
            if recipient.notification_level != 'all':
                continue
            create_or_update_chat_notification(
                recipient_id=recipient.user_id,
                actor_id=message.sender_id,
                chat_id=message.chat_id,
                message_id=message.id,
                project_id=message.chat.project_id,
                message_preview=message.content or "",
                actor_name=actor_name,
            )

        for mention in message.mentions.select_related('mentioned_user').all():
            if mention.mentioned_user_id == message.sender_id:
                continue
            participant = participant_by_user_id.get(mention.mentioned_user_id)
            if participant is None:
                continue
            if participant.is_currently_muted():
                continue
            if participant.notification_level == 'none':
                continue
            if _notification_exists_for_message(
                recipient_id=mention.mentioned_user_id,
                event_type=NotificationEventType.CHAT_MENTION,
                message_id=message.id,
            ):
                continue
            create_notification(
                recipient_id=mention.mentioned_user_id,
                actor_id=message.sender_id,
                category=NotificationCategory.COLLABORATION,
                event_type=NotificationEventType.CHAT_MENTION,
                title=f"{actor_name} mentioned you",
                body=message.content[:200] or "",
                related_object_type="chat",
                related_object_id=message.chat_id,
                action_url=build_messages_action_url(
                    message.chat.slug,
                    message_id=message.id,
                ),
                metadata={
                    "chat_id": message.chat_id,
                    "chat_slug": message.chat.slug,
                    "message_id": message.id,
                    "project_id": message.chat.project_id,
                    "message_preview": message.content[:200] or "",
                },
            )

        if not message.parent_message_id:
            return

        root = message.parent_message
        if not root or root.chat_id != message.chat_id:
            return

        thread_suppressed_user_ids = {
            participant.user_id
            for participant in ChatParticipant.objects.filter(
                chat=message.chat,
                is_active=True,
            )
            if (
                participant.notification_level != 'all'
                or participant.is_currently_muted()
            )
        }

        notified_ids = {message.sender_id}
        candidate_ids = []
        if root.sender_id != message.sender_id:
            candidate_ids.append(root.sender_id)

        previous_sender_ids = (
            root.thread_replies
            .filter(chat=root.chat)
            .exclude(sender_id__in=notified_ids)
            .order_by('sender_id')
            .values_list('sender_id', flat=True)
            .distinct()
        )
        candidate_ids.extend(previous_sender_ids)

        for recipient_id in candidate_ids:
            if recipient_id not in active_recipient_ids:
                continue
            if recipient_id in notified_ids or recipient_id in thread_suppressed_user_ids:
                continue
            notified_ids.add(recipient_id)
            if _notification_exists_for_message(
                recipient_id=recipient_id,
                event_type=NotificationEventType.CHAT_THREAD_REPLY,
                message_id=message.id,
            ):
                continue
            create_notification(
                recipient_id=recipient_id,
                actor_id=message.sender_id,
                category=NotificationCategory.COLLABORATION,
                event_type=NotificationEventType.CHAT_THREAD_REPLY,
                title=f"{actor_name} replied in a thread",
                body=message.content[:200] or "",
                related_object_type="chat",
                related_object_id=message.chat_id,
                action_url=build_messages_action_url(
                    message.chat.slug,
                    parent_message_id=root.id,
                    thread_message_id=message.id,
                ),
                metadata={
                    "chat_id": message.chat_id,
                    "chat_slug": message.chat.slug,
                    "root_message_id": root.id,
                    "message_id": message.id,
                    "project_id": message.chat.project_id,
                },
            )
    except Message.DoesNotExist:
        logger.error("notify_message_recipients: message %s not found", message_id)
    except Exception as exc:
        logger.exception("notify_message_recipients failed for message %s: %s", message_id, exc)
        raise self.retry(exc=exc)


@shared_task
@tenant_schema_task
def notify_pin_update(
    chat_id: int,
    action: str,
    message_id: int,
    pin_data=None,
    tenant_schema: str = 'public',
    actor_user_id: int = None,
):
    """Broadcast a shared pin change to a channel's other active members.

    Args:
        chat_id: ID of the channel whose pins changed
        action: 'pinned' or 'unpinned'
        message_id: ID of the message that was pinned or unpinned
        pin_data: serialised pin row for 'pinned'; None for 'unpinned'
        actor_user_id: the member who made the change, excluded from the
            broadcast because their own client already applied it. Optional so
            tasks queued by an older release still run after a deploy.

    The payload is serialised by the request so recipients keep the absolute
    avatar URLs the request context produces.
    """
    try:
        recipients = ChatParticipant.objects.filter(
            chat_id=chat_id,
            is_active=True,
        )
        if actor_user_id is not None:
            # Their client acknowledged the change on the HTTP response; sending
            # it back marks the channel as having an unseen pin they created
            # themselves.
            recipients = recipients.exclude(user_id=actor_user_id)
        participant_ids = list(recipients.values_list('user_id', flat=True))

        channel_layer = get_channel_layer()
        if not channel_layer:
            return

        succeeded, failed = broadcast_event_to_user_groups_sync(
            channel_layer,
            participant_ids,
            {
                'type': 'pin_update',
                'action': action,
                'chat_id': chat_id,
                'message_id': message_id,
                'pin': pin_data,
            },
        )

        if failed:
            # The request already returned 200, so nothing else records that
            # these members never got the update.
            chat_broadcast_publish_failures_total.labels(event='pin').inc(len(failed))

        logger.info(
            "notify_pin_update: chat=%s action=%s message=%s recipients=%s sent=%s failed=%s",
            chat_id,
            action,
            message_id,
            len(participant_ids),
            len(succeeded),
            len(failed),
        )

    except Exception:
        # A broadcast failure must never undo a pin that is already persisted.
        # Counted too: a channel layer that is down fails every pin this way,
        # and a log line nobody greps is not a signal.
        chat_broadcast_publish_failures_total.labels(event='pin').inc()
        logger.exception(
            "notify_pin_update failed for chat %s message %s", chat_id, message_id
        )


@shared_task
@tenant_schema_task
def notify_reaction_update(
    message_id: int,
    user_id: int,
    emoji: str,
    action: str,
    tenant_schema: str = 'public',
):
    """
    Celery task to notify all chat participants of a reaction update.

    Args:
        message_id: ID of the message that was reacted to
        user_id: ID of the user who added/removed the reaction
        emoji: The emoji that was added/removed
        action: 'added' or 'removed'
    """
    try:
        message = Message.objects.select_related('chat', 'sender').get(id=message_id)
        user = User.objects.get(id=user_id)

        # Build reaction update payload
        reaction_payload = {
            'message_id': message_id,
            'chat_id': message.chat.id,
            'user': {
                'id': user.id,
                'username': user.username,
            },
            'emoji': emoji,
            'action': action,
        }

        # Get all active participants
        participant_ids = list(ChatParticipant.objects.filter(
            chat=message.chat,
            is_active=True
        ).values_list('user_id', flat=True))

        channel_layer = get_channel_layer()
        online_user_ids = OnlineStatusService.get_online_users(participant_ids)
        succeeded, failed = broadcast_event_to_user_groups_sync(
            channel_layer,
            online_user_ids,
            {
                'type': 'reaction_update',
                'reaction': reaction_payload,
            },
        )

        logger.info(
            "notify_reaction_update: message=%s actor=%s action=%s recipients=%s "
            "online=%s sent=%s failed=%s",
            message_id,
            user_id,
            action,
            len(participant_ids),
            len(online_user_ids),
            len(succeeded),
            len(failed),
        )

        # Persist an in-app notification for the message author when someone
        # adds (not removes) a reaction — skip self-reactions.
        if action == 'added' and message.sender_id != user_id:
            try:
                from notifications.services import create_notification
                from notifications.models import NotificationCategory, NotificationEventType
                sender_participant = ChatParticipant.objects.filter(
                    chat=message.chat,
                    user_id=message.sender_id,
                    is_active=True,
                ).first()
                if sender_participant and sender_participant.is_currently_muted():
                    return
                create_notification(
                    recipient_id=message.sender_id,
                    actor_id=user_id,
                    category=NotificationCategory.COLLABORATION,
                    event_type=NotificationEventType.CHAT_REACTION,
                    title=f"{user.username or user.email} reacted {emoji} to your message",
                    body=message.content[:200] or "[Attachment]",
                    related_object_type="chat",
                    related_object_id=message.chat_id,
                    action_url=build_messages_action_url(
                        message.chat.slug,
                        message_id=message_id,
                    ),
                    metadata={
                        "chat_id": message.chat_id,
                        "chat_slug": message.chat.slug,
                        "message_id": message_id,
                        "project_id": message.chat.project_id,
                        "emoji": emoji,
                        "message_preview": message.content[:200] or "[Attachment]",
                    },
                )
            except Exception as e:
                logger.error(f"Failed to create reaction notification for message {message_id}: {e}")

    except Message.DoesNotExist:
        logger.error(f"Message {message_id} not found for reaction update")
    except User.DoesNotExist:
        logger.error(f"User {user_id} not found for reaction update")
    except Exception as e:
        logger.error(f"Error notifying reaction update for message {message_id}: {e}")


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
@tenant_schema_task
def send_scheduled_message(
    self,
    scheduled_message_id: int,
    tenant_schema: str = 'public',
):
    """
    Celery task that fires at the scheduled time and creates the actual Message.

    Flow:
      1. Load ScheduledMessage; skip if not pending (already sent/cancelled).
      2. Mark status=sending.
      3. Create the Message (content, rich_body, attachments, mentions, reply_to).
      4. Mark status=sent, store sent_message FK.
      5. Dispatch notify_new_message so online users receive it via WebSocket.
    """
    from django.contrib.auth import get_user_model as _get_user_model
    from .models import Message, MessageMention, MessageAttachment, ScheduledMessage

    _User = _get_user_model()
    sm = None
    try:
        sm = (
            ScheduledMessage.objects
            .select_related('chat', 'sender', 'reply_to')
            .get(id=scheduled_message_id)
        )
    except ScheduledMessage.DoesNotExist:
        logger.error(f"send_scheduled_message: ScheduledMessage {scheduled_message_id} not found")
        return

    if sm.status != ScheduledMessage.STATUS_PENDING:
        logger.info(f"send_scheduled_message {scheduled_message_id}: status={sm.status}, skipping")
        return

    # Mark as sending so concurrent retries skip
    sm.status = ScheduledMessage.STATUS_SENDING
    sm.save(update_fields=['status', 'updated_at'])

    try:
        with transaction.atomic():
            # Create the message
            message = Message.objects.create(
                chat=sm.chat,
                sender=sm.sender,
                content=sm.content,
                rich_body=sm.rich_body,
                reply_to_id=sm.reply_to_id,
            )

            # Link only still-unlinked attachments uploaded by the scheduling user.
            attachment_ids = list(dict.fromkeys(sm.attachment_ids or []))
            if attachment_ids:
                attachments = MessageAttachment.objects.select_for_update().filter(
                    id__in=attachment_ids,
                    uploader=sm.sender,
                    message__isnull=True,
                )
                if attachments.count() != len(attachment_ids):
                    raise ValueError("Scheduled message attachments are no longer available.")
                attachments.update(message=message)
                message.has_attachments = True
                message.save(update_fields=['has_attachments'])

            # Create mention records
            mention_ids = sm.mention_ids or []
            for uid in mention_ids:
                try:
                    MessageMention.objects.get_or_create(message=message, mentioned_user_id=uid)
                except Exception:
                    pass

            MessageService._create_recipient_statuses(message, sm.sender)
            MessageService._schedule_new_message_side_effects(message, sm.sender)

        # Finalize
        sm.status = ScheduledMessage.STATUS_SENT
        sm.sent_message = message
        sm.save(update_fields=['status', 'sent_message', 'updated_at'])

        logger.info(
            f"send_scheduled_message {scheduled_message_id}: created message {message.id} in chat {sm.chat_id}"
        )

    except Exception as exc:
        logger.error(f"send_scheduled_message {scheduled_message_id} failed: {exc}")
        if sm is not None:
            try:
                sm.status = ScheduledMessage.STATUS_FAILED
                sm.error_message = str(exc)
                sm.save(update_fields=['status', 'error_message', 'updated_at'])
            except Exception:
                pass
        if isinstance(exc, ValueError):
            return
        raise self.retry(exc=exc)


@shared_task
@tenant_schema_task
def fetch_link_preview_task(message_id: int, url: str, tenant_schema: str = 'public'):
    """Fetch a URL's OpenGraph metadata and cache it (MED-279).

    Runs off the hot path: the message is already sent and shown by the time this
    starts. Every outcome — success, upstream failure, or a URL the safety guard
    refuses — is cached for 24h, so a link costs one fetch a day however often it
    is posted, and a dead or unsafe one costs no more than a good one.

    Concurrency is handled by the unique URL key: the first task to create the
    row fetches, everyone else reuses the answer.

    Retries are deliberately disabled: a link preview is cosmetic, and retrying a
    fetch that a user can trigger at will would turn this into an amplifier.
    """
    cache_key = normalize_preview_url(url)
    if not cache_key:
        return

    message = Message.objects.filter(id=message_id, is_deleted=False).first()
    if message is None:
        return  # message deleted mid-flight; nothing left to decorate

    # The unique URL key is what makes this single-flight: when several people
    # post the same new link at once, exactly one task creates the row and
    # fetches; the rest find it and stop.
    preview, created = LinkPreview.objects.get_or_create(
        url=cache_key,
        defaults={'status': LinkPreview.STATUS_PENDING},
    )
    if not created:
        chat_link_preview_create_races_total.inc()

    if not created and not _preview_needs_refetch(preview):
        # Someone already answered this URL recently, or is answering it now.
        # Reusing the answer still means telling *this* message about it.
        if preview.status == LinkPreview.STATUS_READY and (preview.title or preview.image_url):
            _broadcast_link_preview(message, preview)
        return

    # Metered before the row is claimed, not after: claiming flips a stale but
    # still-usable READY row to PENDING, and a rate-limited sender bailing out
    # after that would hide a good card from everyone until the claim expired.
    if not claim_link_preview_fetch_slot(message.sender_id):
        chat_link_preview_refusals_total.labels(reason='rate_limit').inc()
        logger.info('Link preview rate limit reached for user %s', message.sender_id)
        # A brand-new row was never attempted, so it must not look like a cached
        # failure to the next person; an existing row keeps whatever it had.
        if created:
            LinkPreview.objects.filter(pk=preview.pk, status=LinkPreview.STATUS_PENDING).delete()
        elif preview.status == LinkPreview.STATUS_READY and (preview.title or preview.image_url):
            _broadcast_link_preview(message, preview)
        return

    if not created:
        _claim_stale_preview(preview)

    try:
        html = fetch_url_safely(cache_key)
    except UnsafeUrlError:
        chat_link_preview_refusals_total.labels(reason='guard').inc()
        chat_link_preview_fetch_outcomes_total.labels(outcome='blocked').inc()
        logger.warning('Link preview refused by URL guard: %s', cache_key)
        _store_preview_outcome(preview, LinkPreview.STATUS_BLOCKED)
        return
    except (LinkPreviewFetchError, requests.RequestException) as exc:
        chat_link_preview_fetch_outcomes_total.labels(outcome='failed').inc()
        logger.info('Link preview fetch failed for %s: %s', cache_key, exc)
        _store_preview_outcome(preview, LinkPreview.STATUS_FAILED)
        return

    metadata = parse_opengraph(html)
    _store_preview_outcome(preview, LinkPreview.STATUS_READY, **metadata)

    if metadata['title'] or metadata['image_url']:
        chat_link_preview_fetch_outcomes_total.labels(outcome='ready').inc()
        _broadcast_link_preview(message, preview)
    else:
        chat_link_preview_fetch_outcomes_total.labels(outcome='empty').inc()


def _preview_needs_refetch(preview: LinkPreview) -> bool:
    """True when an existing row is stale enough to fetch again. Reads only.

    Kept separate from the claim so callers can decide *whether* to fetch before
    doing anything destructive: claiming flips the row to pending, which hides a
    still-usable card from every viewer until the claim expires.

    A row is left alone while it is fresh — and that covers failures and refusals
    too, so a dead or unsafe link costs one fetch per window rather than one per
    mention. A row still marked pending belongs to another task, unless that task
    has been gone long enough to count as dead.
    """
    now = timezone.now()

    if preview.status == LinkPreview.STATUS_PENDING:
        return now - preview.updated_at >= _link_preview_claim_ttl()
    if preview.fetched_at is None:
        return True
    return now - preview.fetched_at >= _link_preview_ttl(preview.status)


def _claim_stale_preview(preview: LinkPreview) -> None:
    """Take ownership of a row we have already decided to refetch.

    Re-stamps updated_at so a concurrent task sees the claim as live.
    """
    now = timezone.now()
    LinkPreview.objects.filter(pk=preview.pk).update(
        status=LinkPreview.STATUS_PENDING, updated_at=now
    )
    preview.status = LinkPreview.STATUS_PENDING
    preview.updated_at = now


def _broadcast_link_preview(message: Message, preview: LinkPreview) -> None:
    """Push a finished preview to everyone currently watching the conversation.

    Clients that are offline are unaffected: they pick the same preview up from
    the cache when the conversation loads. The row is already committed, so a
    channel-layer outage must never undo it — hence the broad except.
    """
    try:
        channel_layer = get_channel_layer()
        if not channel_layer:
            return

        participant_ids = list(
            ChatParticipant.objects
            .filter(chat_id=message.chat_id, is_active=True)
            .values_list('user_id', flat=True)
        )
        if not participant_ids:
            return

        succeeded, failed = broadcast_event_to_user_groups_sync(
            channel_layer,
            participant_ids,
            {
                'type': 'link_preview',
                'chat_id': message.chat_id,
                'message_id': message.id,
                'preview': {
                    'url': preview.url,
                    'title': preview.title,
                    'description': preview.description,
                    'image_url': preview.image_url,
                },
            },
        )
        if failed:
            chat_broadcast_publish_failures_total.labels(event='link_preview').inc(len(failed))
        logger.info(
            'link preview broadcast: message=%s recipients=%s sent=%s failed=%s',
            message.id, len(participant_ids), len(succeeded), len(failed),
        )
    except Exception:
        chat_broadcast_publish_failures_total.labels(event='link_preview').inc()
        logger.exception('Failed to broadcast link preview for message %s', message.id)


def _store_preview_outcome(
    preview: LinkPreview,
    status: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    image_url: Optional[str] = None,
) -> None:
    """Record the result of one fetch attempt, successful or not."""
    preview.status = status
    preview.title = title
    preview.description = description
    preview.image_url = image_url
    preview.fetched_at = timezone.now()
    preview.save(update_fields=['status', 'title', 'description', 'image_url', 'fetched_at', 'updated_at'])


@shared_task
@tenant_schema_task
def prune_link_previews(tenant_schema: str = 'public') -> int:
    """Delete cache rows nobody has needed for a long time.

    The table is append-only otherwise: every URL anyone ever posted keeps a row,
    including one-off failures. Rows are only ever read through by URL, so deleting
    an old one costs at most one re-fetch if that link resurfaces.
    """
    cutoff = timezone.now() - timedelta(
        days=getattr(settings, 'LINK_PREVIEW_PRUNE_AFTER_DAYS', 30)
    )
    deleted, _ = LinkPreview.objects.filter(fetched_at__lt=cutoff).delete()
    if deleted:
        logger.info('Pruned %s link preview rows older than %s', deleted, cutoff)
    return deleted
