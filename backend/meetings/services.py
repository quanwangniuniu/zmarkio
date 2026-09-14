import datetime
import logging
import re
from typing import Iterable, List, Optional, Tuple

from django.core.cache import cache as django_cache

logger = logging.getLogger(__name__)
from django.db import transaction
from django.db.models import Count, Q
from django.contrib.postgres.search import SearchQuery, SearchRank
from django.utils import timezone as dj_timezone
from django.utils.text import slugify

from django.contrib.auth import get_user_model
from rest_framework import serializers as drf_serializers
from rest_framework.exceptions import PermissionDenied

from core.models import Project, ProjectMember
from meetings.models import AgendaItem, Meeting, MeetingTypeDefinition, ParticipantLink, MeetingDocument, MeetingActionItem, MeetingTagAssignment, MeetingTemplate
from meetings.audit import record_audit_entry


def assert_meeting_not_archived(meeting: Meeting) -> None:
    """Raises PermissionDenied if the meeting is in the archived terminal state."""
    if meeting.status == Meeting.STATUS_ARCHIVED:
        raise PermissionDenied("Archived meetings are read-only.")

User = get_user_model()


def validate_meeting_for_origin_link(*, meeting_id: int, project: Project, user) -> Meeting:
    """
    Ensure ``meeting_id`` can be used as the origin meeting for a task/decision in ``project``.

    Same-project rules are enforced here; membership is checked against ``project``.
    """
    if project is None:
        raise drf_serializers.ValidationError(
            {"origin_meeting_id": "Project is required."},
        )

    try:
        meeting = Meeting.objects.get(pk=meeting_id, is_deleted=False)
    except Meeting.DoesNotExist:
        raise drf_serializers.ValidationError({"origin_meeting_id": "Meeting not found."})

    if meeting.project_id != project.id:
        raise drf_serializers.ValidationError(
            {"origin_meeting_id": "Meeting must belong to the same project."},
        )

    if meeting.is_archived:
        raise drf_serializers.ValidationError(
            {"origin_meeting_id": "Cannot link to an archived meeting."},
        )

    has_membership = ProjectMember.objects.filter(
        user=user,
        project=project,
        is_active=True,
    ).exists()
    if not has_membership:
        raise drf_serializers.ValidationError(
            {"origin_meeting_id": "You do not have access to this project."},
        )

    return meeting

# Whitelist for GET /projects/{id}/meetings/ — must stay in sync with OpenAPI contract.
MEETING_LIST_ORDERING_MAP: dict[str, Tuple[str, ...]] = {
    "created_at": ("created_at", "id"),
    "-created_at": ("-created_at", "-id"),
    "scheduled_date": ("scheduled_date", "id"),
    "-scheduled_date": ("-scheduled_date", "-id"),
    "updated_at": ("updated_at", "id"),
    "-updated_at": ("-updated_at", "-id"),
    "title": ("title", "id"),
    "-title": ("-title", "-id"),
}


def meetings_base_queryset_for_project(project: Project):
    """
    Project-scoped meetings with provenance counts and ORM optimisation for list/detail.
    """

    return (
        Meeting.objects.for_knowledge_discovery()
        .filter(project=project, is_deleted=False)
        .annotate(
            decision_count=Count(
                "decision_origins",
                filter=Q(decision_origins__decision__is_deleted=False),
                distinct=True,
            ),
            task_count=Count("task_origins", distinct=True),
        )
    )


def apply_meeting_knowledge_filters(qs, filters: dict):
    """
    Apply validated query params. Callers should ``.distinct()`` after this when joins may duplicate rows.
    """

    q = filters.get("q")
    if q:
        search_query = SearchQuery(q)
        qs = (
            qs.filter(search_vector=search_query)
            .annotate(rank=SearchRank("search_vector", search_query))
            .order_by("-rank")
        )

    mt = filters.get("meeting_type")
    if mt:
        # Single slug or list — always OR via __in (never chain .filter(slug=a).filter(slug=b)).
        if isinstance(mt, str):
            slugs = [mt.strip()] if str(mt).strip() else []
        else:
            slugs = [str(x).strip() for x in mt if str(x).strip()]
        if slugs:
            qs = qs.filter(type_definition__slug__in=slugs)

    participants_in = filters.get("participant")
    if participants_in:
        if isinstance(participants_in, int):
            participants_in = [participants_in]
        qs = qs.filter(participant_links__user_id__in=participants_in).distinct()

    participants_ex = filters.get("exclude_participant")
    if participants_ex:
        if isinstance(participants_ex, int):
            participants_ex = [participants_ex]
        bad_meeting_ids = ParticipantLink.objects.filter(
            user_id__in=participants_ex
        ).values_list("meeting_id", flat=True)
        qs = qs.exclude(pk__in=bad_meeting_ids)

    tag = filters.get("tag")
    if tag:
        qs = qs.filter(tag_assignments__tag_definition__slug=tag)

    date_from = filters.get("date_from")
    date_to = filters.get("date_to")
    if date_from or date_to:
        qs = qs.filter(scheduled_date__isnull=False)
        if date_from:
            qs = qs.filter(scheduled_date__gte=date_from)
        if date_to:
            qs = qs.filter(scheduled_date__lte=date_to)

    if "is_archived" in filters:
        qs = qs.filter(is_archived=filters["is_archived"])

    # Origin-only: decision_count / task_count count origins (decisions exclude soft-deleted).
    hgd = filters.get("has_generated_decisions")
    if hgd is True:
        qs = qs.filter(decision_count__gt=0)
    elif hgd is False:
        qs = qs.filter(decision_count=0)

    hgt = filters.get("has_generated_tasks")
    if hgt is True:
        qs = qs.filter(task_count__gt=0)
    elif hgt is False:
        qs = qs.filter(task_count=0)

    return qs


def meeting_list_order_by_fields(ordering: str) -> Tuple[str, ...]:
    return MEETING_LIST_ORDERING_MAP[ordering]


def _is_scheduled_calendar_day_fully_past(
    scheduled_date,
    now: datetime.datetime,
) -> bool:
    """
    Mirror meetings hub client split (``meetingScheduleSplit``): a meeting is **Completed** when the
    scheduled **calendar day** has fully ended in Django's active timezone (see ``TIME_ZONE``).
    Undated meetings are **Incoming**.
    """

    if scheduled_date is None:
        return False
    tz = dj_timezone.get_current_timezone()
    naive_end = datetime.datetime.combine(
        scheduled_date, datetime.time(23, 59, 59, 999999)
    )
    end_of_day = dj_timezone.make_aware(naive_end, tz)
    return now > end_of_day


def hub_split_meeting_pks_for_project(project: Project) -> Tuple[set[int], set[int]]:
    """
    Split **all** project meetings into Incoming vs Completed hub lanes (same calendar-day rule
    as the frontend). Uses a lightweight ``Meeting`` query (no list annotations / JOINs).

    Returns:
        (incoming_pks, completed_pks)
    """

    now = dj_timezone.now()
    incoming: set[int] = set()
    completed: set[int] = set()
    qs = Meeting.objects.filter(project=project, is_deleted=False).values_list(
        "pk", "scheduled_date"
    )
    for pk, sd in qs.iterator(chunk_size=2000):
        if _is_scheduled_calendar_day_fully_past(sd, now):
            completed.add(pk)
        else:
            incoming.add(pk)
    return incoming, completed


def ensure_meeting_type_definition(project: Project, label: str) -> MeetingTypeDefinition:
    """
    Resolve a human-entered meeting type string to a **project-scoped** structured row.
    """

    raw = (label or "").strip() or "general"
    safe_label = raw[:160]
    base_slug = (slugify(raw)[:80] or "general")[:80]
    slug = base_slug
    for i in range(1000):
        if i > 0:
            suffix = f"-{i}"
            slug = (base_slug[: 80 - len(suffix)] + suffix)[:80]
        mtd, created = MeetingTypeDefinition.objects.get_or_create(
            project_id=project.id,
            slug=slug,
            defaults={"label": safe_label},
        )
        if created or mtd.label == safe_label:
            return mtd
    raise ValueError("Could not allocate meeting type slug")  # pragma: no cover - defensive


def transition_meeting_status(
    meeting: Meeting,
    new_status: str,
    actor: Optional[User],
    reason: str = 'user_initiated',
) -> None:
    """
    Transition a meeting to a new status.
    Records an audit entry if transition succeeds.
    """
    old_status = meeting.status
    meeting.status = new_status
    meeting.save(update_fields=['status'])

    record_audit_entry(
        meeting=meeting,
        actor=actor,
        event_type='meeting.status_changed',
        before={'status': old_status},
        after={'status': new_status},
        context={'reason': reason},
    )


def update_meeting_title(meeting: Meeting, new_title: str, actor: Optional[User]) -> None:
    """Update meeting title and record audit entry."""
    assert_meeting_not_archived(meeting)
    old_title = meeting.title
    meeting.title = new_title
    meeting.save(update_fields=['title'])
    record_audit_entry(
        meeting=meeting, actor=actor, event_type='meeting.title_changed',
        before={'title': old_title}, after={'title': new_title},
    )


def update_meeting_objective(meeting: Meeting, new_objective: str, actor: Optional[User]) -> None:
    """Update meeting objective and record audit entry."""
    assert_meeting_not_archived(meeting)
    old_objective = meeting.objective
    meeting.objective = new_objective
    meeting.save(update_fields=['objective'])
    record_audit_entry(
        meeting=meeting, actor=actor, event_type='meeting.objective_changed',
        before={'objective': old_objective}, after={'objective': new_objective},
    )


def update_meeting_summary(meeting: Meeting, new_summary: str, actor: Optional[User]) -> None:
    """Update meeting summary and record audit entry."""
    assert_meeting_not_archived(meeting)
    old_summary = meeting.summary
    meeting.summary = new_summary
    meeting.save(update_fields=['summary'])
    record_audit_entry(
        meeting=meeting, actor=actor, event_type='meeting.summary_changed',
        before={'summary': old_summary}, after={'summary': new_summary},
    )


@transaction.atomic
def add_participant(
    meeting: Meeting,
    user: User,
    role: str,
    actor: Optional[User],
) -> ParticipantLink:
    """
    Add a user to a meeting as a participant.
    Records an audit entry if creation succeeds.

    Args:
        meeting: The Meeting to add the participant to.
        user: The User to add as a participant.
        role: The participant role (e.g., 'attendee', 'facilitator').
        actor: The User adding the participant (for audit recording).

    Returns:
        The created ParticipantLink instance.
    """
    assert_meeting_not_archived(meeting)
    link = ParticipantLink.objects.create(
        meeting=meeting,
        user=user,
        role=role,
    )

    record_audit_entry(
        meeting=meeting,
        actor=actor,
        event_type='meeting.participant_added',
        after={
            'user_id': user.id,
            'user_name': (f"{user.first_name} {user.last_name}".strip() or user.username),
            'role': role,
        },
        context={
            'participant_count_after': meeting.participant_links.count(),
        },
    )

    return link


@transaction.atomic
def remove_participant(
    meeting: Meeting,
    user: User,
    actor: Optional[User],
) -> None:
    """
    Remove a user from a meeting.
    Records an audit entry if removal succeeds.

    Args:
        meeting: The Meeting from which to remove the participant.
        user: The User to remove.
        actor: The User performing the removal (for audit recording).

    Raises:
        ValueError: If the user is not a participant of the meeting.
    """
    assert_meeting_not_archived(meeting)
    try:
        link = ParticipantLink.objects.get(meeting=meeting, user=user)
    except ParticipantLink.DoesNotExist:
        raise ValueError(f"User {user.id} is not a participant of meeting {meeting.id}")

    user_name = (f"{user.first_name} {user.last_name}".strip() or user.username)
    user_id = user.id

    link.delete()

    record_audit_entry(
        meeting=meeting,
        actor=actor,
        event_type='meeting.participant_removed',
        after={
            'user_id': user_id,
            'user_name': user_name,
        },
        context={
            'participant_count_after': meeting.participant_links.count(),
        },
    )


def user_has_meeting_document_access(user_id: int, meeting: Meeting) -> bool:
    """Project member (active) or explicit meeting participant may load/sync the meeting document."""
    if ProjectMember.objects.filter(
        user_id=user_id,
        project_id=meeting.project_id,
        is_active=True,
    ).exists():
        return True
    return ParticipantLink.objects.filter(meeting_id=meeting.id, user_id=user_id).exists()


def reorder_agenda_items(meeting_id: int, items: Iterable[dict]) -> List[AgendaItem]:
    """
    Reorder agenda items for a given meeting.

    This function performs the update in a single transaction to avoid
    transient unique_together(meeting, order_index) conflicts.
    """
    assert_meeting_not_archived(Meeting.objects.only("status").get(pk=meeting_id))
    items = list(items)

    temp_offset = 1000000

    with transaction.atomic():
        agenda_items = {
            item.id: item
            for item in AgendaItem.objects.select_for_update().filter(
                meeting_id=meeting_id, id__in=[i["id"] for i in items]
            )
        }

        for payload in items:
            agenda_item = agenda_items.get(payload["id"])
            if not agenda_item:
                continue
            agenda_item.order_index = payload["order_index"] + temp_offset
            agenda_item.save(update_fields=["order_index"])

        for payload in items:
            agenda_item = agenda_items.get(payload["id"])
            if not agenda_item:
                continue
            agenda_item.order_index = payload["order_index"]
            agenda_item.save(update_fields=["order_index"])

    return list(
        AgendaItem.objects.filter(meeting_id=meeting_id).order_by("order_index")
    )


def get_or_create_meeting_document(meeting_id: int) -> MeetingDocument:
    document, _ = MeetingDocument.objects.get_or_create(meeting_id=meeting_id)
    return document


def _meeting_document_text_preview(text: str, *, max_len: int = 50) -> str:
    s = (text or "").strip()
    if len(s) <= max_len:
        return s
    return s[:max_len] + "…"


def normalize_text(text: str) -> str:
    """Strip leading/trailing whitespace and collapse internal runs to a single space."""
    return re.sub(r'\s+', ' ', (text or '').strip())


# Updates arriving within this window are merged into one notification record.
# Set to 3 s: 1 s frontend debounce + up to 2 s of network / processing jitter.
_AGENDA_NOTIF_DEDUP_TTL = 3


def upsert_agenda_item_notification(
    *,
    meeting: Meeting,
    item_id: int,
    old_content: str,
    new_content: str,
    actor_id: int,
    recipient_id: int,
) -> None:
    """
    Create-or-update a ``MEETING_AGENDA_CHANGED`` notification within a dedup window.

    Strategy
    --------
    * **data key** ``notif_debounce:{item_id}:{recipient_id}`` — stores the DB notification ID
      once it has been created, letting subsequent requests *merge* rather than create.
    * **lock key** ``notif_lock:{item_id}:{recipient_id}`` — set with ``cache.add()`` so only
      ONE concurrent request may reach the DB create path; others are suppressed atomically.
    * ``old_content`` is preserved from the very first call so the notification always shows
      what the item looked like *before* the edit session started.
    * ``new_content`` is updated on every merge so the recipient sees the latest text.
    """
    from notifications.models import Notification, NotificationCategory, NotificationEventType
    from notifications.services import create_notification
    from notifications.action_urls import meeting_action_url
    from notifications.sse import publish_notification_to_redis

    data_key = f"notif_debounce:{item_id}:{recipient_id}"
    lock_key = f"notif_lock:{item_id}:{recipient_id}"

    existing_id = django_cache.get(data_key)

    if existing_id:
        # MERGE PATH — update the existing notification with the latest content.
        try:
            notif = Notification.objects.get(id=existing_id)
            # Only update if the content is substantively different (normalised comparison).
            current_new = notif.metadata.get("new_agenda", "")
            if normalize_text(current_new) == normalize_text(new_content):
                logger.warning(
                    "NOTIF_SUPPRESSED: item=%s recipient=%s — content unchanged after normalisation",
                    item_id, recipient_id,
                )
                return
            notif.metadata["new_agenda"] = new_content
            notif.save(update_fields=["metadata"])
            publish_notification_to_redis(recipient_id, notif)
            django_cache.set(data_key, existing_id, timeout=_AGENDA_NOTIF_DEDUP_TTL)
            logger.warning(
                "NOTIF_MERGED: item=%s recipient=%s — updated new_agenda to '%.50s'",
                item_id, recipient_id, new_content,
            )
            return
        except Notification.DoesNotExist:
            pass  # stale cache — fall through to create a fresh notification

    # CREATION PATH — use cache.add() so only one concurrent request wins.
    acquired = django_cache.add(lock_key, "1", timeout=_AGENDA_NOTIF_DEDUP_TTL)
    if not acquired:
        logger.warning(
            "NOTIF_SUPPRESSED: item=%s recipient=%s — concurrent duplicate blocked by lock",
            item_id, recipient_id,
        )
        return

    notif = create_notification(
        recipient_id=recipient_id,
        actor_id=actor_id,
        category=NotificationCategory.MEETINGS,
        event_type=NotificationEventType.MEETING_UPDATED,
        title=f"Meeting updated: {meeting.title}",
        body="Meeting details were changed.",
        related_object_type="meeting",
        related_object_id=str(meeting.id),
        action_url=meeting_action_url(meeting.slug, meeting.project_id),
        metadata={
            "project_id": meeting.project_id,
            "item_id": item_id,
            "change_type": "agenda_item",
            "old_agenda": old_content,
            "new_agenda": new_content,
        },
    )
    if notif:
        # create_notification already publishes SSE notification internally
        django_cache.set(data_key, str(notif.id), timeout=_AGENDA_NOTIF_DEDUP_TTL)
    else:
        # create_notification returned None — preferences/settings blocked it.
        # Release the lock so the next update within the TTL can attempt again.
        django_cache.delete(lock_key)
        logger.warning(
            "NOTIF_BLOCKED (upsert): meeting=%s item=%s recipient=%s — "
            "create_notification returned None (check preferences or user existence)",
            meeting.id, item_id, recipient_id,
        )


def notify_agenda_event(
    *,
    meeting: Meeting,
    actor_id: int,
    recipient_id: int,
    change_type: str,
    item_id: int | None = None,
    old_content: str = "",
    new_content: str = "",
) -> None:
    """
    Dedup-aware ``MEETING_UPDATED`` notification for agenda-item create / delete events.

    Cache-key scope
    ---------------
    When ``item_id`` is provided the dedup window is *per-item*, so creating /
    deleting two different items in quick succession each produces their own
    notification.  This prevents the "meeting-wide" key from accidentally
    suppressing legitimate events for unrelated items.

    Lock safety
    -----------
    The lock key is released (deleted) if ``create_notification`` fails so that
    callers are not silently blocked for the full TTL on a transient error.
    """
    from notifications.models import NotificationCategory, NotificationEventType
    from notifications.services import create_notification
    from notifications.action_urls import meeting_action_url

    scope = str(item_id) if item_id is not None else "meeting"
    data_key = f"notif_debounce:{meeting.id}:{change_type}:{scope}:{recipient_id}"
    lock_key = f"notif_lock:{meeting.id}:{change_type}:{scope}:{recipient_id}"

    logger.info(
        "NOTIF_AGENDA_EVENT: meeting=%s change_type=%s item=%s recipient=%s",
        meeting.id, change_type, item_id, recipient_id,
    )

    # Pure dedup: within the TTL window, skip duplicate events for the same item.
    if django_cache.get(data_key):
        logger.warning(
            "NOTIF_SUPPRESSED: meeting=%s change_type=%s item=%s recipient=%s — within dedup window",
            meeting.id, change_type, item_id, recipient_id,
        )
        return

    # Atomic lock so only one concurrent request reaches the DB create path.
    acquired = django_cache.add(lock_key, "1", timeout=_AGENDA_NOTIF_DEDUP_TTL)
    if not acquired:
        logger.warning(
            "NOTIF_SUPPRESSED: meeting=%s change_type=%s item=%s recipient=%s — concurrent duplicate blocked",
            meeting.id, change_type, item_id, recipient_id,
        )
        return

    metadata: dict = {"project_id": meeting.project_id, "change_type": change_type}
    if old_content:
        metadata["old_agenda"] = old_content
    if new_content:
        metadata["new_agenda"] = new_content

    try:
        notif = create_notification(
            recipient_id=recipient_id,
            actor_id=actor_id,
            category=NotificationCategory.MEETINGS,
            event_type=NotificationEventType.MEETING_UPDATED,
            title=f"Meeting updated: {meeting.title}",
            body="Meeting details were changed.",
            related_object_type="meeting",
            related_object_id=str(meeting.id),
            action_url=meeting_action_url(meeting.slug, meeting.project_id),
            metadata=metadata,
        )
        if notif:
            django_cache.set(data_key, str(notif.id), timeout=_AGENDA_NOTIF_DEDUP_TTL)
            logger.info(
                "NOTIF_CREATED: id=%s meeting=%s change_type=%s item=%s recipient=%s",
                notif.id, meeting.id, change_type, item_id, recipient_id,
            )
        else:
            # create_notification returned None — release the lock so retries are possible.
            django_cache.delete(lock_key)
            logger.warning(
                "NOTIF_FAILED: create_notification returned None for meeting=%s change_type=%s item=%s recipient=%s",
                meeting.id, change_type, item_id, recipient_id,
            )
    except Exception:
        django_cache.delete(lock_key)
        logger.exception(
            "NOTIF_ERROR: exception in create_notification for meeting=%s change_type=%s item=%s recipient=%s",
            meeting.id, change_type, item_id, recipient_id,
        )
        raise


def notify_participants_meeting_document_updated(
    *,
    meeting: Meeting,
    editor_id: int,
    old_content: str,
    new_content: str,
    document_id: int | None = None,
) -> None:
    """
    Notify meeting participants (except the editor) that collaborative notes changed.
    Uses DOC_ASSET_UPDATE + metadata keys compatible with DrawerWhatChanged.
    """
    if old_content == new_content:
        return

    from notifications.models import NotificationCategory, NotificationEventType
    from notifications.services import create_notification
    from notifications.action_urls import meeting_action_url

    title_label = meeting.title
    meta = {
        "project_id": meeting.project_id,
        "meeting_id": meeting.id,
        "meeting_title": title_label,
        "old_title": title_label,
        "new_title": title_label,
        "old_agenda": _meeting_document_text_preview(old_content),
        "new_agenda": _meeting_document_text_preview(new_content),
    }
    if document_id is not None:
        meta["asset_id"] = document_id

    participant_ids = meeting.participant_links.filter(is_accepted=True).values_list("user_id", flat=True)
    for uid in participant_ids:
        if uid == editor_id:
            continue
        create_notification(
            recipient_id=uid,
            actor_id=editor_id,
            category=NotificationCategory.COLLABORATION,
            event_type=NotificationEventType.DOC_ASSET_UPDATE,
            title=f"Meeting notes updated: {meeting.title}",
            body="The collaborative document for this meeting was edited.",
            related_object_type="meeting",
            related_object_id=str(meeting.id),
            action_url=meeting_action_url(meeting.slug, meeting.project_id),
            metadata=meta,
        )


def update_meeting_document_content(
    *,
    meeting_id: int,
    content: str,
    yjs_state: str | None = None,
    user_id: int | None = None,
    notify_collaborators: bool = False,
) -> MeetingDocument:
    assert_meeting_not_archived(Meeting.objects.only("status").get(pk=meeting_id))
    document = get_or_create_meeting_document(meeting_id)
    old_content = document.content
    document.content = content
    if isinstance(yjs_state, str):
        document.yjs_state = yjs_state
    document.last_edited_by_id = user_id
    update_fields = ["content", "last_edited_by", "updated_at"]
    if isinstance(yjs_state, str):
        update_fields.append("yjs_state")
    document.save(update_fields=update_fields)

    if (
        notify_collaborators
        and user_id
        and old_content != content
    ):
        meeting = Meeting.objects.select_related("project").get(pk=meeting_id)
        notify_participants_meeting_document_updated(
            meeting=meeting,
            editor_id=user_id,
            old_content=old_content,
            new_content=content,
            document_id=document.id,
        )

    return document


@transaction.atomic
def update_document_content(
    document: MeetingDocument,
    new_content: str,
    actor: Optional[User],
) -> MeetingDocument:
    """
    Update meeting document content and record audit entry.

    Args:
        document: The MeetingDocument to update.
        new_content: The new document content.
        actor: The User updating the document (for audit recording).

    Returns:
        The updated MeetingDocument instance.
    """
    old_content = document.content
    document.content = new_content
    document.save(update_fields=['content'])

    char_diff = len(new_content) - len(old_content)

    record_audit_entry(
        meeting=document.meeting,
        actor=actor,
        event_type='meeting.document_edited',
        context={
            'char_count_before': len(old_content),
            'char_count_after': len(new_content),
            'char_diff': char_diff,
        },
    )

    return document


@transaction.atomic
def create_agenda_item(
    meeting: Meeting,
    content: str,
    order_index: int,
    is_priority: bool,
    actor: Optional[User],
) -> AgendaItem:
    """
    Create agenda item and record audit entry.

    Args:
        meeting: The Meeting to add the agenda item to.
        content: The agenda item content/text.
        order_index: The position in the agenda (0-indexed).
        is_priority: Whether this item is marked as priority.
        actor: The User creating the agenda item (for audit recording).

    Returns:
        The created AgendaItem instance.
    """
    assert_meeting_not_archived(meeting)
    item = AgendaItem.objects.create(
        meeting=meeting,
        content=content,
        order_index=order_index,
        is_priority=is_priority,
    )

    record_audit_entry(
        meeting=meeting,
        actor=actor,
        event_type='meeting.agenda_item_added',
        after={
            'content': content,
            'order_index': order_index,
            'is_priority': is_priority,
        },
    )

    return item


@transaction.atomic
def update_agenda_item(
    item: AgendaItem,
    content: str,
    is_priority: bool,
    actor: Optional[User],
) -> AgendaItem:
    """
    Update agenda item and record audit entry.

    Args:
        item: The AgendaItem to update.
        content: The new agenda item content.
        is_priority: Whether the item is marked as priority.
        actor: The User updating the agenda item (for audit recording).

    Returns:
        The updated AgendaItem instance.
    """
    assert_meeting_not_archived(item.meeting)
    old_content = item.content
    old_priority = item.is_priority

    item.content = content
    item.is_priority = is_priority
    item.save(update_fields=['content', 'is_priority'])

    record_audit_entry(
        meeting=item.meeting,
        actor=actor,
        event_type='meeting.agenda_item_edited',
        before={
            'content': old_content,
            'is_priority': old_priority,
        },
        after={
            'content': content,
            'is_priority': is_priority,
        },
        context={'order_index': item.order_index},
    )

    return item


@transaction.atomic
def delete_agenda_item(item: AgendaItem, actor: Optional[User]) -> None:
    """
    Delete agenda item and record audit entry.

    Args:
        item: The AgendaItem to delete.
        actor: The User deleting the agenda item (for audit recording).
    """
    assert_meeting_not_archived(item.meeting)
    meeting = item.meeting
    content = item.content
    order_index = item.order_index

    item.delete()

    record_audit_entry(
        meeting=meeting,
        actor=actor,
        event_type='meeting.agenda_item_deleted',
        before={
            'content': content,
            'order_index': order_index,
        },
    )


@transaction.atomic
def create_action_item(
    meeting: Meeting,
    title: str,
    description: str,
    order_index: int,
    actor: Optional[User],
) -> MeetingActionItem:
    """
    Create action item and record audit entry.

    Args:
        meeting: The Meeting to add the action item to.
        title: The action item title.
        description: The action item description.
        order_index: The position in the action items list.
        actor: The User creating the action item (for audit recording).

    Returns:
        The created MeetingActionItem instance.
    """
    assert_meeting_not_archived(meeting)
    item = MeetingActionItem.objects.create(
        meeting=meeting,
        title=title,
        description=description,
        order_index=order_index,
    )

    record_audit_entry(
        meeting=meeting,
        actor=actor,
        event_type='meeting.action_item_added',
        after={
            'title': title,
            'description': description,
            'order_index': order_index,
        },
    )

    return item


@transaction.atomic
def update_action_item(
    item: MeetingActionItem,
    title: str,
    description: str,
    is_resolved: bool,
    actor: Optional[User],
) -> MeetingActionItem:
    """
    Update action item and record audit entry.

    Args:
        item: The MeetingActionItem to update.
        title: The new action item title.
        description: The new action item description.
        is_resolved: Whether the action item is marked as resolved.
        actor: The User updating the action item (for audit recording).

    Returns:
        The updated MeetingActionItem instance.
    """
    assert_meeting_not_archived(item.meeting)
    old_title = item.title
    old_resolved = item.is_resolved

    item.title = title
    item.description = description
    item.is_resolved = is_resolved
    item.save(update_fields=['title', 'description', 'is_resolved'])

    just_resolved = is_resolved and not old_resolved

    record_audit_entry(
        meeting=item.meeting,
        actor=actor,
        event_type='meeting.action_item_resolved' if just_resolved else 'meeting.action_item_edited',
        before={
            'title': old_title,
            'is_resolved': old_resolved,
        },
        after={
            'title': title,
            'is_resolved': is_resolved,
        },
    )

    return item


@transaction.atomic
def resolve_action_item(
    item: MeetingActionItem,
    actor: Optional[User],
) -> MeetingActionItem:
    """
    Mark action item as resolved and record audit entry.

    Args:
        item: The MeetingActionItem to resolve.
        actor: The User resolving the action item (for audit recording).

    Returns:
        The updated MeetingActionItem instance.
    """
    item.is_resolved = True
    item.save(update_fields=['is_resolved'])

    record_audit_entry(
        meeting=item.meeting,
        actor=actor,
        event_type='meeting.action_item_resolved',
        after={
            'title': item.title,
            'id': item.id,
        },
    )

    return item


@transaction.atomic
def update_meeting_type(
    meeting: Meeting,
    new_type: MeetingTypeDefinition,
    actor: Optional[User],
) -> None:
    """Update meeting type and record audit entry."""
    assert_meeting_not_archived(meeting)
    old_type_id = meeting.type_definition_id
    meeting.type_definition = new_type
    meeting.save(update_fields=['type_definition'])

    record_audit_entry(
        meeting=meeting,
        actor=actor,
        event_type='meeting.type_changed',
        before={'type_definition_id': old_type_id},
        after={'type_definition_id': new_type.id},
    )


@transaction.atomic
def update_meeting_datetime(
    meeting: Meeting,
    scheduled_date,
    scheduled_time,
    actor: Optional[User],
) -> None:
    """Update meeting date and time and record audit entry."""
    assert_meeting_not_archived(meeting)
    old_date = meeting.scheduled_date
    old_time = meeting.scheduled_time

    meeting.scheduled_date = scheduled_date
    meeting.scheduled_time = scheduled_time
    meeting.save(update_fields=['scheduled_date', 'scheduled_time'])

    record_audit_entry(
        meeting=meeting,
        actor=actor,
        event_type='meeting.datetime_changed',
        before={
            'scheduled_date': str(old_date) if old_date else None,
            'scheduled_time': str(old_time) if old_time else None,
        },
        after={
            'scheduled_date': str(scheduled_date) if scheduled_date else None,
            'scheduled_time': str(scheduled_time) if scheduled_time else None,
        },
    )


@transaction.atomic
def update_meeting_tags(
    meeting: Meeting,
    tag_definition_ids: list,
    actor: Optional[User],
) -> None:
    """Update meeting tags and record audit entry."""
    assert_meeting_not_archived(meeting)
    old_tags = list(meeting.tag_assignments.values_list('tag_definition_id', flat=True))

    meeting.tag_assignments.all().delete()
    for tag_id in tag_definition_ids:
        MeetingTagAssignment.objects.create(meeting=meeting, tag_definition_id=tag_id)

    new_tags = tag_definition_ids

    record_audit_entry(
        meeting=meeting,
        actor=actor,
        event_type='meeting.tags_changed',
        before={'tag_ids': old_tags},
        after={'tag_ids': new_tags},
        context={'count_before': len(old_tags), 'count_after': len(new_tags)},
    )


@transaction.atomic
def apply_template(
    meeting: Meeting,
    template: MeetingTemplate,
    actor: Optional[User],
) -> None:
    """Apply a template to meeting and record audit entry."""
    meeting.layout_config = template.layout_config
    meeting.save(update_fields=['layout_config'])

    record_audit_entry(
        meeting=meeting,
        actor=actor,
        event_type='meeting.template_applied',
        context={
            'template_id': template.id,
            'template_name': template.name,
            'blocks_applied': list(template.layout_config.keys()) if template.layout_config else [],
        },
    )


def record_decision_created(
    meeting: Meeting,
    decision_id: int,
    actor: Optional[User],
) -> None:
    """Record audit entry when a decision is created from a meeting."""
    record_audit_entry(
        meeting=meeting,
        actor=actor,
        event_type='meeting.decision_created',
        context={'decision_id': decision_id},
    )


def record_decision_updated(
    meeting: Meeting,
    decision_id: int,
    actor: Optional[User],
) -> None:
    """Record audit entry when a decision linked to a meeting is modified."""
    record_audit_entry(
        meeting=meeting,
        actor=actor,
        event_type='meeting.decision_updated',
        context={'decision_id': decision_id},
    )


def record_decision_deleted(
    meeting: Meeting,
    decision_id: int,
    actor: Optional[User],
) -> None:
    """Record audit entry when a decision linked to a meeting is deleted."""
    record_audit_entry(
        meeting=meeting,
        actor=actor,
        event_type='meeting.decision_deleted',
        context={'decision_id': decision_id},
    )


def record_task_created(
    meeting: Meeting,
    task_id: int,
    actor: Optional[User],
) -> None:
    """Record audit entry when a task is created from a meeting."""
    record_audit_entry(
        meeting=meeting,
        actor=actor,
        event_type='meeting.task_created',
        context={'task_id': task_id},
    )


def record_task_updated(
    meeting: Meeting,
    task_id: int,
    actor: Optional[User],
) -> None:
    """Record audit entry when a task linked to a meeting is modified."""
    record_audit_entry(
        meeting=meeting,
        actor=actor,
        event_type='meeting.task_updated',
        context={'task_id': task_id},
    )


def record_task_deleted(
    meeting: Meeting,
    task_id: int,
    actor: Optional[User],
) -> None:
    """Record audit entry when a task linked to a meeting is deleted."""
    record_audit_entry(
        meeting=meeting,
        actor=actor,
        event_type='meeting.task_deleted',
        context={'task_id': task_id},
    )

