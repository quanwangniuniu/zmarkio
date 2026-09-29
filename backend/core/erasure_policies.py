"""Central, reviewable GDPR erasure policy for every direct user relation.

The policy is grouped by Django app while remaining field-specific: a model can
contain collaborative authorship, private state, and legally retained fields at
the same time. Tests fail when an installed model adds a user relation without
adding a decision here.
"""

from dataclasses import dataclass
from enum import Enum


class ErasureAction(str, Enum):
    DELETE = "delete"
    ANONYMIZE = "anonymize"
    RETAIN = "retain"
    UNLINK = "unlink"


@dataclass(frozen=True)
class FieldPolicy:
    field_name: str
    action: ErasureAction


@dataclass(frozen=True)
class ModelPolicy:
    model_label: str
    fields: tuple[FieldPolicy, ...]


def _model(model_label, *, delete=(), anonymize=(), retain=(), unlink=()):
    fields = tuple(
        FieldPolicy(field_name, action)
        for action, field_names in (
            (ErasureAction.DELETE, delete),
            (ErasureAction.ANONYMIZE, anonymize),
            (ErasureAction.RETAIN, retain),
            (ErasureAction.UNLINK, unlink),
        )
        for field_name in field_names
    )
    return ModelPolicy(model_label, fields)


# DELETE removes user-private state. ANONYMIZE preserves collaborative content
# linked to the account tombstone. RETAIN preserves legal/audit/billing records.
# UNLINK removes only the named nullable/M2M relationship, not the record.
ERASURE_POLICIES = {
    "admin": (
        _model("admin.LogEntry", retain=("user",)),
    ),
    "access_control": (
        _model("access_control.UserRole", delete=("user",)),
        _model("access_control.AdminOverrideAudit", retain=("user",)),
        _model("access_control.ModuleApprover", delete=("user",)),
    ),
    "ad_copy_variation": (
        _model("ad_copy_variation.AdCopyVariation", anonymize=("created_by",)),
    ),
    "agent": (
        _model("agent.AgentSession", delete=("user",)),
        _model("agent.ImportedCSVFile", delete=("user",)),
        _model("agent.DataSchemaTemplate", anonymize=("created_by",)),
        _model("agent.AgentWorkflowDefinition", anonymize=("created_by",)),
        _model("agent.AgentWorkflowTemplate", anonymize=("created_by",)),
    ),
    "alerting": (
        _model("alerting.AlertTask", anonymize=("acknowledged_by", "assigned_to")),
    ),
    "asset": (
        _model("asset.Asset", anonymize=("owner",)),
        _model("asset.AssetStateTransition", anonymize=("triggered_by",)),
        _model("asset.AssetVersion", anonymize=("uploaded_by",)),
        _model("asset.AssetVersionStateTransition", anonymize=("triggered_by",)),
        _model("asset.AssetComment", anonymize=("user",)),
        _model("asset.ReviewAssignment", anonymize=("user", "assigned_by")),
    ),
    "audit": (
        _model("audit.AdminAuditEvent", retain=("actor",)),
    ),
    "behavioral_tracking": (
        _model("behavioral_tracking.FocusSession", delete=("user",)),
    ),
    "budget_approval": (
        _model("budget_approval.BudgetRequest", retain=("requested_by", "current_approver")),
    ),
    "calendars": (
        _model("calendars.Calendar", anonymize=("owner",)),
        _model("calendars.CalendarSubscription", delete=("user",)),
        _model("calendars.CalendarShare", delete=("shared_with",)),
        _model("calendars.Event", anonymize=("created_by",)),
        _model("calendars.EventAttendee", delete=("user",)),
        _model("calendars.EventReminder", delete=("user",)),
        _model("calendars.EventCategory", delete=("user",)),
        _model("calendars.CalendarSettings", delete=("user",)),
        _model("calendars.Notification", delete=("user",)),
        _model(
            "calendars.BookingLink",
            anonymize=("owner", "created_by"),
            unlink=("invitee_users",),
        ),
    ),
    "campaign": (
        _model("campaign.Campaign", anonymize=("owner", "creator", "assignee")),
        _model("campaign.CampaignStatusHistory", anonymize=("changed_by",)),
        _model("campaign.PerformanceCheckIn", anonymize=("checked_by",)),
        _model("campaign.PerformanceSnapshot", anonymize=("snapshot_by",)),
        _model("campaign.CampaignNotificationPreference", delete=("user",)),
        _model("campaign.CampaignTemplate", anonymize=("creator",)),
        _model("campaign.CampaignAttachment", anonymize=("uploaded_by",)),
    ),
    "chat": (
        _model("chat.Chat", anonymize=("created_by",)),
        _model("chat.ChatStar", delete=("user",)),
        _model("chat.ChatParticipant", delete=("user",)),
        _model(
            "chat.Message",
            anonymize=("sender",),
            unlink=("hidden_by_users", "link_preview_hidden_by"),
        ),
        _model("chat.MessageStatus", delete=("user",)),
        _model("chat.MessageMention", anonymize=("mentioned_user",)),
        _model("chat.MessageReaction", delete=("user",)),
        _model("chat.ThreadReadStatus", delete=("user",)),
        _model("chat.MessageAttachment", anonymize=("uploader",)),
        _model("chat.PinnedMessage", anonymize=("pinned_by",)),
        _model("chat.SavedMessage", delete=("user",)),
        _model("chat.MessageReminder", delete=("user",)),
        _model("chat.ScheduledMessage", delete=("sender",)),
    ),
    "comments": (
        _model("comments.Comment", anonymize=("author", "deleted_by")),
        _model("comments.CommentMention", anonymize=("mentioned_user",)),
        _model("comments.CommentAttachment", anonymize=("uploaded_by",)),
    ),
    "core": (
        _model("core.TeamMember", delete=("user",)),
        _model("core.Project", anonymize=("owner",)),
        _model("core.ProjectMember", delete=("user",)),
        _model("core.ProjectInvitation", anonymize=("approved_by", "invited_by")),
        _model("core.DataExportRequest", delete=("user",)),
        _model("core.AuditEvent", retain=("actor",)),
        _model(
            "core.OrganizationMembership",
            delete=("user",),
            anonymize=("invited_by",),
        ),
        _model("core.OrganizationInvitation", anonymize=("invited_by",)),
        _model("core.OrganizationInvitationUse", anonymize=("user",)),
        _model(
            "core.OrganizationActivityEvent",
            retain=("actor", "target_user"),
        ),
    ),
    "csm": (
        _model("csm.QueueAgent", delete=("user",), anonymize=("assigned_by",)),
        _model("csm.CustomerUser", delete=("user",)),
        _model("csm.CsmNotification", delete=("recipient",), anonymize=("sender",)),
        _model("csm.Ticket", anonymize=("assigned_to",)),
        _model("csm.QuickReplyTemplate", anonymize=("created_by",)),
        _model("csm.QuickReplyTemplateHistory", anonymize=("edited_by",)),
        _model("csm.TicketForm", anonymize=("created_by",)),
        _model("csm.TicketFormSubmission", anonymize=("submitted_by",)),
        _model("csm.CSMInvitation", anonymize=("invited_by",)),
    ),
    "customer": (
        _model("customer.Customer", anonymize=("user",)),
        _model("customer.CustomerInternalNote", anonymize=("author",)),
        _model("customer.CustomerInternalNoteAuditLog", retain=("actor",)),
    ),
    "decision": (
        _model(
            "decision.Decision",
            anonymize=("author", "last_edited_by", "committed_by", "approved_by"),
        ),
        _model("decision.DecisionEdge", anonymize=("created_by",)),
        _model("decision.Signal", anonymize=("author",)),
        _model("decision.Review", anonymize=("reviewer",)),
        _model("decision.DecisionStateTransition", anonymize=("triggered_by",)),
        _model("decision.CommitRecord", anonymize=("committed_by",)),
    ),
    "experience_group": (
        _model("experience_group.ExperienceGroup", anonymize=("created_by",)),
    ),
    "experiment": (
        _model("experiment.Experiment", anonymize=("created_by",)),
        _model("experiment.ExperimentProgressUpdate", anonymize=("created_by",)),
    ),
    "facebook_integration": (
        _model("facebook_integration.FacebookConnection", delete=("user",)),
    ),
    "facebook_meta": (
        _model("facebook_meta.AdCreativePhotoData", anonymize=("uploaded_by",)),
        _model("facebook_meta.AdCreativeVideoData", anonymize=("uploaded_by",)),
        _model("facebook_meta.AdCreative", anonymize=("actor",)),
    ),
    "google_ads": (
        _model("google_ads.CustomerAccount", anonymize=("created_by",)),
        _model("google_ads.AdPreview", anonymize=("created_by",)),
        _model("google_ads.Ad", anonymize=("created_by",)),
    ),
    "google_calendar_integration": (
        _model("google_calendar_integration.GoogleCalendarConnection", delete=("user",)),
    ),
    "google_docs_integration": (
        _model("google_docs_integration.GoogleDocsConnection", delete=("user",)),
    ),
    "klaviyo": (
        _model("klaviyo.EmailDraft", anonymize=("user",)),
        _model("klaviyo.KlaviyoImage", anonymize=("uploaded_by",)),
    ),
    "linear_integration": (
        _model("linear_integration.LinearCredential", delete=("user",)),
    ),
    "mailchimp": (
        _model("mailchimp.Campaign", anonymize=("user",)),
        _model("mailchimp.Template", anonymize=("user",)),
        _model("mailchimp.CampaignComment", anonymize=("author", "resolved_by")),
    ),
    "meetings": (
        _model("meetings.ParticipantLink", anonymize=("user",)),
        _model("meetings.MeetingDecisionOrigin", anonymize=("created_by",)),
        _model("meetings.MeetingTemplate", anonymize=("user",)),
        _model("meetings.MeetingDocument", anonymize=("last_edited_by",)),
        _model("meetings.MeetingAuditLog", retain=("actor",)),
    ),
    "metric_upload": (
        _model("metric_upload.MetricFile", anonymize=("uploaded_by",)),
    ),
    "miro": (
        _model("miro.BoardAccess", delete=("user",)),
    ),
    "notifications": (
        _model(
            "notifications.Notification",
            delete=("recipient",),
            anonymize=("actor",),
        ),
        _model("notifications.UserNotificationPreference", delete=("user",)),
    ),
    "notion_editor": (
        _model("notion_editor.Draft", anonymize=("user",)),
        _model("notion_editor.DraftRevision", anonymize=("created_by",)),
        _model("notion_editor.MediaFile", anonymize=("uploaded_by",)),
        _model("notion_editor.NotionConnection", delete=("user",)),
    ),
    "optimization": (
        _model("optimization.OptimizationExperiment", anonymize=("created_by",)),
        _model("optimization.ScalingAction", anonymize=("performed_by",)),
        _model("optimization.RollbackHistory", anonymize=("performed_by",)),
    ),
    "policy": (
        _model(
            "policy.PlatformPolicyUpdate",
            retain=("reviewed_by", "created_by", "assigned_to"),
        ),
    ),
    "retrospective": (
        _model("retrospective.RetrospectiveTask", anonymize=("reviewed_by", "created_by")),
        _model("retrospective.Insight", anonymize=("created_by",)),
    ),
    "spreadsheet": (
        _model("spreadsheet.SheetStructureOperation", anonymize=("created_by",)),
        _model("spreadsheet.WorkflowPattern", anonymize=("owner",)),
        _model("spreadsheet.PatternJob", anonymize=("created_by",)),
        _model("spreadsheet.SpreadsheetAiConsent", retain=("user",)),
    ),
    "stripe_meta": (
        _model("stripe_meta.UsageDaily", retain=("user",)),
        _model("stripe_meta.Payment", retain=("user",)),
        _model("stripe_meta.LLMCallLog", retain=("user",)),
    ),
    "task": (
        _model("task.Task", anonymize=("owner", "current_approver", "created_by")),
        _model("task.TaskPin", delete=("user",)),
        _model("task.ApprovalRecord", retain=("approved_by",)),
        _model("task.ApprovalChainStep", retain=("approver",)),
        _model("task.TaskComment", anonymize=("user",)),
        _model("task.TaskAttachment", anonymize=("uploaded_by",)),
        _model("task.TaskFieldHistory", anonymize=("changed_by",)),
    ),
    "tiktok": (
        _model("tiktok.TikTokCreative", anonymize=("uploaded_by",)),
        _model("tiktok.AdGroup", anonymize=("created_by",)),
        _model("tiktok.AdDraft", anonymize=("created_by",)),
    ),
    "tracking": (
        _model("tracking.TrackingSession", delete=("user",)),
        _model("tracking.TrackingEvent", delete=("user",)),
    ),
    "user_preferences": (
        _model("user_preferences.UserPreferences", delete=("user",)),
        _model("user_preferences.NotificationSettings", delete=("user",)),
        _model("user_preferences.SlackIntegration", delete=("user",)),
    ),
    "workflows": (
        _model("workflows.Workflow", anonymize=("created_by",)),
        _model("workflows.WorkflowVersion", anonymize=("created_by",)),
    ),
    "zoom_integration": (
        _model("zoom_integration.ZoomCredential", delete=("user",)),
        _model("zoom_integration.ZoomMeetingData", anonymize=("zoom_host_user",)),
    ),
}


def iter_model_policies():
    for policies in ERASURE_POLICIES.values():
        yield from policies


def policy_field_map():
    return {
        (policy.model_label.lower(), field.field_name): field.action
        for policy in iter_model_policies()
        for field in policy.fields
    }
