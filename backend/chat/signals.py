"""
chat/signals.py
───────────────
Keeps Message.search_vector in sync whenever a message is saved.

The vector weights:
  A (highest) — message content (plain text)
  B           — sender username / email

NOTE: We use Value(instance.sender.username) instead of the relation
path 'sender__username' because QuerySet.update() cannot traverse
ForeignKey joins; passing a literal Value avoids the restriction.
"""

from django.db.models import Value
from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver
from django.contrib.postgres.search import SearchVector


@receiver(pre_save, sender='chat.ChatParticipant')
def remember_chat_participant_membership(sender, instance, update_fields=None, **kwargs):
    """Remember persisted membership state for the post-save receiver."""
    if not instance.pk:
        instance._previous_is_active = None
        return
    if update_fields is not None and 'is_active' not in update_fields:
        return
    instance._previous_is_active = (
        sender.objects.filter(pk=instance.pk)
        .values_list('is_active', flat=True)
        .first()
    )


@receiver(post_save, sender='chat.ChatParticipant')
def invalidate_visibility_cache_on_participant_save(
    sender, instance, created, update_fields=None, **kwargs
):
    """Invalidate cached and live visibility when membership changes."""
    if not created:
        if update_fields is not None and 'is_active' not in update_fields:
            return
        if getattr(instance, '_previous_is_active', instance.is_active) == instance.is_active:
            return

    from .services import ChatService

    chat_model = sender._meta.get_field('chat').remote_field.model
    ChatService.invalidate_presence_recipients_for_chat(
        chat_model(pk=instance.chat_id),
        extra_user_ids=[instance.user_id],
    )


@receiver(post_delete, sender='chat.ChatParticipant')
def invalidate_visibility_cache_on_participant_delete(sender, instance, **kwargs):
    """Hard-deleted participants must lose cached and live visibility too."""
    from .services import ChatService

    # Avoid dereferencing instance.chat during a parent Chat cascade.
    chat_model = sender._meta.get_field('chat').remote_field.model
    ChatService.invalidate_presence_recipients_for_chat(
        chat_model(pk=instance.chat_id),
        extra_user_ids=[instance.user_id],
    )


@receiver(post_save, sender='chat.Message')
def update_message_search_vector(sender, instance, **kwargs):
    """Rebuild the tsvector for this message after every save.

    Revoked or soft-deleted messages get search_vector=NULL so they are
    never surfaced by FTS *or* the icontains fallback (which matches on
    content; nulling out the vector is belt-and-suspenders on top of the
    is_revoked=False / is_deleted=False filters in the search view).
    """
    if instance.is_revoked or instance.is_deleted:
        # Remove from search index immediately — the content field still
        # holds the original text, so we must not leave a populated vector.
        sender.objects.filter(pk=instance.pk).update(search_vector=None)
        return

    # Resolve the sender username at Python level to avoid an FK traversal
    # in the UPDATE statement, which Django/PostgreSQL doesn't support cleanly.
    try:
        sender_name = instance.sender.username or instance.sender.email or ''
    except Exception:
        sender_name = ''

    sender.objects.filter(pk=instance.pk).update(
        search_vector=(
            SearchVector('content', weight='A', config='english')
            + SearchVector(Value(sender_name), weight='B', config='english')
        )
    )


@receiver(pre_delete, sender='chat.Message')
def mark_forwarded_attachment_copies_unavailable(sender, instance, **kwargs):
    """When an original message is deleted, forwarded file copies become tombstones."""
    forwarded_messages = (
        instance.forwarded_messages
        .filter(has_attachments=True)
    )

    for forwarded_message in forwarded_messages:
        forwarded_message.has_attachments = False
        forwarded_message.save(update_fields=['has_attachments', 'updated_at'])
