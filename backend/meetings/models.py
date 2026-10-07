"""
Meetings domain models with **structured, project-scoped metadata** for knowledge discovery.

Indexing strategy (B-tree, Postgres-friendly)
---------------------------------------------
- **Project is always the first column** on `Meeting` composite indexes so every list/search
  query narrows the heap immediately (scope + scale).
- **``(project, scheduled_date)``** supports date-range filters (``meeting_date`` dimension uses
  ``scheduled_date`` as the canonical calendar anchor; time-of-day remains in ``scheduled_time``).
- **``(project, is_archived, -updated_at)``** supports "active vs archived knowledge" slices and
  recency sorts without full scans.
- **``(project, -created_at)``** supports default "newest first" browsing.
- **ParticipantLink (user, meeting)** speeds "meetings for this participant" reverse lookups.
- **MeetingTagAssignment (tag_definition, meeting)** speeds "meetings with this tag" filters.
  Index names are kept <= 30 characters for SQLite compatibility.
- **FK columns** (`type_definition`, `project`, etc.) get implicit B-tree indexes in Django/Postgres.

Full-text / ranking (next steps, not applied here)
--------------------------------------------------
- For instant text search on ``title`` + ``summary`` + ``objective``, add a generated
  ``tsvector`` column + **GIN** index (via ``django.contrib.postgres.search`` or raw SQL).
  That stays out of this migration to avoid DB-specific coupling until search endpoints land.

Retrieval optimization (ORM)
----------------------------
Use ``Meeting.objects.for_knowledge_discovery()`` for list/detail paths that power search UI:
``select_related`` for scalar FKs and ``prefetch_related`` for participants, tags, and
provenance links to decisions/tasks.
"""

from django.conf import settings
from core.slug_mixins import SluggedResourceModelMixin
from django.db import models
import uuid

from core.models import TimeStampedModel
from meetings.querysets import MeetingManager

from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField

class MeetingTypeDefinition(models.Model):
    """
    Project-scoped meeting **type** vocabulary (structured filter dimension: ``slug``).
    """

    project = models.ForeignKey(
        "core.Project",
        on_delete=models.CASCADE,
        related_name="meeting_type_definitions",
    )
    slug = models.SlugField(max_length=80)
    label = models.CharField(max_length=160)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "slug"],
                name="meetings_type_def_unique_project_slug",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "slug"], name="mtgs_typedef_prj_slug"),
        ]

    def __str__(self) -> str:
        return f"{self.label} ({self.project_id})"


class MeetingTagDefinition(models.Model):
    """
    Project-scoped **tag** vocabulary (structured filter dimension: ``slug``).
    """

    project = models.ForeignKey(
        "core.Project",
        on_delete=models.CASCADE,
        related_name="meeting_tag_definitions",
    )
    slug = models.SlugField(max_length=80)
    label = models.CharField(max_length=160)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "slug"],
                name="meetings_tag_def_unique_project_slug",
            ),
        ]
        indexes = [
            models.Index(fields=["project", "slug"], name="mtgs_tagdef_prj_slug"),
        ]

    def __str__(self) -> str:
        return f"#{self.slug} ({self.project_id})"


class Meeting(SluggedResourceModelMixin, TimeStampedModel):
    """
    Single meeting, strictly **project-scoped**.

    Cognitive clarity fields: ``title``, ``summary``, ``is_archived``, timestamps (via
    ``TimeStampedModel``). Archived meetings are immutable at the API/service layer (enforced
    when write endpoints are updated).
    """

    STATUS_DRAFT = "draft"
    STATUS_PLANNED = "planned"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_COMPLETED = "completed"
    STATUS_ARCHIVED = "archived"

    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_PLANNED, "Planned"),
        (STATUS_IN_PROGRESS, "In Progress"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_ARCHIVED, "Archived"),
    ]

    VALID_TRANSITIONS = {
        STATUS_DRAFT:       [STATUS_PLANNED],
        STATUS_PLANNED:     [STATUS_IN_PROGRESS, STATUS_DRAFT],
        STATUS_IN_PROGRESS: [STATUS_COMPLETED, STATUS_PLANNED],
        STATUS_COMPLETED:   [STATUS_ARCHIVED],
        STATUS_ARCHIVED:    [],
    }

    project = models.ForeignKey(
        "core.Project",
        on_delete=models.CASCADE,
        related_name="meetings",
    )
    title = models.CharField(max_length=255)
    type_definition = models.ForeignKey(
        MeetingTypeDefinition,
        on_delete=models.PROTECT,
        related_name="meetings",
    )
    objective = models.TextField()
    summary = models.TextField(
        blank=True,
        default="",
        help_text="Concise outcomes / takeaways for scanning and search snippets.",
    )
    scheduled_date = models.DateField(blank=True, null=True)
    scheduled_time = models.TimeField(blank=True, null=True)
    external_reference = models.CharField(max_length=255, blank=True, null=True)
    layout_config = models.JSONField(default=dict, null=True, blank=True)
    status = models.CharField(
        max_length=32,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
    )
    is_archived = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Archived meetings are treated as immutable knowledge records.",
    )
    is_deleted = models.BooleanField(
        default=False,
        help_text="Soft-delete flag (added in migration 0002).",
    )
    transcript = models.TextField(
        blank=True,
        default="",
        help_text="Plain-text Zoom transcript for full-text search."
    )
    search_vector = SearchVectorField(null=True, blank=True)
    minutes_published = models.BooleanField(
        default=False,
        help_text="Whether the meeting minutes have been published to all participants.",
    )

    objects = MeetingManager()

    class Meta:
        indexes = [
            models.Index(
                fields=["project", "-created_at"],
                name="mtgs_mtg_prj_crtd_d",
            ),
            models.Index(
                fields=["project", "is_archived", "-updated_at"],
                name="mtgs_mtg_prj_arch_u",
            ),
            models.Index(
                fields=["project", "scheduled_date"],
                name="mtgs_mtg_prj_sched",
            ),
            models.Index(
                fields=["project", "type_definition"],
                name="mtgs_mtg_prj_type",
            ),
            GinIndex(
                fields=["search_vector"],
                name="mtgs_mtg_search_vec",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.project_id})"


class AgendaItem(models.Model):
    """
    AgendaItem model stores a single agenda entry for a meeting.
    """

    meeting = models.ForeignKey(
        Meeting,
        on_delete=models.CASCADE,
        related_name="agenda_items",
    )
    content = models.TextField()
    order_index = models.PositiveIntegerField()
    is_priority = models.BooleanField(default=False)

    class Meta:
        unique_together = ("meeting", "order_index")

    def __str__(self) -> str:
        return f"AgendaItem #{self.order_index} for meeting {self.meeting_id}"


class ParticipantLink(models.Model):
    """
    ParticipantLink model represents a link between a meeting and a user.
    """

    meeting = models.ForeignKey(
        Meeting,
        on_delete=models.CASCADE,
        related_name="participant_links",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="meeting_participations",
    )
    role = models.CharField(max_length=100, blank=True, null=True)
    is_accepted = models.BooleanField(
        default=False,
        help_text="True once the invitee explicitly accepts; False while the invite is pending.",
    )

    class Meta:
        unique_together = ("meeting", "user")
        indexes = [
            models.Index(
                fields=["user", "meeting"],
                name="mtgs_prt_usr_mtg",
            ),
        ]

    def __str__(self) -> str:
        return f"ParticipantLink user={self.user_id} meeting={self.meeting_id}"


class MeetingTagAssignment(models.Model):
    """
    Structured **tag** assignment: links a meeting to a project-scoped tag definition.
    """

    meeting = models.ForeignKey(
        Meeting,
        on_delete=models.CASCADE,
        related_name="tag_assignments",
    )
    tag_definition = models.ForeignKey(
        MeetingTagDefinition,
        on_delete=models.CASCADE,
        related_name="assignments",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["meeting", "tag_definition"],
                name="meetings_tag_assign_unique_meeting_tag",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tag_definition", "meeting"],
                name="mtgs_tagas_tag_mtg",
            ),
        ]

    def __str__(self) -> str:
        return f"Tag {self.tag_definition_id} -> meeting {self.meeting_id}"


class MeetingDecisionOrigin(models.Model):
    """
    Provenance: a **decision** generated from / anchored to a single meeting (one origin row).

    **Semantics**
    - ``decision_id`` is unique (OneToOne): each decision has at most one origin meeting.
    - ``(meeting_id, decision_id)`` is unique at the DB level (explicit composite constraint).
    - Same-project alignment (meeting.project == decision.project) is enforced in application code
      when creating links via ``origin_meeting_id`` on decision create.
    """

    meeting = models.ForeignKey(
        Meeting,
        on_delete=models.CASCADE,
        related_name="decision_origins",
    )
    decision = models.OneToOneField(
        "decision.Decision",
        on_delete=models.CASCADE,
        related_name="meeting_origin",
    )
    origin_timestamp = models.DateTimeField()
    creation_context = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="meeting_decision_origins",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["meeting", "decision"],
                name="mtgs_dcor_unique_meeting_decision",
            ),
        ]
        indexes = [
            models.Index(
                fields=["meeting", "decision"],
                name="mtgs_dcor_mtg_dec",
            ),
            models.Index(
                fields=["meeting", "origin_timestamp"],
                name="mtgs_dcor_mtg_time",
            ),
        ]

    def __str__(self) -> str:
        return f"Decision {self.decision_id} from meeting {self.meeting_id}"


class MeetingTaskOrigin(models.Model):
    """
    Provenance: a **task** generated from / anchored to a single meeting (one origin row).

    **Semantics**
    - ``task_id`` is unique (OneToOne): each task has at most one origin meeting.
    - ``(meeting_id, task_id)`` is unique at the DB level (explicit composite constraint).
    - Same-project alignment is enforced in application code when creating links via
      ``origin_meeting_id`` on task create.
    """

    meeting = models.ForeignKey(
        Meeting,
        on_delete=models.CASCADE,
        related_name="task_origins",
    )
    task = models.OneToOneField(
        "task.Task",
        on_delete=models.CASCADE,
        related_name="meeting_origin",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["meeting", "task"],
                name="mtgs_tkor_unique_meeting_task",
            ),
        ]
        indexes = [
            models.Index(
                fields=["meeting", "task"],
                name="mtgs_tkor_mtg_tsk",
            ),
        ]

    def __str__(self) -> str:
        return f"Task {self.task_id} from meeting {self.meeting_id}"


class MeetingActionItem(models.Model):
    """
    Captures a follow-up action from a meeting before it becomes an executable Task.

    Conversion to ``task.Task`` is one-to-one: each action item may produce at most one task
    (see ``Task.origin_action_item``).
    """

    meeting = models.ForeignKey(
        Meeting,
        on_delete=models.CASCADE,
        related_name="action_items",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    order_index = models.PositiveIntegerField(default=0)
    is_resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order_index", "id"]
        indexes = [
            models.Index(
                fields=["meeting", "order_index"],
                name="mtgs_actitem_meet_ord",
            ),
        ]

    def __str__(self) -> str:
        return f"ActionItem {self.pk} ({self.meeting_id}): {self.title[:40]}"


class ArtifactLink(models.Model):
    """
    ArtifactLink model represents a link between a meeting and an external artifact.
    """

    meeting = models.ForeignKey(
        Meeting,
        on_delete=models.CASCADE,
        related_name="artifact_links",
    )
    artifact_type = models.CharField(max_length=50)
    artifact_id = models.PositiveIntegerField()

    class Meta:
        unique_together = ("meeting", "artifact_type", "artifact_id")
        indexes = [
            models.Index(
                fields=["artifact_type", "artifact_id"],
                name="mtgs_artlink_type_artid",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"ArtifactLink type={self.artifact_type} "
            f"id={self.artifact_id} meeting={self.meeting_id}"
        )

def _meeting_template_id() -> str:
    # Use hex string UUIDs to keep URL-safe IDs.
    return uuid.uuid4().hex


class MeetingTemplate(models.Model):
    """
    MeetingTemplate stores reusable workspace templates (layout_config).
    layout_config is expected to be JSON-serializable (e.g. the frontend `blocks` structure).
    Do not reintroduce block_config - legacy DB columns are dropped via migration 0003.
    """

    id = models.CharField(primary_key=True, max_length=64, default=_meeting_template_id, editable=False)
    name = models.CharField(max_length=255)
    layout_config = models.JSONField(default=dict, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="meeting_templates",
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.name} ({self.id})"


class MeetingDocument(models.Model):
    """
    A single collaborative document attached to a meeting.
    """

    meeting = models.OneToOneField(
        Meeting,
        on_delete=models.CASCADE,
        related_name="document",
    )
    content = models.TextField(blank=True, default="")
    yjs_state = models.TextField(blank=True, default="")
    last_edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="edited_meeting_documents",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"MeetingDocument meeting={self.meeting_id}"


class MeetingAuditLog(models.Model):
    """
    Immutable append-only audit log for Meeting and direct-child mutations.
    Enforced immutable via Postgres BEFORE UPDATE OR DELETE trigger.
    """

    # Event type constants
    EVENT_STATUS_CHANGED = 'meeting.status_changed'
    EVENT_TITLE_CHANGED = 'meeting.title_changed'
    EVENT_OBJECTIVE_CHANGED = 'meeting.objective_changed'
    EVENT_SUMMARY_CHANGED = 'meeting.summary_changed'
    EVENT_TYPE_CHANGED = 'meeting.type_changed'
    EVENT_DATETIME_CHANGED = 'meeting.datetime_changed'
    EVENT_PARTICIPANT_ADDED = 'meeting.participant_added'
    EVENT_PARTICIPANT_REMOVED = 'meeting.participant_removed'
    EVENT_AGENDA_ITEM_ADDED = 'meeting.agenda_item_added'
    EVENT_AGENDA_ITEM_EDITED = 'meeting.agenda_item_edited'
    EVENT_AGENDA_ITEM_DELETED = 'meeting.agenda_item_deleted'
    EVENT_DOCUMENT_EDITED = 'meeting.document_edited'
    EVENT_ACTION_ITEM_ADDED = 'meeting.action_item_added'
    EVENT_ACTION_ITEM_EDITED = 'meeting.action_item_edited'
    EVENT_ACTION_ITEM_RESOLVED = 'meeting.action_item_resolved'
    EVENT_DECISION_CREATED = 'meeting.decision_created'
    EVENT_DECISION_UPDATED = 'meeting.decision_updated'
    EVENT_DECISION_DELETED = 'meeting.decision_deleted'
    EVENT_TASK_CREATED = 'meeting.task_created'
    EVENT_TASK_UPDATED = 'meeting.task_updated'
    EVENT_TASK_DELETED = 'meeting.task_deleted'
    EVENT_TEMPLATE_APPLIED = 'meeting.template_applied'
    EVENT_TAGS_CHANGED = 'meeting.tags_changed'

    EVENT_TYPE_CHOICES = [
        (EVENT_STATUS_CHANGED, 'Status Changed'),
        (EVENT_TITLE_CHANGED, 'Title Changed'),
        (EVENT_OBJECTIVE_CHANGED, 'Objective Changed'),
        (EVENT_SUMMARY_CHANGED, 'Summary Changed'),
        (EVENT_TYPE_CHANGED, 'Type Changed'),
        (EVENT_DATETIME_CHANGED, 'Date/Time Changed'),
        (EVENT_PARTICIPANT_ADDED, 'Participant Added'),
        (EVENT_PARTICIPANT_REMOVED, 'Participant Removed'),
        (EVENT_AGENDA_ITEM_ADDED, 'Agenda Item Added'),
        (EVENT_AGENDA_ITEM_EDITED, 'Agenda Item Edited'),
        (EVENT_AGENDA_ITEM_DELETED, 'Agenda Item Deleted'),
        (EVENT_DOCUMENT_EDITED, 'Document Edited'),
        (EVENT_ACTION_ITEM_ADDED, 'Action Item Added'),
        (EVENT_ACTION_ITEM_EDITED, 'Action Item Edited'),
        (EVENT_ACTION_ITEM_RESOLVED, 'Action Item Resolved'),
        (EVENT_DECISION_CREATED, 'Decision Created'),
        (EVENT_DECISION_UPDATED, 'Decision Updated'),
        (EVENT_DECISION_DELETED, 'Decision Deleted'),
        (EVENT_TASK_CREATED, 'Task Created'),
        (EVENT_TASK_UPDATED, 'Task Updated'),
        (EVENT_TASK_DELETED, 'Task Deleted'),
        (EVENT_TEMPLATE_APPLIED, 'Template Applied'),
        (EVENT_TAGS_CHANGED, 'Tags Changed'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    meeting = models.ForeignKey(
        Meeting,
        on_delete=models.CASCADE,
        related_name='audit_log',
        db_index=True,
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='meeting_audit_entries',
        db_index=True,
    )
    event_type = models.CharField(
        max_length=64,
        choices=EVENT_TYPE_CHOICES,
        db_index=True,
    )
    timestamp = models.DateTimeField(auto_now_add=True)
    before = models.JSONField(null=True, blank=True)
    after = models.JSONField(null=True, blank=True)
    context = models.JSONField(null=True, blank=True, default=dict)

    class Meta:
        indexes = [
            models.Index(fields=['meeting', '-timestamp'], name='audit_mtg_ts_idx'),
            models.Index(fields=['meeting', 'event_type', '-timestamp'], name='audit_mtg_type_ts_idx'),
            models.Index(fields=['meeting', 'actor', '-timestamp'], name='audit_mtg_actor_ts_idx'),
        ]
        constraints = [
            models.CheckConstraint(check=models.Q(id__isnull=False), name='audit_log_has_id'),
        ]
        ordering = ['-timestamp']
        verbose_name = 'Meeting Audit Log'
        verbose_name_plural = 'Meeting Audit Logs'

    def __str__(self):
        actor_name = self.actor.display_name if self.actor else 'System'
        return f"AuditLog({self.meeting_id}, {self.event_type}, {actor_name}, {self.timestamp})"
