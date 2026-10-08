import logging
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.files.storage import default_storage
from django.db import models, transaction
from django.utils import timezone

from rest_framework.exceptions import PermissionDenied, ValidationError

from comments.models import Comment, CommentAttachment, CommentMention
from comments.permissions import CommentPermissionService
from comments.registry import CommentTargetRegistry

User = get_user_model()
logger = logging.getLogger(__name__)
UNSET = object()


def extract_body_text(body):
    """
    Extract searchable plain text from the rich-text JSON document.

    The traversal is intentionally schema-light so text extraction is not
    coupled to frontend-only node details.
    """

    if body is None:
        return ""
    if isinstance(body, str):
        return body.strip()
    parts = []

    def visit(value):
        if isinstance(value, dict):
            text = value.get("text")
            if isinstance(text, str):
                parts.append(text)
            for child in value.get("content", []):
                visit(child)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(body)
    return " ".join(part.strip() for part in parts if part and part.strip())


def extract_body_attachment_ids(body):
    """Return attachment UUIDs referenced by inline commentMedia nodes."""

    ids = set()

    def visit(value):
        if isinstance(value, dict):
            if value.get("type") == "commentMedia":
                attrs = value.get("attrs") or {}
                attachment_id = attrs.get("attachmentId")
                if attachment_id:
                    ids.add(str(attachment_id))
            for child in value.get("content", []):
                visit(child)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(body)
    return ids


def delete_comment_attachment_files(attachment):
    """Remove the original upload and any generated preview pages for one attachment."""

    file_name = getattr(attachment.file, "name", "")
    if file_name:
        _delete_storage_path(file_name)

    pages = attachment.preview_metadata.get("preview_pages", [])
    if isinstance(pages, list):
        for page in pages:
            if isinstance(page, dict):
                _delete_storage_path(_storage_path_from_url(page.get("image_url")))


def delete_unbound_comment_attachment(*, user, attachment):
    """
    Delete a staged attachment only while it is still unbound.

    Once an attachment belongs to a saved comment, comment updates should merely
    unbind it; permanent cleanup is handled by the TTL cleanup command below.
    """

    if attachment.comment_id is not None:
        raise ValidationError({"attachment": "Only unbound attachments can be deleted."})
    if attachment.uploaded_by_id != getattr(user, "id", None):
        raise PermissionDenied("You do not have access to delete this attachment.")
    delete_comment_attachment_files(attachment)
    attachment.delete()


def cleanup_orphan_comment_attachments(*, older_than, limit=None):
    """Delete unbound staged attachments older than the supplied datetime."""

    queryset = CommentAttachment.objects.filter(
        comment__isnull=True,
        created_at__lt=older_than,
    ).order_by("created_at")
    if limit is not None:
        queryset = queryset[:limit]

    deleted_count = 0
    for attachment in queryset:
        delete_comment_attachment_files(attachment)
        attachment.delete()
        deleted_count += 1
    return deleted_count


def _delete_storage_path(path):
    if not path:
        return
    try:
        if default_storage.exists(path):
            default_storage.delete(path)
    except Exception:
        # Storage cleanup should not block database cleanup; leaked files can be
        # swept by the next run or by object-storage lifecycle policies.
        logger.error(
            "Comment attachment storage cleanup failed; file is now orphaned: path=%s",
            path, exc_info=True,
        )


def _storage_path_from_url(url):
    if not isinstance(url, str) or not url:
        return None
    parsed_path = urlparse(url).path if "://" in url else url
    media_path = urlparse(getattr(settings, "MEDIA_URL", "/media/")).path
    if media_path and parsed_path.startswith(media_path):
        return parsed_path[len(media_path):].lstrip("/")
    if parsed_path.startswith("/"):
        return parsed_path.lstrip("/")
    return parsed_path


class CommentService:
    """
    Transactional write boundary for comments.

    Views validate HTTP shape, but this service owns consistency across the
    comment row, mentions, inline media attachments, and derived body_text.
    """

    @classmethod
    @transaction.atomic
    def create_comment(
        cls,
        *,
        user,
        target,
        body,
        body_format=Comment.BODY_FORMAT_RICH_TEXT_JSON,
        mention_user_ids=None,
        attachment_ids=None,
    ):
        """
        Create a comment and its related rows atomically.

        If mention or attachment validation fails, the main comment row is rolled
        back so callers never observe a partially-created comment.
        """

        if not CommentPermissionService.can_create(user, target):
            raise PermissionDenied("You do not have access to comment on this target.")

        requested_attachment_ids = attachment_ids or []
        cls._validate_body_attachment_ids(body=body, attachment_ids=requested_attachment_ids)

        comment = Comment.objects.create(
            content_type=ContentType.objects.get_for_model(target),
            object_id=str(target.pk),
            author=user,
            body=body,
            body_text=extract_body_text(body),
            body_format=body_format or Comment.BODY_FORMAT_RICH_TEXT_JSON,
        )
        cls._sync_mentions(comment=comment, target=target, mention_user_ids=mention_user_ids or [])
        cls._sync_attachments(comment=comment, user=user, attachment_ids=requested_attachment_ids)
        return cls.get_comment_for_response(comment.id)

    @classmethod
    @transaction.atomic
    def update_comment(
        cls,
        *,
        user,
        comment,
        body=UNSET,
        body_format=UNSET,
        mention_user_ids=UNSET,
        attachment_ids=UNSET,
    ):
        """
        Update a comment and synchronize optional related collections atomically.

        The UNSET sentinel distinguishes omitted fields from intentional clearing
        of mentions or inline media attachments with an empty list.
        """

        comment = Comment.objects.select_for_update().get(pk=comment.pk)
        if not CommentPermissionService.can_update(user, comment):
            raise PermissionDenied("You do not have access to update this comment.")

        update_fields = []
        if body is not UNSET:
            comment.body = body
            comment.body_text = extract_body_text(body)
            update_fields.extend(["body", "body_text"])
        if body_format is not UNSET:
            comment.body_format = body_format or Comment.BODY_FORMAT_RICH_TEXT_JSON
            update_fields.append("body_format")
        comment.is_edited = True
        update_fields.append("is_edited")
        if update_fields:
            comment.save(update_fields=[*update_fields, "updated_at"])

        target = comment.content_object
        if mention_user_ids is not UNSET:
            cls._sync_mentions(
                comment=comment,
                target=target,
                mention_user_ids=mention_user_ids or [],
            )
        if body is not UNSET and attachment_ids is UNSET:
            attachment_ids = sorted(extract_body_attachment_ids(comment.body))
        if attachment_ids is not UNSET:
            cls._validate_body_attachment_ids(
                body=comment.body,
                attachment_ids=attachment_ids or [],
            )
            cls._sync_attachments(
                comment=comment,
                user=user,
                attachment_ids=attachment_ids or [],
            )
        return cls.get_comment_for_response(comment.id)

    @classmethod
    @transaction.atomic
    def soft_delete_comment(cls, *, user, comment):
        """Soft delete a comment so API reads return 404 without losing audit data."""

        comment = Comment.objects.select_for_update().get(pk=comment.pk)
        if not CommentPermissionService.can_delete(user, comment):
            raise PermissionDenied("You do not have access to delete this comment.")
        comment.deleted_at = timezone.now()
        comment.deleted_by = user
        comment.save(update_fields=["deleted_at", "deleted_by", "updated_at"])

    @classmethod
    def get_comment_for_response(cls, comment_id):
        """Reload a comment with related data needed by the response serializer."""

        return Comment.objects.select_related(
            "author",
            "content_type",
        ).prefetch_related(
            "mentions__mentioned_user",
        ).get(pk=comment_id)

    @classmethod
    def _validate_body_attachment_ids(cls, *, body, attachment_ids):
        """Ensure bound attachments are exactly the files rendered in the editor body."""

        requested_ids = {str(attachment_id) for attachment_id in attachment_ids}
        body_ids = extract_body_attachment_ids(body)
        if requested_ids != body_ids:
            raise ValidationError(
                {
                    "attachment_ids": (
                        "Attachment IDs must match commentMedia nodes in the comment body."
                    )
                }
            )

    @classmethod
    def _sync_mentions(cls, *, comment, target, mention_user_ids):
        """Replace mention rows after confirming every user can access the target."""

        users = cls._get_users(mention_user_ids)
        invalid_users = [
            user for user in users if not CommentPermissionService.can_read(user, target)
        ]
        if invalid_users:
            raise ValidationError(
                {"mentions": "Mentioned users must have access to the comment target."}
            )
        CommentMention.objects.filter(comment=comment).delete()
        CommentMention.objects.bulk_create(
            [
                CommentMention(comment=comment, mentioned_user=user)
                for user in users
            ],
            ignore_conflicts=True,
        )

    @classmethod
    def _sync_attachments(cls, *, comment, user, attachment_ids):
        """Bind exactly the requested staged attachments to the comment."""

        requested_ids = [str(attachment_id) for attachment_id in attachment_ids]
        if len(requested_ids) != len(set(requested_ids)):
            raise ValidationError({"attachment_ids": "Duplicate attachment IDs are not allowed."})

        attachments = list(
            CommentAttachment.objects.select_for_update().filter(id__in=requested_ids)
        )
        if len(attachments) != len(requested_ids):
            raise ValidationError({"attachment_ids": "One or more attachments were not found."})

        for attachment in attachments:
            # Attachments are user-scoped until bound, preventing cross-user file binding.
            if attachment.uploaded_by_id != user.id:
                raise ValidationError({"attachment_ids": "Attachment is not usable by this user."})
            if attachment.comment_id is not None and attachment.comment_id != comment.id:
                raise ValidationError({"attachment_ids": "Attachment is already bound to another comment."})

        requested_set = {attachment.id for attachment in attachments}
        # Removed inline media is unbound first; orphan cleanup deletes files after the TTL.
        CommentAttachment.objects.filter(comment=comment).exclude(id__in=requested_set).update(comment=None)
        if attachments:
            CommentAttachment.objects.filter(id__in=requested_set).update(comment_id=comment.id)

    @classmethod
    def _get_users(cls, user_ids):
        """Resolve mention user IDs with duplicate and missing-user validation."""

        requested_ids = [int(user_id) for user_id in user_ids]
        if len(requested_ids) != len(set(requested_ids)):
            raise ValidationError({"mentions": "Duplicate mentioned users are not allowed."})
        users_by_id = User.objects.in_bulk(requested_ids)
        if len(users_by_id) != len(requested_ids):
            raise ValidationError({"mentions": "One or more mentioned users were not found."})
        return [users_by_id[user_id] for user_id in requested_ids]


def get_users_with_target_access(*, target, query=None):
    """Return mention candidates limited to users who can read the target entity."""

    entity_type = CommentTargetRegistry.get_entity_type_for_instance(target)
    if entity_type == "task":
        project_member_user_ids = target.project.members.filter(
            is_active=True,
        ).values_list("user_id", flat=True)
        user_filter_ids = list(project_member_user_ids)
        if target.current_approver_id:
            user_filter_ids.append(target.current_approver_id)
        queryset = User.objects.filter(id__in=user_filter_ids, is_active=True).distinct()
    else:
        queryset = User.objects.none()

    q = str(query or "").strip()
    if q:
        queryset = queryset.filter(
            models.Q(username__icontains=q)
            | models.Q(email__icontains=q)
            | models.Q(first_name__icontains=q)
            | models.Q(last_name__icontains=q)
        )
    return queryset.order_by("username", "email", "id")
