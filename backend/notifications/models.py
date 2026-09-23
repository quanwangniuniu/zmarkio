import uuid

from django.conf import settings
from django.db import models


class NotificationCategory(models.TextChoices):
    COLLABORATION = "COLLABORATION", "Collaboration"
    MEETINGS = "MEETINGS", "Meetings"
    TASKS = "TASKS", "Tasks"
    DECISIONS = "DECISIONS", "Decisions"
    APPROVAL = "APPROVAL", "Approval"
    SYSTEM = "SYSTEM", "System"
    AUTOMATION = "AUTOMATION", "Automation"
    INTEGRATIONS = "INTEGRATIONS", "Integrations"


class NotificationEventType(models.TextChoices):
    # Collaboration & assets
    CHAT_NEW_MESSAGE = "chat_new_message", "Chat new message"
    CHAT_NEW_CONVERSATION = "chat_new_conversation", "Chat new conversation"
    MESSAGE_REMINDER = "message_reminder", "Message reminder"
    PROJECT_INVITE = "project_invite", "Project invitation"
    CALENDAR_REMINDER = "calendar_reminder", "Calendar reminder"
    DOC_ASSET_UPDATE = "doc_asset_update", "Document or asset update"
    # Meetings
    MEETING_CREATED = "meeting_created", "Meeting created"
    MEETING_UPDATED = "meeting_updated", "Meeting updated"
    MEETING_PARTICIPANT_ADDED = "meeting_participant_added", "Added to meeting"
    MEETING_PARTICIPANT_REMOVED = "meeting_participant_removed", "Removed from meeting"
    MEETING_STARTING_SOON = "meeting_starting_soon", "Meeting starting soon"
    MEETING_AGENDA_CHANGED = "meeting_agenda_changed", "Agenda changed"
    MEETING_MINUTES_PUBLISHED = "meeting_minutes_published", "Minutes published"
    # Tasks
    TASK_ASSIGNED = "task_assigned", "Task assigned"
    TASK_OWNER_CHANGED = "task_owner_changed", "Task owner changed"
    TASK_STATUS_CHANGED = "task_status_changed", "Task status changed"
    TASK_DEADLINE_SOON = "task_deadline_soon", "Deadline approaching"
    TASK_OVERDUE = "task_overdue", "Task overdue"
    TASK_COMMENT_MENTION = "task_comment_mention", "Mentioned in comment"
    CHAT_MENTION = "chat_mention", "Mentioned in chat"
    CHAT_THREAD_REPLY = "chat_thread_reply", "Reply in thread"
    CHAT_REACTION = "chat_reaction", "Reaction to your message"
    TASK_ANOMALY = "task_anomaly", "Task anomaly alert"
    # Decisions
    DECISION_REVIEW_NEEDED = "decision_review_needed", "Decision needs review"
    DECISION_DEADLINE = "decision_deadline", "Decision deadline"
    DECISION_PUBLISHED = "decision_published", "Decision published"
    # Budget
    BUDGET_REVIEW_NEEDED = "budget_review_needed", "Budget needs review"
    BUDGET_APPROVAL_RESULT = "budget_approval_result", "Budget approval result"
    BUDGET_POOL_LOW = "budget_pool_low", "Budget pool insufficient"
    BUDGET_ESCALATION = "budget_escalation", "Budget escalation"
    # Organization
    ORG_INVITE = "org_invite", "Organization invitation"
    # Automation & system
    WORKFLOW_NODE = "workflow_node", "Workflow node update"
    ACCOUNT_PERMISSION = "account_permission", "Account permission changed"
    BILLING_ANOMALY = "billing_anomaly", "Billing anomaly"
    SYSTEM = "system", "System"


MENTION_TAB_EVENT_TYPES = frozenset(
    {
        NotificationEventType.TASK_COMMENT_MENTION,
        NotificationEventType.CHAT_MENTION,
    }
)

DEADLINE_TAB_EVENT_TYPES = frozenset(
    {
        NotificationEventType.TASK_DEADLINE_SOON,
        NotificationEventType.TASK_OVERDUE,
        NotificationEventType.MEETING_STARTING_SOON,
        NotificationEventType.DECISION_DEADLINE,
    }
)


def _pref_row():
    return {"web": True, "email": True}


def default_notification_preferences() -> dict:
    """
    Four top-level groups (Web + Email per row). Keys must stay in sync with the frontend.
    """
    r = _pref_row
    return {
        "disable_all": False,
        "collaboration_assets": {
            "chat_in_app": r(),
            "chat_new_session": r(),
            "message_reminder": r(),
            "project_invite": r(),
            "org_invite": r(),
            "calendar_reminders": r(),
            "doc_asset_updates": r(),
        },
        "core_business": {
            "meeting_created_updated": r(),
            "meeting_participant_assign": r(),
            "meeting_starting_soon": r(),
            "meeting_agenda_minutes": r(),
            "task_assignee": r(),
            "task_mention": r(),
            "task_deadline": r(),
            "task_status": r(),
            "task_alert": r(),
            "decision_review": r(),
            "decision_deadline": r(),
            "decision_published": r(),
            "budget_review": r(),
            "budget_pool_low": r(),
            "budget_escalation": r(),
        },
        "automation_system": {
            "approval_budget_member": r(),
            "workflow_nodes": r(),
            "account_security_billing": r(),
        },
        "integrations": {
            "slack_webhook": r(),
        },
    }


class Notification(models.Model):
    """
    In-app notification for a single user.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications_triggered",
        help_text="User who triggered the event; used to suppress self-notifications.",
    )
    category = models.CharField(
        max_length=32,
        choices=NotificationCategory.choices,
        db_index=True,
    )
    event_type = models.CharField(
        max_length=64,
        choices=NotificationEventType.choices,
        db_index=True,
    )
    related_object_type = models.CharField(max_length=64, blank=True, default="")
    related_object_id = models.CharField(max_length=64, blank=True, default="")
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True, default="")
    is_read = models.BooleanField(default=False, db_index=True)
    action_url = models.CharField(max_length=1024, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    # Tracks whether the recipient has explicitly accepted or declined an actionable notification.
    responded = models.BooleanField(default=False, db_index=True)
    response = models.CharField(
        max_length=16,
        blank=True,
        default="",
        choices=[("accept", "Accepted"), ("reject", "Rejected")],
    )

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("recipient", "-created_at")),
            models.Index(fields=("recipient", "is_read")),
        ]

    def __str__(self) -> str:
        return f"{self.title} → {self.recipient_id}"


class UserNotificationPreference(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_preference",
    )
    preferences = models.JSONField(default=default_notification_preferences)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_notification_preferences"

    def __str__(self) -> str:
        return f"NotificationPreference({self.user_id})"
